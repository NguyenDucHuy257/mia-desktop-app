import base64
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mia_backend import ProductionBackend


class ProductionBackendTests(unittest.TestCase):
    def setUp(self):
        self.previous_key = os.environ.get("MIA_SESSION_ENCRYPTION_KEY")
        self.previous_key_id = os.environ.get("MIA_SESSION_ENCRYPTION_KEY_ID")
        os.environ["MIA_SESSION_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
            b"x" * 32
        ).decode()
        os.environ["MIA_SESSION_ENCRYPTION_KEY_ID"] = "test-key"

    def tearDown(self):
        if self.previous_key is None:
            os.environ.pop("MIA_SESSION_ENCRYPTION_KEY", None)
        else:
            os.environ["MIA_SESSION_ENCRYPTION_KEY"] = self.previous_key
        if self.previous_key_id is None:
            os.environ.pop("MIA_SESSION_ENCRYPTION_KEY_ID", None)
        else:
            os.environ["MIA_SESSION_ENCRYPTION_KEY_ID"] = self.previous_key_id

    def _create_backend_and_connection(self, directory: str):
        backend = ProductionBackend(Path(directory), start_worker=False)
        with patch.object(
            ProductionBackend,
            "_source_company_name",
            return_value="Synthetic Company",
        ):
            connection = backend.create_connection(
                "0100000000", "synthetic-password"
            )
        self.assertTrue(connection["connection_id"].startswith("conn_"))
        self.assertEqual(connection["company_name"], "Synthetic Company")
        return backend, connection

    def test_company_name_uses_same_source_managed_session_as_worker(self):
        backend = object.__new__(ProductionBackend)
        connection = SimpleNamespace(connection_id="conn_account_1")
        backend.accounts = Mock()
        backend.accounts.session_hash.return_value = (connection, "a" * 64)
        portal = Mock()
        portal.get_company_info.return_value = {"name": "  Source Company  "}
        backend.sessions = Mock()
        backend.sessions.build_worker_portal_session.return_value = portal

        self.assertEqual(backend._source_company_name(connection), "Source Company")
        backend.accounts.session_hash.assert_called_once_with(
            "conn_account_1",
            owner_id="mia-desktop-local",
        )
        backend.sessions.build_worker_portal_session.assert_called_once_with(
            "a" * 64,
            worker_id="desktop-local-worker",
        )
        portal.get_company_info.assert_called_once_with()

    def test_source_account_connection_and_job_engine_own_durable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            backend, connection = self._create_backend_and_connection(directory)
            request = {
                "idempotency_key": "desktop-source-idempotent-test",
                "intent": {
                    "connection_id": connection["connection_id"],
                    "date_from": "2026-01-01",
                    "date_to": "2026-02-28",
                    "directions": ["purchase", "sold"],
                    "query_types": ["query", "sco-query"],
                    "scopes": ["overview", "detail"],
                    "data_types": ["invoice"],
                    "force_refresh": False,
                    "refresh_latest_month": True,
                },
            }
            first = backend.start(request)
            second = backend.start(request)
            self.assertEqual(first["job_id"], second["job_id"])
            self.assertEqual(first["status"], "queued")
            self.assertEqual(first["overall_percent"], 0)
            work = backend.summary(first["job_id"])["work"]
            self.assertEqual(
                [item["key"] for item in work["pipeline_plan"]["months"]],
                ["2026-01", "2026-02"],
            )
            self.assertEqual(work["message"], "Đang chờ worker xử lý")
            cancelled = backend.cancel(first["job_id"])
            self.assertIn(cancelled["status"], {"cancelled", "cancelling"})
            backend.close()

    def test_artifact_intent_uses_source_pipeline_prerequisites(self):
        with tempfile.TemporaryDirectory() as directory:
            backend, connection = self._create_backend_and_connection(directory)
            record = backend.start({
                "idempotency_key": "desktop-source-html-prerequisite",
                "intent": {
                    "connection_id": connection["connection_id"],
                    "date_from": "2026-01-01",
                    "date_to": "2026-01-01",
                    "directions": ["purchase"],
                    "query_types": ["query"],
                    "scopes": ["overview"],
                    "data_types": ["html"],
                    "force_refresh": False,
                    "refresh_latest_month": True,
                },
            })
            source = backend.repository.get_job(record["job_id"])
            self.assertEqual(source.parameters["result_scope"], "detail")
            self.assertTrue(source.parameters["include_xml"])
            self.assertEqual(
                source.parameters["pipeline_plan"]["modules"],
                ["overview", "detail", "ensure_xml"],
            )
            backend.cancel(record["job_id"])
            backend.close()

    def test_public_job_uses_local_source_error_transport(self):
        now = datetime(2026, 8, 21, 8, 0, tzinfo=timezone.utc)
        job = SimpleNamespace(
            job_id="job-source",
            account_key="conn_abc123",
            company_tax_code="0100000000",
            parameters={
                "connection_id": "conn_abc123",
                "date_from": "2026-08-01",
                "date_to": "2026-08-21",
                "directions": ["purchase"],
                "query_types": ["query"],
                "result_scope": "detail",
                "include_xml": False,
                "force_refresh": False,
            },
            progress_state={
                "version": 3,
                "current_stage": "auth",
                "current_month": None,
                "message": "auth:captcha_fetched",
            },
            current_stage="auth",
            last_error_code="source_rate_limited",
            last_error_message="source_rate_limited",
            status="failed",
            progress_percent=5,
            progress_updated_at=now,
            updated_at=now,
            created_at=now,
            lease_generation=2,
        )
        value = ProductionBackend.public_job(job)
        self.assertEqual(value["message"], "auth:captcha_fetched")
        self.assertEqual(value["overall_percent"], 5)
        self.assertEqual(value["error"]["code"], "source_rate_limited")
        self.assertEqual(value["error"]["message"], "source_rate_limited")
        # HTTP API retryability classification is intentionally not part of the
        # local JSON-RPC contract. Desktop owns polling/retry behavior itself.
        self.assertFalse(value["error"]["retryable"])

    def test_pdf_export_calls_source_pdf_service_for_latest_completed_job(self):
        backend = object.__new__(ProductionBackend)
        backend.data_root = Path("data-root")
        job = SimpleNamespace(
            job_id="new",
            updated_at="2026-08-20T00:00:00Z",
            company_tax_code="0100000000",
            status="completed",
            parameters={
                "connection_id": "conn_account_1",
                "directions": ["purchase"],
                "query_types": ["query"],
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        backend.repository = SimpleNamespace(
            list_jobs_for_reconciliation=lambda: [job]
        )
        service = Mock()
        with patch(
            "mia_source_backend.InvoicePdfExportService", return_value=service
        ):
            backend.prepare_artifacts({
                "connection_ids": ["conn_account_1"],
                "kinds": ["pdf"],
            })
        service.export_invoice_pdfs.assert_called_once_with(
            "0100000000",
            "purchase",
            "query",
            "2026-01-01",
            "2026-01-31",
            overwrite=False,
        )


if __name__ == "__main__":
    unittest.main()
