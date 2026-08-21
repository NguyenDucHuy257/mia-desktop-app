import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpyxl import load_workbook

import mia_runtime
from mia_backend import ProductionBackend


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
