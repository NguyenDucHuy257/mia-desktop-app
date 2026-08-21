import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpyxl import load_workbook

import mia_runtime
from mia_backend import ProductionBackend


class SourceJobIntentTests(unittest.TestCase):
    def test_refresh_options_are_forwarded_to_source_create_job_body(self):
        backend = object.__new__(ProductionBackend)
        source_job = SimpleNamespace(job_id="job-source")
        backend.service = Mock()
        backend.service.create_job.return_value = source_job
        with patch.object(
            ProductionBackend,
            "public_job",
            return_value={"job_id": "job-source", "status": "queued"},
        ):
            result = backend.start({
                "idempotency_key": "source-policy-test",
                "intent": {
                    "connection_id": "conn_123456",
                    "date_from": "2026-07-15",
                    "date_to": "2026-08-10",
                    "directions": ["purchase", "sold"],
                    "query_types": ["query", "sco-query"],
                    "scopes": ["overview", "detail"],
                    "data_types": ["invoice"],
                    "force_refresh": False,
                    "refresh_latest_month": True,
                },
            })
        self.assertEqual(result["job_id"], "job-source")
        body = backend.service.create_job.call_args.args[0]
        self.assertEqual(body.connection_id, "conn_123456")
        self.assertFalse(body.force_refresh)
        self.assertTrue(body.refresh_latest_month)
        self.assertEqual(body.result_scope, "detail")
        self.assertFalse(body.include_xml)
        backend.service.create_job.assert_called_once()

    def test_force_refresh_is_not_rewritten_by_desktop_policy(self):
        backend = object.__new__(ProductionBackend)
        backend.service = Mock(return_value=None)
        backend.service.create_job.return_value = SimpleNamespace(job_id="job-source")
        with patch.object(
            ProductionBackend,
            "public_job",
            return_value={"job_id": "job-source", "status": "queued"},
        ):
            backend.start({
                "idempotency_key": "source-force-refresh-test",
                "intent": {
                    "connection_id": "conn_123456",
                    "date_from": "2023-01-01",
                    "date_to": "2026-08-31",
                    "directions": ["purchase"],
                    "query_types": ["query"],
                    "scopes": ["overview"],
                    "data_types": ["invoice"],
                    "force_refresh": True,
                    "refresh_latest_month": False,
                },
            })
        body = backend.service.create_job.call_args.args[0]
        self.assertTrue(body.force_refresh)
        self.assertFalse(body.refresh_latest_month)


class ResultViewTests(unittest.TestCase):
    def backend_with_job(self):
        backend = object.__new__(ProductionBackend)
        backend.data_root = Path("source-data")
        job = SimpleNamespace(
            job_id="job-latest",
            owner_id="mia-desktop-local",
            account_key="conn_account_1",
            created_at="2026-08-20T10:00:00+00:00",
            updated_at="2026-08-20T10:00:00+00:00",
            company_tax_code="0100000000",
            parameters={
                "connection_id": "conn_account_1",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "directions": ["purchase"],
                "query_types": ["query"],
            },
        )
        backend.repository = SimpleNamespace(
            list_jobs_for_reconciliation=lambda: [job]
        )
        return backend, job

    def test_result_range_overrides_latest_job_range_using_source_result_reader(self):
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
            return SimpleNamespace(**{**vars(job), "parameters": parameters})

        with patch(
            "mia_source_backend.JobResultReader", return_value=reader
        ), patch(
            "mia_source_backend.replace", side_effect=replace_job
        ):
            result = backend.results("overview", {
                "connection_id": "conn_account_1",
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

    def test_result_dispatch_initializes_source_backend_after_restart(self):
        previous = (
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
                "mia_runtime.ProductionBackend", return_value=backend
            ) as constructor:
                mia_runtime.data_directory = Path(directory)
                mia_runtime.logger = None
                mia_runtime.production_backend = None
                query = {
                    "connection_id": "conn_account_1",
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
                mia_runtime.data_directory,
                mia_runtime.logger,
                mia_runtime.production_backend,
            ) = previous

    def test_result_workbook_can_include_overview_and_detail_sheets(self):
        backend, _ = self.backend_with_job()
        backend._all_result_rows = Mock(side_effect=[
            [{
                "direction": "purchase",
                "business_key": "A",
                "payload": {"shdon": "1"},
            }],
            [{
                "direction": "purchase",
                "business_key": "A",
                "line_key": "1",
                "payload": {"thhdvu": "Dịch vụ"},
            }],
        ])
        with tempfile.TemporaryDirectory() as directory:
            result = backend.export_results({
                "destination": directory,
                "connection_ids": ["conn_account_1"],
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
