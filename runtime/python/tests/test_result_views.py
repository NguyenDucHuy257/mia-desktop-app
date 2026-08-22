import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpyxl import load_workbook

import mia_runtime
from app.exporters.invoice_detail_excel_exporter import InvoiceDetailExcelExporter
from app.job_engine.models import JobRecord
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.services.overview_downloader import _find_header_row
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

    def test_real_source_excel_export_uses_selected_result_range(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "source-data"
            backend, _ = self.backend_with_job(data_root=data_root)
            database_path = data_root / "0100000000" / "db" / "invoices.sqlite3"
            overview_repository = InvoiceOverviewRepository(database_path)
            detail_repository = InvoiceDetailRepository(database_path)
            timestamp = "2026-08-22T03:00:00+00:00"
            raw_overview = root / "overview.json"
            raw_overview.write_text("{}", encoding="utf-8")

            def overview_item(day: str, shdon: str) -> dict:
                public = {
                    "khmshdon": "1",
                    "khhdon": "K25TAN",
                    "shdon": shdon,
                    "tdlap": f"{day}T08:00:00+07:00",
                    "nbmst": "0300555450-001",
                    "nbten": "CHI NHÁNH XĂNG DẦU\x0b SÀI GÒN",
                    "nmmst": "0315394414",
                    "nmten": "CÔNG TY TNHH HỒNG TRÀ NGỌC",
                    "nmdchi": "TP Hồ Chí Minh",
                    "tgtcthue": 100,
                    "tgtthue": 8,
                    "ttcktmai": 0,
                    "tgtphi": 0,
                    "tgtttbso": 108,
                    "dvtte": "VND",
                    "tgia": 1,
                    "tthai": 1,
                    "kqcht": "Hợp lệ",
                }
                return {
                    "nbmst": public["nbmst"],
                    "khhdon": public["khhdon"],
                    "shdon": shdon,
                    "khmshdon": public["khmshdon"],
                    "nlap": public["tdlap"],
                    "nlap_date": day,
                    "_public_fields": public,
                }

            overview_repository.upsert_items(
                company_tax_code="0100000000",
                direction="purchase",
                query_type="query",
                invoice_category="electronic",
                raw_json_path=raw_overview,
                items=[
                    overview_item("2025-05-01", "101"),
                    overview_item("2025-05-03", "303"),
                ],
                timestamp=timestamp,
            )

            def detail_payload(day: str, shdon: str) -> dict:
                return {
                    "detail": {
                        "khmshdon": 1,
                        "khhdon": "K25TAN",
                        "shdon": shdon,
                        "tdlap": f"{day}T08:00:00+07:00",
                        "nky": f"{day}T08:05:00+07:00",
                        "mhdon": f"MCCQT-{shdon}",
                        "dvtte": "VND",
                        "tgia": 1,
                        "nbten": "CHI NHÁNH XĂNG DẦU\x0b SÀI GÒN",
                        "nbmst": "0300555450-001",
                        "nbdchi": "TP Hồ Chí Minh",
                        "nmten": "CÔNG TY TNHH HỒNG TRÀ NGỌC",
                        "nmmst": "0315394414",
                        "nmdchi": "TP Hồ Chí Minh",
                        "ttcktmai": 0,
                        "tgtphi": 0,
                        "tgtttbso": 108,
                        "tgtthue": 8,
                        "tthai": 1,
                        "ttxly": 5,
                        "thtttoan": "CK",
                        "ttkhac": [],
                        "hdhhdvu": [{
                            "ten": "Dịch vụ\x0b kiểm thử",
                            "dvtinh": "Lần",
                            "sluong": 1,
                            "dgia": 100,
                            "stckhau": 0,
                            "tsuat": 8,
                            "thtien": 100,
                            "tthue": 8,
                            "tchat": 1,
                            "ttkhac": [],
                        }],
                    }
                }

            for day, shdon in (("2025-05-01", "101"), ("2025-05-03", "303")):
                raw_detail = root / f"detail-{shdon}.json"
                raw_detail.write_text(
                    json.dumps(detail_payload(day, shdon), ensure_ascii=False),
                    encoding="utf-8",
                )
                detail_repository.upsert_detail_success(
                    company_tax_code="0100000000",
                    direction="purchase",
                    query_type="query",
                    invoice_category="electronic",
                    nbmst="0300555450-001",
                    khhdon="K25TAN",
                    shdon=shdon,
                    khmshdon="1",
                    nlap=f"{day}T08:00:00+07:00",
                    nlap_date=day,
                    raw_detail_path=raw_detail,
                    http_status=200,
                    fetched_at=timestamp,
                )

            export_dir = root / "export"
            result = backend.export_results({
                "destination": str(export_dir),
                "connection_ids": ["conn_account_1"],
                "result_scopes": ["overview", "details"],
                "date_from": "2025-05-01",
                "date_to": "2025-05-02",
                "direction": "purchase",
                "query_type": "query",
                "search": "",
            })

            self.assertEqual(result["count"], 2)
            overview_path = next(
                Path(path) for path in result["files"]
                if Path(path).name.startswith("DANH SÁCH")
            )
            detail_path = next(
                Path(path) for path in result["files"]
                if Path(path).name.startswith("THỐNG KÊ CHI TIẾT")
            )

            overview_workbook = load_workbook(overview_path, data_only=False)
            try:
                worksheet = overview_workbook.active
                header_row = _find_header_row(worksheet)
                self.assertEqual(
                    worksheet.cell(4, 1).value,
                    "Từ ngày 01/05/2025 đến ngày 02/05/2025",
                )
                rows = [
                    row for row in worksheet.iter_rows(
                        min_row=header_row + 1,
                        max_row=worksheet.max_row,
                        values_only=True,
                    )
                    if any(value is not None for value in row)
                ]
                self.assertEqual(len(rows), 1)
                self.assertEqual(str(rows[0][3]), "101")
                self.assertNotIn("\x0b", str(rows[0][6]))
            finally:
                overview_workbook.close()

            detail_workbook = load_workbook(detail_path, data_only=False)
            try:
                worksheet = detail_workbook.active
                header_row = InvoiceDetailExcelExporter._find_header_row(worksheet)
                headers = [
                    str(worksheet.cell(header_row, column).value or "")
                    for column in range(1, worksheet.max_column + 1)
                ]
                shdon_column = headers.index("Số hóa đơn") + 1
                self.assertEqual(
                    worksheet.cell(4, 1).value,
                    "Từ ngày 01/05/2025 đến ngày 02/05/2025",
                )
                values = [
                    worksheet.cell(row, shdon_column).value
                    for row in range(header_row + 1, worksheet.max_row + 1)
                    if worksheet.cell(row, shdon_column).value is not None
                ]
                self.assertEqual([str(value) for value in values], ["101"])
                self.assertNotIn("\x0b", str(worksheet.cell(header_row + 1, 18).value))
            finally:
                detail_workbook.close()


if __name__ == "__main__":
    unittest.main()
