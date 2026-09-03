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
                    id INTEGER PRIMARY KEY, company_tax_code TEXT, direction TEXT,
                    query_type TEXT, nbmst TEXT, khhdon TEXT, shdon TEXT, khmshdon TEXT,
                    nlap_date TEXT
                );
                CREATE TABLE invoice_overview_checkpoints (
                    company_tax_code TEXT, direction TEXT, query_type TEXT,
                    status_filter TEXT, from_date TEXT, to_date TEXT,
                    checkpoint_status TEXT, fetched_count INTEGER,
                    expected_total INTEGER, page_number INTEGER
                );
                CREATE TABLE invoice_detail_items (
                    id INTEGER PRIMARY KEY, company_tax_code TEXT, direction TEXT,
                    query_type TEXT, nbmst TEXT, khhdon TEXT, shdon TEXT,
                    khmshdon TEXT, nlap_date TEXT, normalized_ready INTEGER,
                    detail_outcome TEXT, error_message TEXT
                );
                CREATE TABLE invoice_detail_lines (
                    id INTEGER PRIMARY KEY, detail_item_id INTEGER, line_number INTEGER
                );
                CREATE TABLE invoice_detail_checkpoints (
                    company_tax_code TEXT, direction TEXT, query_type TEXT,
                    from_date TEXT, to_date TEXT, checkpoint_status TEXT,
                    overview_expected INTEGER, detail_succeeded INTEGER,
                    detail_failed INTEGER
                );
            """)
            connection.executemany(
                """INSERT INTO invoice_overview_items(
                       company_tax_code,direction,query_type,nbmst,khhdon,shdon,khmshdon,nlap_date
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                [(self.tax_code, "purchase", "query", "0200000000", "AA/25E", "1", "1", "2025-01-10"),
                 (self.tax_code, "purchase", "query", "0200000001", "AA/25E", "2", "1", "2025-02-10")],
            )
            connection.executemany(
                "INSERT INTO invoice_overview_checkpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(self.tax_code, "purchase", query_type, status, "2025-01-01", "2025-01-31", "finalized", 0, 0, 0)
                 for query_type, statuses in (("query", ("5", "6", "8")), ("sco-query", ("all",))) for status in statuses],
            )
            connection.executemany(
                "INSERT INTO invoice_detail_checkpoints VALUES(?,?,?,?,?,?,?,?,?)",
                [(self.tax_code, "purchase", query_type, "2025-01-01", "2025-01-31", "finalized", 0, 0, 0)
                 for query_type in ("query", "sco-query")],
            )
            connection.execute(
                "INSERT INTO invoice_detail_items VALUES(1,?,?,?,?,?,?,?,?,?,?,?)",
                (self.tax_code, "purchase", "query", "0200000000", "AA/25E", "1", "1",
                 "2025-01-10", 1, "with_lines", None),
            )
            connection.executemany(
                "INSERT INTO invoice_detail_lines(detail_item_id,line_number) VALUES(1,?)",
                [(line,) for line in range(1, 11)],
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
        self.assertEqual(state["detail_invoice_count"], 1)
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

    def test_persistent_counts_do_not_require_an_active_or_historical_job(self):
        self.backend.repository.latest_invoice_job_for_direction.return_value = None
        state = self.backend.sync_states(["conn_account"], "purchase")[0]
        self.assertEqual(state["status"], "not_synced")
        self.assertEqual(state["invoice_count"], 2)
        self.assertEqual(state["detail_invoice_count"], 1)
        self.assertIsNone(state["last_job_id"])

    def test_persisted_overview_and_detail_coverage_is_completed_for_selected_range(self):
        self.backend.repository.latest_invoice_job_for_direction.return_value = None
        state = self.backend.sync_states(
            ["conn_account"], "purchase", "2025-01-01", "2025-01-31"
        )[0]
        self.assertEqual(state["status"], "completed")
        self.assertTrue(state["overview_ready"])
        self.assertTrue(state["detail_ready"])

    def test_completed_legacy_job_restores_coverage_without_detail_checkpoint(self):
        database = self.root / self.tax_code / "db" / "invoices.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("DELETE FROM invoice_detail_checkpoints")
            connection.commit()
        legacy_job = SimpleNamespace(
            job_id="legacy-completed", status="completed",
            parameters={
                "directions": ["purchase"],
                "query_types": ["query", "sco-query"],
            },
            progress_state={"modules": {
                name: {"status": "completed", "months": [{
                    "from_date": "2025-01-01", "to_date": "2025-01-31",
                    "status": "completed",
                }]}
                for name in ("overview", "detail")
            }},
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = legacy_job
        self.backend.repository.invoice_jobs_for_account.return_value = [legacy_job]
        state = self.backend.sync_states(
            ["conn_account"], "purchase", "2025-01-01", "2025-01-31"
        )[0]
        self.assertEqual(state["status"], "completed")
        self.assertTrue(state["overview_ready"])
        self.assertTrue(state["detail_ready"])

    def test_overview_ready_without_detail_checkpoint_is_not_completed(self):
        database = self.root / self.tax_code / "db" / "invoices.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("DELETE FROM invoice_detail_checkpoints")
            connection.commit()
        self.backend.repository.latest_invoice_job_for_direction.return_value = None
        state = self.backend.sync_states(
            ["conn_account"], "purchase", "2025-01-01", "2025-01-31"
        )[0]
        self.assertEqual(state["status"], "not_synced")
        self.assertTrue(state["overview_ready"])
        self.assertFalse(state["detail_ready"])

    def test_overview_completed_while_detail_runs_is_still_running(self):
        job = SimpleNamespace(
            job_id="job-detail", status="running", current_stage="detail",
            parameters={"directions": ["purchase"], "date_from": "2025-01-01", "date_to": "2025-01-31"},
            progress_state={"current_stage": "detail", "current_month": {"key": "2025-01"}, "modules": {
                "overview": {"status": "completed", "months": []},
                "detail": {"status": "running", "months": []},
            }},
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = job
        state = self.backend.sync_states(
            ["conn_account"], "purchase", "2025-01-01", "2025-01-31"
        )[0]
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["current_stage"], "detail")

    def test_failed_detail_job_never_reports_completed(self):
        job = SimpleNamespace(
            job_id="job-failed", status="failed", current_stage="detail",
            parameters={"directions": ["purchase"], "date_from": "2025-01-01", "date_to": "2025-01-31"},
            progress_state={"current_stage": "detail", "modules": {
                "overview": {"status": "completed", "months": []},
                "detail": {"status": "running", "months": []},
            }},
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = job
        state = self.backend.sync_states(
            ["conn_account"], "purchase", "2025-01-01", "2025-01-31"
        )[0]
        self.assertEqual(state["status"], "failed")

    def test_missing_account_database_reports_zero_counts(self):
        self.backend.service.get_account_connection.return_value = SimpleNamespace(
            username="0999999999"
        )
        self.backend.repository.latest_invoice_job_for_direction.return_value = None
        state = self.backend.sync_states(["conn_empty"], "purchase")[0]
        self.assertEqual(state["status"], "not_synced")
        self.assertEqual(state["invoice_count"], 0)
        self.assertEqual(state["detail_invoice_count"], 0)

    def test_progress_totals_accumulate_discovered_months(self):
        totals = _progress_totals_from_state({"modules": {"overview": {"status": "running", "months": [
            {"planned": 100, "processed": 100}, {"planned": 80, "processed": 5}, {"processed": 0},
        ]}}})
        self.assertEqual(totals["overview"], {"processed": 105, "total": 180})


if __name__ == "__main__":
    unittest.main()
