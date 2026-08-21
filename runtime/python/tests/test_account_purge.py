import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[1]
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

from mia_account_purge import purge_account_data, scrub_account_log_lines
from mia_storage import Storage, StorageError


class AccountPurgeTests(unittest.TestCase):
    def test_purge_removes_legacy_jobs_production_state_source_data_and_attributable_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root / "mia.sqlite3")
            storage.initialize()
            account_id = "account-delete-123"
            tax_code = "0101234567"
            storage.create_account({
                "account_id": account_id,
                "tax_code": tax_code,
                "encrypted_password": b"cipher",
                "timestamp": "2026-08-21T00:00:00+00:00",
            })
            storage.create_job({
                "job_id": "job_legacy_delete",
                "connection_id": account_id,
                "idempotency_key": "legacy-delete",
                "intent": {"date_from": "2026-08-01", "date_to": "2026-08-21"},
                "timestamp": "2026-08-21T00:00:00+00:00",
            })
            artifact = root / "artifacts" / "delete-me.xml"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("payload", encoding="utf-8")
            # sqlite3.Connection's context manager only commits/rolls back; it
            # does not close the native handle. Explicitly close it so Windows
            # can delete TemporaryDirectory files after the test.
            with closing(sqlite3.connect(root / "mia.sqlite3")) as connection:
                connection.execute(
                    """INSERT INTO artifacts(
                        artifact_id, job_id, account_id, artifact_type,
                        relative_path, status, created_at
                    ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        "artifact-delete", "job_legacy_delete", account_id, "xml",
                        "artifacts/delete-me.xml", "ready", "2026-08-21T00:00:00+00:00",
                    ),
                )
                connection.commit()

            with closing(sqlite3.connect(root / "source-control.sqlite3")) as control:
                control.executescript("""
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE source_accounts(
                        account_id TEXT PRIMARY KEY, account_key TEXT UNIQUE,
                        created_at TEXT, updated_at TEXT
                    );
                    CREATE TABLE internal_sessions(
                        session_hash TEXT PRIMARY KEY,
                        account_id TEXT REFERENCES source_accounts(account_id),
                        owner_id TEXT
                    );
                    CREATE TABLE account_connections(
                        connection_id TEXT PRIMARY KEY,
                        owner_id TEXT, username TEXT, session_hash TEXT UNIQUE
                            REFERENCES internal_sessions(session_hash)
                    );
                    CREATE TABLE crawl_jobs(
                        job_id TEXT PRIMARY KEY,
                        account_key TEXT, company_tax_code TEXT
                    );
                """)
                control.execute(
                    "INSERT INTO source_accounts VALUES (?,?,?,?)",
                    ("source-account", tax_code, "now", "now"),
                )
                control.execute(
                    "INSERT INTO internal_sessions VALUES (?,?,?)",
                    ("a" * 64, "source-account", "mia-desktop-local"),
                )
                control.execute(
                    "INSERT INTO account_connections VALUES (?,?,?,?)",
                    ("conn-production", "mia-desktop-local", tax_code, "a" * 64),
                )
                control.execute(
                    "INSERT INTO crawl_jobs VALUES (?,?,?)",
                    ("job-production-delete", account_id, tax_code),
                )
                control.commit()

            source_file = root / "source-data" / tax_code / "db" / "invoices.sqlite3"
            source_file.parent.mkdir(parents=True)
            source_file.write_bytes(b"db")

            log_dir = root / "logs"
            log_dir.mkdir()
            runtime_log = log_dir / "runtime.log"
            runtime_log.write_text(
                f"job {account_id} job-production-delete failed\nnormal event\n",
                encoding="utf-8",
            )

            result = purge_account_data(root, account_id, tax_code)
            removed = scrub_account_log_lines(
                root, [account_id, tax_code, *result["job_ids"]]
            )

            with self.assertRaises(StorageError):
                storage.get_account(account_id)
            self.assertFalse(artifact.exists())
            self.assertFalse((root / "source-data" / tax_code).exists())
            with closing(sqlite3.connect(root / "source-control.sqlite3")) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM crawl_jobs").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM account_connections").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM internal_sessions").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_accounts").fetchone()[0], 0)
            self.assertGreaterEqual(removed, 1)
            self.assertEqual(runtime_log.read_text(encoding="utf-8"), "normal event\n")


if __name__ == "__main__":
    unittest.main()
