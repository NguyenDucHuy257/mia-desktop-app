import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from mia_backend import ProductionBackend
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository


class SyncStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.backend = object.__new__(ProductionBackend)
        self.backend.data_root = Path(self.temporary.name) / "source-data"
        database = self.backend.data_root / "0100000000" / "db" / "invoices.sqlite3"
        database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript("""
                CREATE TABLE invoice_overview_items (
                    id INTEGER PRIMARY KEY, company_tax_code TEXT, direction TEXT,
                    query_type TEXT, created_at TEXT
                );
                CREATE TABLE invoice_overview_checkpoints (
                    company_tax_code TEXT, direction TEXT, from_date TEXT,
                    to_date TEXT, checkpoint_status TEXT
                );
            """)
            connection.executemany(
                "INSERT INTO invoice_overview_items VALUES (?,?,?,?,?)",
                [
                    (1, "0100000000", "purchase", "query", "2025-01-01T00:00:00+00:00"),
                    (2, "0100000000", "purchase", "query", "2026-08-01T00:00:01+00:00"),
                    (3, "0100000000", "sold", "query", "2025-01-01T00:00:00+00:00"),
                ],
            )
            connection.execute(
                "INSERT INTO invoice_overview_checkpoints VALUES (?,?,?,?,?)",
                ("0100000000", "purchase", "2025-01-01", "2025-08-31", "finalized"),
            )
            connection.commit()
        self.backend.service = Mock()
        self.backend.service.get_account_connection.return_value = SimpleNamespace(username="0100000000")
        self.backend.repository = Mock()

    def tearDown(self):
        self.temporary.cleanup()

    def test_completed_overview_is_the_business_boundary_while_detail_runs(self):
        job = SimpleNamespace(
            job_id="job_purchase", status="running", current_stage="detail",
            created_at="2026-08-01T00:00:00+00:00", finished_at=None,
            parameters={
                "directions": ["purchase"], "sync_mode": "supplement",
                "baseline_invoice_count": 1, "date_from": "2025-05-01",
                "date_to": "2025-08-16",
            },
            progress_state={
                "current_stage": "detail",
                "current_month": {"key": "2025-08"},
                "modules": {"overview": {"status": "completed"}, "detail": {"status": "running"}},
            },
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = job
        state = self.backend.sync_states(["conn_one"], "purchase")[0]
        self.assertEqual(state["status"], "completed")
        self.assertIsNone(state["current_month"])
        self.assertIsNone(state["current_until"])
        self.assertEqual(state["invoice_count"], 2)
        self.assertEqual(state["baseline_invoice_count"], 1)
        self.assertEqual(state["added_invoice_count"], 1)
        self.assertEqual((state["sync_from"], state["sync_until"]), ("2025-05-01", "2025-08-16"))

    def test_direction_counts_are_independent(self):
        self.backend.repository.latest_invoice_job_for_direction.return_value = None
        purchase = self.backend.sync_states(["conn_one"], "purchase")[0]
        sold = self.backend.sync_states(["conn_one"], "sold")[0]
        self.assertEqual(purchase["invoice_count"], 2)
        self.assertEqual(sold["invoice_count"], 1)
        self.assertEqual(purchase["status"], "completed")
        self.assertEqual(sold["status"], "completed")

    def test_running_overview_exposes_real_current_month(self):
        job = SimpleNamespace(
            job_id="job_running", status="running", current_stage="overview",
            created_at="2026-08-01T00:00:00+00:00", finished_at=None,
            parameters={
                "directions": ["purchase"], "sync_mode": "new",
                "baseline_invoice_count": 1, "date_to": "2025-08-16",
            },
            progress_state={
                "current_stage": "overview", "current_month": {"key": "2025-05"},
                "modules": {"overview": {"status": "running", "months": [{
                    "key": "2025-05", "from_date": "2025-05-01", "to_date": "2025-05-16",
                }]}},
            },
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = job
        state = self.backend.sync_states(["conn_one"], "purchase")[0]
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["current_month"], "2025-05")
        self.assertEqual(state["current_until"], "2025-05-16")

    def test_queued_job_is_active_and_uses_selected_start_month(self):
        job = SimpleNamespace(
            job_id="job_queued", status="queued", current_stage=None,
            created_at="2026-08-01T00:00:00+00:00", finished_at=None,
            parameters={
                "directions": ["purchase"], "scopes": ["overview", "detail"],
                "sync_mode": "new", "baseline_invoice_count": 2,
                "date_from": "2025-05-01",
            },
            progress_state={},
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = job
        state = self.backend.sync_states(["conn_one"], "purchase")[0]
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["current_month"], "2025-05")
        self.assertEqual(state["current_until"], "2025-05-31")
        self.assertEqual(state["added_invoice_count"], 0)

    def test_active_job_counts_committed_invoices_realtime_from_baseline(self):
        database = self.backend.data_root / "0100000000" / "db" / "invoices.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "DELETE FROM invoice_overview_items WHERE company_tax_code=? AND direction=?",
                ("0100000000", "purchase"),
            )
            connection.executemany(
                "INSERT INTO invoice_overview_items VALUES (?,?,?,?,?)",
                [
                    (index, "0100000000", "purchase", "query", "2025-01-01T00:00:00+00:00")
                    for index in range(1000, 1450)
                ],
            )
            connection.commit()
        job = SimpleNamespace(
            job_id="job_realtime", status="running", current_stage="overview",
            created_at="2026-08-01T00:00:00+00:00", finished_at=None,
            parameters={
                "directions": ["purchase"], "scopes": ["overview", "detail"],
                "sync_mode": "new", "baseline_invoice_count": 450,
                "date_from": "2025-05-01", "date_to": "2025-08-31",
            },
            progress_state={
                "current_stage": "overview", "current_month": {"key": "2025-08"},
                "modules": {"overview": {"status": "running"}},
            },
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = job

        initial = self.backend.sync_states(["conn_one"], "purchase")[0]
        self.assertEqual((initial["invoice_count"], initial["added_invoice_count"]), (450, 0))
        self.assertEqual(initial["status"], "running")

        with closing(sqlite3.connect(database)) as connection:
            connection.executemany(
                "INSERT INTO invoice_overview_items VALUES (?,?,?,?,?)",
                [
                    (index, "0100000000", "purchase", "query", "2026-08-01T00:00:01+00:00")
                    for index in range(1450, 1456)
                ],
            )
            connection.commit()
        after_six = self.backend.sync_states(["conn_one"], "purchase")[0]
        self.assertEqual((after_six["invoice_count"], after_six["added_invoice_count"]), (456, 6))

        with closing(sqlite3.connect(database)) as connection:
            connection.executemany(
                "INSERT INTO invoice_overview_items VALUES (?,?,?,?,?)",
                [
                    (index, "0100000000", "purchase", "query", "2026-08-01T00:00:02+00:00")
                    for index in range(1456, 1460)
                ],
            )
            connection.commit()
        after_ten = self.backend.sync_states(["conn_one"], "purchase")[0]
        self.assertEqual((after_ten["invoice_count"], after_ten["added_invoice_count"]), (460, 10))
        self.assertEqual(after_ten["current_month"], "2025-08")

        job.status = "completed"
        job.current_stage = None
        job.finished_at = "2026-08-01T00:01:00+00:00"
        job.progress_state = {
            "modules": {"overview": {"status": "completed"}, "detail": {"status": "completed"}}
        }
        completed = self.backend.sync_states(["conn_one"], "purchase")[0]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual((completed["invoice_count"], completed["added_invoice_count"]), (460, 10))

    def test_source_business_key_upsert_adds_only_missing_supplement_invoices(self):
        database = Path(self.temporary.name) / "supplement.sqlite3"
        repository = InvoiceOverviewRepository(database)
        original = [
            {"nbmst": f"buyer-{index}", "khhdon": "AA", "shdon": str(index), "khmshdon": "1", "nlap": "2025-05-01"}
            for index in range(100)
        ]
        refreshed = original + [
            {"nbmst": f"buyer-{index}", "khhdon": "AA", "shdon": str(index), "khmshdon": "1", "nlap": "2025-05-01"}
            for index in range(100, 107)
        ]
        common = dict(
            company_tax_code="0100000000", direction="purchase", query_type="query",
            invoice_category="invoice", raw_json_path="", timestamp="2026-08-01T00:00:00+00:00",
        )
        repository.upsert_items(items=original, **common)
        repository.upsert_items(items=refreshed, **{**common, "timestamp": "2026-08-02T00:00:00+00:00"})
        self.assertEqual(repository.count_overview_items(
            "0100000000", "purchase", "query", "2025-05-01", "2025-05-31"
        ), 107)

    def test_source_business_key_upsert_replaces_existing_invoice_values(self):
        database = Path(self.temporary.name) / "force-refresh.sqlite3"
        repository = InvoiceOverviewRepository(database)
        common = dict(
            company_tax_code="0100000000", direction="sold", query_type="sco-query",
            invoice_category="invoice", raw_json_path="",
        )
        identity = {
            "nbmst": "buyer-1", "khhdon": "AA/25E", "shdon": "42",
            "khmshdon": "1", "nlap": "2025-05-10",
        }
        repository.upsert_items(
            items=[{**identity, "_public_fields": {"tthai": "OLD", "tgtttbso": 100}}],
            timestamp="2026-08-01T00:00:00+00:00", **common,
        )
        repository.upsert_items(
            items=[{**identity, "_public_fields": {"tthai": "NEW", "tgtttbso": 125}}],
            timestamp="2026-08-02T00:00:00+00:00", **common,
        )

        self.assertEqual(repository.count_overview_items(
            "0100000000", "sold", "sco-query", "2025-05-01", "2025-05-31"
        ), 1)
        with closing(sqlite3.connect(database)) as connection:
            values = dict(connection.execute(
                """SELECT field_name, value_json FROM invoice_overview_attributes
                   ORDER BY field_name"""
            ).fetchall())
        self.assertEqual(values, {"tgtttbso": "125", "tthai": '"NEW"'})


if __name__ == "__main__":
    unittest.main()
