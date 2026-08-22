import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mia_runtime
from app.job_engine.models import JobRecord
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
    @staticmethod
    def source_job() -> JobRecord:
        return JobRecord(
            job_id="job-latest",
            account_key="conn_account_1",
            company_tax_code="0100000000",
            job_type="invoice_crawl",
            queue_order=1,
            parameters={
                "connection_id": "conn_account_1",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "directions": ["purchase", "sold"],
                "query_types": ["query", "sco-query"],
            },
            status="completed",
            current_stage="finalize",
            worker_id=None,
            lease_token=None,
            lease_generation=0,
            lease_expires_at=None,
            available_at="2026-08-20T10:00:00+00:00",
            cancel_requested_at=None,
            warning_count=0,
            created_at="2026-08-20T10:00:00+00:00",
            updated_at="2026-08-20T10:00:00+00:00",
            started_at="2026-08-20T10:00:00+00:00",
            finished_at="2026-08-20T10:05:00+00:00",
            last_error_code=None,
            last_error_message=None,
            owner_id="mia-desktop-local",
            pipeline_version=2,
        )

    def backend_with_job(self, *, data_root: Path | None = None):
        backend = object.__new__(ProductionBackend)
        backend.data_root = data_root or Path("source-data")
        job = self.source_job()
        backend.repository = SimpleNamespace(
            list_jobs_for_reconciliation=lambda: [job]
        )
        return backend, job

    def test_result_range_and_source_filters_override_latest_job_view(self):
        backend, _ = self.backend_with_job()
        reader = Mock()
        reader.overview_page.return_value = {
            "items": [{
                "id": 7,
                "nbmst": "0300000000",
                "khhdon": "AA/26E",
                "shdon": "12",
                "khmshdon": "1",
            }],
            "total_count": 1,
            "pagination": {"has_more": False, "next_cursor": None},
        }

        with patch(
            "app.external_api.results.JobResultReader", return_value=reader
        ):
            result = backend.results("overview", {
                "connection_id": "conn_account_1",
                "date_from": "2026-02-01",
                "date_to": "2026-02-28",
                "direction": "sold",
                "query_type": "sco-query",
                "limit": 50,
            })

        read_job = reader.overview_page.call_args.args[0]
        self.assertEqual(read_job.parameters["date_from"], "2026-02-01")
        self.assertEqual(read_job.parameters["date_to"], "2026-02-28")
        self.assertEqual(read_job.parameters["directions"], ["sold"])
        self.assertEqual(read_job.parameters["query_types"], ["sco-query"])
        self.assertEqual(result["items"][0]["direction"], "sold")
        self.assertTrue(result["columns"])
        self.assertTrue(result["column_labels"])

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

    def test_result_export_uses_separate_source_native_overview_and_detail_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend, _ = self.backend_with_job(data_root=root / "source-data")
            overview_row = {
                "khmshdon": "1",
                "khhdon": "AA/26E",
                "shdon": "1",
                "tdlap": "2026-02-01T00:00:00+07:00",
            }
            detail_record = {"raw_detail_path": "unused.json"}

            def write_overview(_rows, **kwargs):
                kwargs["target"].write_bytes(b"source-overview")

            detail_repository = Mock()
            detail_repository.get_detail_records_for_export.return_value = [detail_record]
            detail_exporter = Mock()
            detail_exporter.export.side_effect = (
                lambda **kwargs: Path(kwargs["output_path"]).write_bytes(b"source-detail")
            )

            with patch(
                "mia_source_results._all_overview_fields",
                return_value=[overview_row],
            ) as overview_rows, patch(
                "mia_source_results._write_overview_excel_from_source_template",
                side_effect=write_overview,
            ) as overview_writer, patch(
                "app.repositories.invoice_detail_query_repository.InvoiceDetailQueryRepository",
                return_value=detail_repository,
            ), patch(
                "app.exporters.invoice_detail_excel_exporter.InvoiceDetailExcelExporter",
                return_value=detail_exporter,
            ):
                result = backend.export_results({
                    "destination": directory,
                    "connection_ids": ["conn_account_1"],
                    "result_scopes": ["overview", "details"],
                    "date_from": "2026-02-01",
                    "date_to": "2026-02-28",
                    "direction": "purchase",
                    "query_type": "query",
                    "search": "",
                })

            self.assertEqual(result["count"], 2)
            self.assertEqual(len(result["files"]), 2)
            self.assertTrue(all(Path(path).is_file() for path in result["files"]))
            overview_rows.assert_called_once()
            overview_writer.assert_called_once()
            detail_repository.get_detail_records_for_export.assert_called_once()
            detail_exporter.export.assert_called_once()


if __name__ == "__main__":
    unittest.main()
