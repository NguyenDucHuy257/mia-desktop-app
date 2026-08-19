import base64
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mia_backend import DesktopInvoiceCrawlTaskHandler, ProductionBackend


class ProductionBackendTests(unittest.TestCase):
    def setUp(self):
        self.previous_key = os.environ.get("MIA_SESSION_ENCRYPTION_KEY")
        os.environ["MIA_SESSION_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(b"x" * 32).decode()

    def tearDown(self):
        if self.previous_key is None:
            os.environ.pop("MIA_SESSION_ENCRYPTION_KEY", None)
        else:
            os.environ["MIA_SESSION_ENCRYPTION_KEY"] = self.previous_key

    def test_production_job_engine_owns_idempotency_progress_and_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = ProductionBackend(Path(directory), start_worker=False)
            request = {
                "username": "0100000000", "password": "synthetic-password",
                "idempotency_key": "desktop-idempotent-test",
                "intent": {
                    "connection_id": "desktop-account-1",
                    "date_from": "2026-01-01", "date_to": "2026-02-28",
                    "directions": ["purchase", "sold"],
                    "query_types": ["query", "sco-query"],
                    "scopes": ["overview", "detail"], "data_types": ["invoice"],
                },
            }
            first = backend.start(request)
            second = backend.start(request)
            self.assertEqual(first["job_id"], second["job_id"])
            self.assertEqual(first["overall_percent"], 0)
            work = backend.summary(first["job_id"])["work"]
            self.assertEqual([item["key"] for item in work["pipeline_plan"]["months"]], ["2026-01", "2026-02"])
            cancelled = backend.cancel(first["job_id"])
            self.assertEqual(cancelled["status"], "cancelled")
            backend.close()

    def test_desktop_artifact_intent_uses_production_package_handler(self):
        handler = object.__new__(DesktopInvoiceCrawlTaskHandler)
        job = SimpleNamespace(parameters={"data_types": ["html", "pdf"]})
        with patch(
            "mia_backend.InvoiceCrawlTaskHandler.run_xml_unit",
            return_value={"downloaded_count": 1},
        ) as production:
            result = handler.run_xml_unit(job, {"export_xml": True})
        self.assertEqual(result, {"downloaded_count": 1})
        production.assert_called_once_with(
            job,
            {"export_xml": False, "export_html": True},
            progress_callback=None,
        )

    def test_pdf_export_uses_production_pdf_service_for_latest_completed_job(self):
        backend = object.__new__(ProductionBackend)
        backend.data_root = Path("data-root")
        job = SimpleNamespace(
            job_id="new", updated_at="2026-08-20T00:00:00Z",
            company_tax_code="0100000000", status="completed",
            parameters={
                "connection_id": "account-1", "directions": ["purchase"],
                "query_types": ["query"], "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        backend.repository = SimpleNamespace(
            list_jobs_for_reconciliation=lambda: [job],
        )
        service = Mock()
        with patch("mia_backend.InvoicePdfExportService", return_value=service):
            backend.prepare_artifacts({
                "connection_ids": ["account-1"], "kinds": ["pdf"],
            })
        service.export_invoice_pdfs.assert_called_once_with(
            "0100000000", "purchase", "query", "2026-01-01", "2026-01-31",
            overwrite=False,
        )


if __name__ == "__main__":
    unittest.main()
