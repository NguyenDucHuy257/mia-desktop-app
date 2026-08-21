import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpyxl import load_workbook

import mia_runtime
from mia_backend import BUSINESS_TIMEZONE, DesktopInvoiceCrawlPipeline, ProductionBackend


class RefreshPolicyTests(unittest.TestCase):
    @staticmethod
    def pipeline(now=datetime(2026, 8, 21, 12, 0, tzinfo=BUSINESS_TIMEZONE)):
        pipeline = object.__new__(DesktopInvoiceCrawlPipeline)
        pipeline.clock = lambda: now
        return pipeline

    @staticmethod
    def parameters(date_from, date_to, *, force_refresh=False):
        return {
            "date_from": date_from,
            "date_to": date_to,
            "directions": ["purchase", "sold"],
            "query_types": ["query", "sco-query"],
            "force_refresh": force_refresh,
            "refresh_recent_months": True,
        }

    def test_old_historical_range_uses_verified_cache_when_fresh_download_is_off(self):
        pipeline = self.pipeline()
        parameters = self.parameters("2023-10-01", "2023-10-31")
        self.assertIsNone(pipeline._latest_month_range(parameters))
        self.assertEqual(pipeline._latest_month_force_slices(parameters), frozenset())

    def test_previous_and_current_calendar_month_are_always_forced(self):
        pipeline = self.pipeline()
        parameters = self.parameters("2026-06-01", "2026-08-31")
        self.assertEqual(
            pipeline._latest_month_range(parameters),
            (date(2026, 7, 1), date(2026, 8, 31)),
        )
        slices = pipeline._latest_month_force_slices(parameters)
        expected = {
            (direction, query_type, begin, end)
            for direction in ("purchase", "sold")
            for query_type in ("query", "sco-query")
            for begin, end in (
                (date(2026, 7, 1), date(2026, 7, 31)),
                (date(2026, 8, 1), date(2026, 8, 31)),
            )
        }
        self.assertEqual(slices, frozenset(expected))
        self.assertFalse(any(item[2].month == 6 for item in slices))

    def test_partial_recent_range_only_forces_selected_days(self):
        pipeline = self.pipeline()
        parameters = self.parameters("2026-07-15", "2026-08-10")
        self.assertEqual(
            pipeline._latest_month_range(parameters),
            (date(2026, 7, 15), date(2026, 8, 10)),
        )
        slices = pipeline._latest_month_force_slices(parameters)
        self.assertIn(
            ("purchase", "query", date(2026, 7, 15), date(2026, 7, 31)),
            slices,
        )
        self.assertIn(
            ("purchase", "query", date(2026, 8, 1), date(2026, 8, 10)),
            slices,
        )

    def test_fresh_download_checkbox_delegates_to_full_production_force_refresh(self):
        pipeline = self.pipeline()
        parameters = self.parameters("2023-01-01", "2026-08-31", force_refresh=True)
        # Production CoveragePlanner sees force_refresh=True and refreshes every
        # selected slice. The desktop recent-month hook must not narrow it.
        self.assertIsNone(pipeline._latest_month_range(parameters))
        self.assertEqual(pipeline._latest_month_force_slices(parameters), frozenset())

    def test_january_policy_refreshes_previous_december_and_current_january(self):
        pipeline = self.pipeline(datetime(2027, 1, 10, 12, 0, tzinfo=BUSINESS_TIMEZONE))
        parameters = self.parameters("2026-12-01", "2027-01-31")
        self.assertEqual(
            pipeline._latest_month_range(parameters),
            (date(2026, 12, 1), date(2027, 1, 31)),
        )


