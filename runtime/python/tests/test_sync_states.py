import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from mia_backend import ProductionBackend, _progress_totals_from_state


class SyncStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.tax_code = "0100000000"
        database = self.root / self.tax_code / "db" / "invoices.sqlite3"
        database.parent.mkdir(parents=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript("""
                CREATE TABLE invoice_overview_items (
                    id INTEGER PRIMARY KEY, company_tax_code TEXT, direction TEXT, nlap_date TEXT
                );
                CREATE TABLE invoice_overview_checkpoints (
                    company_tax_code TEXT, direction TEXT, from_date TEXT, to_date TEXT, checkpoint_status TEXT
                );
            """)
            connection.executemany(
                "INSERT INTO invoice_overview_items(company_tax_code,direction,nlap_date) VALUES(?,?,?)",
                [(self.tax_code, "purchase", "2025-01-10"), (self.tax_code, "purchase", "2025-02-10")],
            )
            connection.execute(
                "INSERT INTO invoice_overview_checkpoints VALUES(?,?,?,?,?)",
                (self.tax_code, "purchase", "2025-01-01", "2025-01-31", "finalized"),
            )
            connection.commit()
        self.backend = object.__new__(ProductionBackend)
        self.backend.data_root = self.root
        self.backend.service = Mock()
        self.backend.service.get_account_connection.return_value = SimpleNamespace(username=self.tax_code)
        self.backend.repository = Mock()

    def tearDown(self):
        self.directory.cleanup()

    def test_running_job_wins_over_old_finalized_coverage_and_counts_real_rows(self):
        job = SimpleNamespace(
            job_id="job-1", status="running", current_stage="overview",
            parameters={"directions": ["purchase"], "scopes": ["overview"], "date_from": "2025-01-01", "date_to": "2025-02-28", "baseline_invoice_count": 1, "sync_mode": "supplement"},
            progress_state={"current_stage": "overview", "current_month": {"key": "2025-02"}, "modules": {"overview": {"status": "running", "months": []}}},
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = job
        state = self.backend.sync_states(["conn_account"], "purchase")[0]
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["invoice_count"], 2)
        self.assertEqual(state["added_invoice_count"], 1)
        self.assertEqual(state["current_until"], "2025-02-28")

    def test_replacement_counts_selected_range_as_downloaded_new(self):
        job = SimpleNamespace(
            job_id="job-new", status="running", current_stage="overview",
            parameters={"directions": ["purchase"], "scopes": ["overview"], "date_from": "2025-01-01", "date_to": "2025-01-31", "baseline_invoice_count": 5, "replaced_old_count": 4, "replacement_prepared": True, "sync_mode": "new"},
            progress_state={"current_stage": "overview", "current_month": {"key": "2025-01"}, "modules": {"overview": {"status": "running", "months": []}}},
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = job
        state = self.backend.sync_states(["conn_account"], "purchase")[0]
        self.assertEqual(state["replaced_old_count"], 4)
        self.assertEqual(state["downloaded_new_count"], 1)
        self.assertEqual(state["invoice_count"], 2)

    def test_progress_totals_accumulate_discovered_months(self):
        totals = _progress_totals_from_state({"modules": {"overview": {"status": "running", "months": [
            {"planned": 100, "processed": 100}, {"planned": 80, "processed": 5}, {"processed": 0},
        ]}}})
        self.assertEqual(totals["overview"], {"processed": 105, "total": 180})


if __name__ == "__main__":
    unittest.main()
