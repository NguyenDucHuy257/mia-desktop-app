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

    def test_overview_completion_controls_status_while_detail_is_running(self):
        job = SimpleNamespace(
            job_id="job_purchase", status="running", current_stage="detail",
            created_at="2026-08-01T00:00:00+00:00", finished_at=None,
            parameters={"directions": ["purchase"], "sync_mode": "supplement", "baseline_invoice_count": 1},
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
        self.assertEqual(state["invoice_count"], 2)
        self.assertEqual(state["baseline_invoice_count"], 1)
        self.assertEqual(state["added_invoice_count"], 1)
        self.assertEqual((state["sync_from"], state["sync_until"]), ("2025-01-01", "2025-08-31"))

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
            parameters={"directions": ["purchase"], "sync_mode": "new", "baseline_invoice_count": 1},
            progress_state={
                "current_stage": "overview", "current_month": {"key": "2025-05"},
                "modules": {"overview": {"status": "running"}},
            },
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = job
        state = self.backend.sync_states(["conn_one"], "purchase")[0]
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["current_month"], "2025-05")

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


if __name__ == "__main__":
    unittest.main()