class ResultViewTests(unittest.TestCase):
    def backend_with_job(self):
        backend = object.__new__(ProductionBackend)
        backend.data_root = Path("source-data")
        job = SimpleNamespace(
            job_id="job-latest",
            created_at="2026-08-20T10:00:00+00:00",
            updated_at="2026-08-20T10:00:00+00:00",
            company_tax_code="0100000000",
            parameters={
                "connection_id": "account-1",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "directions": ["purchase"],
                "query_types": ["query"],
            },
        )
        backend.repository = SimpleNamespace(list_jobs_for_reconciliation=lambda: [job])
        return backend, job

    def test_result_range_overrides_latest_job_range_and_reads_all_invoice_types(self):
        backend, _ = self.backend_with_job()
        reader = Mock()
        reader.overview_page.return_value = {
            "items": [{
                "id": 7,
                "direction": "sold",
                "nbmst": "0300000000",
                "khhdon": "AA/26E",
                "shdon": "12",
                "khmshdon": "1",
            }],
            "pagination": {"has_more": False, "next_cursor": None},
        }

        def replace_job(job, *, parameters):
            return SimpleNamespace(**{
                **vars(job),
                "parameters": parameters,
            })

        with patch("mia_backend.JobResultReader", return_value=reader), patch(
            "mia_backend.replace", side_effect=replace_job,
        ):
            result = backend.results("overview", {
                "connection_id": "account-1",
                "date_from": "2026-02-01",
                "date_to": "2026-02-28",
                "direction": None,
                "limit": 50,
            })

        read_job = reader.overview_page.call_args.args[0]
        self.assertEqual(read_job.parameters["date_from"], "2026-02-01")
        self.assertEqual(read_job.parameters["date_to"], "2026-02-28")
        self.assertEqual(read_job.parameters["directions"], ["purchase", "sold"])
        self.assertEqual(read_job.parameters["query_types"], ["query", "sco-query"])
        self.assertEqual(result["items"][0]["direction"], "sold")

    def test_result_dispatch_initializes_production_backend_after_restart(self):
        previous = (
            mia_runtime.storage,
            mia_runtime.data_directory,
            mia_runtime.logger,
            mia_runtime.production_backend,
        )
        backend = Mock()
        backend.results.return_value = {
            "items": [{"overview_id": 1}],
            "pagination": {"limit": 50, "has_more": False, "next_cursor": None},
        }
        try:
            with tempfile.TemporaryDirectory() as directory, patch(
                "mia_runtime.ProductionBackend", return_value=backend,
            ) as constructor:
                mia_runtime.storage = object()
                mia_runtime.data_directory = Path(directory)
                mia_runtime.logger = None
                mia_runtime.production_backend = None
                query = {
                    "connection_id": "account-1",
                    "date_from": "2026-02-01",
                    "date_to": "2026-02-28",
                }
                result, should_stop = mia_runtime.dispatch("results.overview", query)
                self.assertFalse(should_stop)
                self.assertEqual(result["items"][0]["overview_id"], 1)
                constructor.assert_called_once_with(Path(directory), None)
                backend.results.assert_called_once_with("overview", query)
        finally:
            (
                mia_runtime.storage,
                mia_runtime.data_directory,
                mia_runtime.logger,
                mia_runtime.production_backend,
            ) = previous

    def test_result_workbook_can_include_overview_and_detail_sheets(self):
        backend, _ = self.backend_with_job()
        backend._all_result_rows = Mock(side_effect=[
            [{"direction": "purchase", "business_key": "A", "payload": {"shdon": "1"}}],
            [{"direction": "purchase", "business_key": "A", "line_key": "1", "payload": {"thhdvu": "Dịch vụ"}}],
        ])
        with tempfile.TemporaryDirectory() as directory:
            result = backend.export_results({
                "destination": directory,
                "connection_ids": ["account-1"],
                "result_scopes": ["overview", "details"],
                "date_from": "2026-02-01",
                "date_to": "2026-02-28",
                "direction": None,
                "search": "",
            })
            target = Path(result["files"][0])
            self.assertTrue(target.is_file())
            workbook = load_workbook(target, read_only=True)
            self.assertEqual(workbook.sheetnames, ["Tong quan", "Chi tiet"])
            workbook.close()


if __name__ == "__main__":
    unittest.main()
