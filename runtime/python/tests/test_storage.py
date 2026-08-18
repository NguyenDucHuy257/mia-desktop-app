import logging
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mia_logging import configure_logging
from mia_storage import SCHEMA_VERSION, Storage, StorageError


class StorageTests(unittest.TestCase):
    def test_creates_and_reopens_current_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "mia.sqlite3")
            self.assertEqual(storage.initialize(), {"schema_version": SCHEMA_VERSION, "integrity": "ok"})
            self.assertEqual(storage.initialize(), {"schema_version": SCHEMA_VERSION, "integrity": "ok"})
            self.assertEqual(storage.status()["integrity"], "ok")

    def test_migrates_an_existing_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mia.sqlite3"
            sqlite3.connect(database).close()
            self.assertEqual(Storage(database).initialize()["schema_version"], SCHEMA_VERSION)

    def test_reports_locked_and_corrupt_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mia.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("BEGIN EXCLUSIVE")
            with self.assertRaisesRegex(StorageError, "database_locked"):
                Storage(database).initialize()
            connection.rollback()
            connection.close()
            database.write_bytes(b"not a sqlite database")
            with self.assertRaisesRegex(StorageError, "database_corrupt"):
                Storage(database).initialize()

    def test_rolls_back_interrupted_transactions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mia.sqlite3"
            Storage(database).initialize()
            connection = sqlite3.connect(database)
            connection.execute("BEGIN")
            connection.execute(
                "INSERT INTO accounts(account_id, normalized_tax_code, encrypted_password, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("account", "tax-code", b"encrypted", "unchecked", "now", "now"),
            )
            connection.close()
            with closing(sqlite3.connect(database)) as reopened:
                self.assertEqual(reopened.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)

    def test_redacts_credentials_and_tax_codes_from_rotating_log(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = configure_logging(Path(directory), "INFO")
            logger.info("password=unsafe token=unsafe 0100000000-001")
            for handler in logger.handlers:
                handler.flush()
            content = (Path(directory) / "runtime.log").read_text(encoding="utf-8")
            self.assertNotIn("unsafe", content)
            self.assertNotIn("0100000000-001", content)
            self.assertGreaterEqual(content.count("[REDACTED]"), 2)
            logging.shutdown()

    def test_account_crud_never_returns_encrypted_password(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "mia.sqlite3")
            storage.initialize()
            created = storage.create_account({
                "account_id": "account-1", "tax_code": "0101234567",
                "encrypted_password": "ciphertext", "timestamp": "2026-08-18T00:00:00Z",
            })
            self.assertEqual(created["status"], "unchecked")
            self.assertNotIn("encrypted_password", created)
            reused = storage.create_account({
                "account_id": "account-2", "tax_code": "0101234567",
                "encrypted_password": "different", "timestamp": "later",
            })
            self.assertTrue(reused["reused"])
            updated = storage.update_account({
                "account_id": "account-1", "tax_code": "0101234567",
                "encrypted_password": "new-ciphertext", "timestamp": "later",
            })
            self.assertEqual(updated["status"], "unchecked")
            self.assertEqual(len(storage.list_accounts()), 1)
            storage.delete_account("account-1")
            self.assertEqual(storage.list_accounts(), [])

    def test_job_idempotency_resume_cancel_and_event_order_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mia.sqlite3"
            storage = Storage(database)
            storage.initialize()
            storage.create_account({
                "account_id": "account-1", "tax_code": "0101234567",
                "encrypted_password": "ciphertext", "timestamp": "2026-08-18T00:00:00Z",
            })
            value = {
                "job_id": "job_1", "connection_id": "account-1", "idempotency_key": "desktop-key",
                "intent": {"directions": ["purchase"]}, "timestamp": "2026-08-18T00:01:00Z",
            }
            created = storage.create_job(value)
            duplicate = storage.create_job({**value, "job_id": "job_2"})
            self.assertEqual(created["job_id"], duplicate["job_id"])
            self.assertTrue(duplicate["reused"])
            reopened = Storage(database)
            self.assertEqual(reopened.resume_job()["job_id"], "job_1")
            cancelled = reopened.cancel_job("job_1", "2026-08-18T00:02:00Z")
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(cancelled["event_sequence"], 2)
            self.assertIsNone(reopened.resume_job())
            summary = reopened.job_summary("job_1")
            self.assertEqual([event["type"] for event in summary["events"]], ["queued", "cancelled"])

    def test_running_cancel_is_bounded_and_terminal_cancel_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mia.sqlite3"
            storage = Storage(database)
            storage.initialize()
            storage.create_account({
                "account_id": "account-1", "tax_code": "0101234567",
                "encrypted_password": "ciphertext", "timestamp": "now",
            })
            storage.create_job({
                "job_id": "job_1", "connection_id": "account-1", "idempotency_key": "desktop-key",
                "intent": {}, "timestamp": "2026-08-18T00:00:00Z",
            })
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("UPDATE jobs SET status='running', stage='running' WHERE job_id='job_1'")
                connection.commit()
            first = storage.cancel_job("job_1", "2026-08-18T00:01:00Z")
            second = storage.cancel_job("job_1", "2026-08-18T00:02:00Z")
            self.assertEqual(first["status"], "cancelling")
            self.assertEqual(second["event_sequence"], first["event_sequence"])

    def test_worker_transitions_clamp_progress_and_reject_stale_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "mia.sqlite3")
            storage.initialize()
            storage.create_account({
                "account_id": "account-1", "tax_code": "0101234567",
                "encrypted_password": "ciphertext", "timestamp": "now",
            })
            storage.create_job({
                "job_id": "job_1", "connection_id": "account-1", "idempotency_key": "desktop-key",
                "intent": {}, "timestamp": "2026-08-18T00:00:00Z",
            })
            waiting = storage.transition_job({
                "job_id": "job_1", "expected_sequence": 1, "status": "waiting_account",
                "stage": "login", "overall_percent": -10, "timestamp": "2026-08-18T00:01:00Z",
            })
            running = storage.transition_job({
                "job_id": "job_1", "expected_sequence": 2, "status": "running", "stage": "overview",
                "overall_percent": 140, "current_month": {"key": "2026-08", "percent": 125},
                "timestamp": "2026-08-18T00:02:00Z",
            })
            self.assertEqual(waiting["overall_percent"], 0)
            self.assertEqual(running["overall_percent"], 100)
            self.assertEqual(running["current_month"]["percent"], 100)
            with self.assertRaisesRegex(StorageError, "stale_job_update"):
                storage.transition_job({
                    "job_id": "job_1", "expected_sequence": 2, "status": "completed",
                    "timestamp": "2026-08-18T00:03:00Z",
                })
            completed = storage.transition_job({
                "job_id": "job_1", "expected_sequence": 3, "status": "completed",
                "stage": "done", "overall_percent": 100, "timestamp": "2026-08-18T00:04:00Z",
            })
            unchanged = storage.cancel_job("job_1", "2026-08-18T00:05:00Z")
            self.assertEqual(unchanged["status"], "completed")
            self.assertEqual(unchanged["event_sequence"], completed["event_sequence"])

    def test_overview_cursor_reads_all_421_without_duplicate_or_missing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "mia.sqlite3")
            storage.initialize()
            storage.create_account({"account_id": "account-1", "tax_code": "0101234567", "encrypted_password": "ciphertext", "timestamp": "now"})
            items = [{
                "direction": "purchase" if index % 2 else "sold",
                "business_key": f"invoice-{index:04d}",
                "payload": {"invoice_number": f"HD-{index:04d}", "company": f"Company {index}"},
            } for index in range(421)]
            storage.import_overviews({"connection_id": "account-1", "items": items, "timestamp": "now"})
            storage.import_overviews({"connection_id": "account-1", "items": items, "timestamp": "later"})
            found, cursor = [], None
            while True:
                page = storage.query_overviews({"connection_id": "account-1", "limit": 37, "cursor": cursor})
                found.extend(item["business_key"] for item in page["items"])
                cursor = page["pagination"]["next_cursor"]
                if not page["pagination"]["has_more"]:
                    break
            self.assertEqual(len(found), 421)
            self.assertEqual(len(set(found)), 421)
            self.assertEqual(storage.query_overviews({"connection_id": "account-1", "direction": "purchase", "limit": 200})["items"][0]["direction"], "purchase")
            self.assertEqual(len(storage.query_overviews({"connection_id": "account-1", "search": "HD-0420", "limit": 10})["items"]), 1)
            with self.assertRaisesRegex(StorageError, "invalid_result_cursor"):
                storage.query_overviews({"connection_id": "account-1", "cursor": "broken", "limit": 10})
            details = [{
                "direction": item["direction"], "business_key": item["business_key"],
                "line_key": "line-1", "payload": {"product": f"Product {index}", "amount": index},
            } for index, item in enumerate(items)]
            storage.import_details({"connection_id": "account-1", "items": details})
            storage.import_details({"connection_id": "account-1", "items": details})
            detail_keys, cursor = [], None
            while True:
                page = storage.query_details({"connection_id": "account-1", "limit": 41, "cursor": cursor})
                detail_keys.extend((item["business_key"], item["line_key"]) for item in page["items"])
                cursor = page["pagination"]["next_cursor"]
                if not page["pagination"]["has_more"]:
                    break
            self.assertEqual(len(detail_keys), 421)
            self.assertEqual(len(set(detail_keys)), 421)


if __name__ == "__main__":
    unittest.main()
