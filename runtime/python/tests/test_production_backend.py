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

    def test_reused_import_reconnects_and_verifies_the_submitted_password(self):
        backend = object.__new__(ProductionBackend)
        existing = SimpleNamespace(connection_id="conn_existing", username="0100000000")
        reconnected = SimpleNamespace(connection_id="conn_existing", username="0100000000")
        backend.service = Mock()
        backend.service.create_account_connection.return_value = (existing, True)
        backend.service.reconnect_account_connection.return_value = reconnected
        backend._source_company_name = Mock(return_value="Verified Company")
        backend._save_company_name = Mock()
        backend.public_connection = Mock(return_value={"connection_id": "conn_existing"})

        result = backend.create_connection("0100000000", "submitted-password")

        self.assertEqual(result["connection_id"], "conn_existing")
        reconnect_body = backend.service.reconnect_account_connection.call_args.args[1]
        self.assertEqual(reconnect_body.username, "0100000000")
        self.assertEqual(reconnect_body.password.get_secret_value(), "submitted-password")
        backend._source_company_name.assert_called_once_with(reconnected)
        backend._save_company_name.assert_called_once_with(
            "conn_existing", "Verified Company"
        )

    def test_invalid_new_import_is_revoked_and_not_reported_as_success(self):
        backend = object.__new__(ProductionBackend)
        created = SimpleNamespace(connection_id="conn_invalid", username="0100000000")
        backend.service = Mock()
        backend.service.create_account_connection.return_value = (created, False)
        backend._source_company_name = Mock(side_effect=RuntimeError("invalid_source_credentials"))
        backend._save_company_name = Mock()

        with self.assertRaisesRegex(RuntimeError, "invalid_source_credentials"):
            backend.create_connection("0100000000", "wrong-password")

        backend.service.revoke_account_connection.assert_called_once_with(
            "conn_invalid", owner_id="mia-desktop-local"
        )
        backend._save_company_name.assert_not_called()

    def test_account_list_backfills_only_missing_names_and_isolates_failures(self):
        backend = object.__new__(ProductionBackend)
        backend.service = Mock()
        backend.logger = Mock()
        backend._load_company_names = Mock(return_value={"conn_named": "Cached Company"})
        backend._save_company_name = Mock()
        backend._source_company_name = Mock(
            side_effect=["Backfilled Company", RuntimeError("auth failed")]
        )
        records = [
            {"connection_id": "conn_named", "company_name": "Cached Company"},
            {"connection_id": "conn_missing", "company_name": None},
            {"connection_id": "conn_failed", "company_name": None},
        ]
        backend.service.get_account_connection.side_effect = [
            SimpleNamespace(connection_id="conn_missing"),
            SimpleNamespace(connection_id="conn_failed"),
        ]

        with patch("mia_backend.SourceBackend.list_connections", return_value=records):
            result = backend.list_connections()

        self.assertEqual(result[0]["company_name"], "Cached Company")
        self.assertEqual(result[1]["company_name"], "Backfilled Company")
        self.assertIsNone(result[2]["company_name"])
        self.assertEqual(backend._source_company_name.call_count, 2)
        backend._save_company_name.assert_called_once_with(
            "conn_missing", "Backfilled Company"
        )
        backend.logger.warning.assert_called_once()

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
                "artifact_progress": {
                    "current_key": "purchase|query|0101|AA|1|1",
                    "processed": 1,
                    "completed_xml": 1,
                    "completed_html": 1,
                    "items": {
                        "purchase|query|0101|AA|1|1": {
                            "xml": "completed", "html": "completed",
                        },
                    },
                },
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
        self.assertEqual(value["artifact_progress"]["completed_xml"], 1)
        self.assertEqual(
            value["artifact_progress"]["items"]["purchase|query|0101|AA|1|1"]["html"],
            "completed",
        )
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

    def test_artifact_export_keys_cover_every_filtered_overview_page(self):
        backend = object.__new__(ProductionBackend)
        pages = [
            {
                "items": [{"direction": "purchase", "fields": {
                    "nbmst": "0101", "khhdon": "AA", "shdon": "1", "khmshdon": "1",
                }}],
                "pagination": {"has_more": True, "next_cursor": "page-2"},
            },
            {
                "items": [{"direction": "sold", "fields": {
                    "nbmst": "0102", "khhdon": "BB", "shdon": "2", "khmshdon": "2",
                }}],
                "pagination": {"has_more": False, "next_cursor": None},
            },
        ]
        backend.results = Mock(side_effect=pages)

        keys = backend.artifact_keys_for_export({
            "connection_ids": ["conn_1"], "date_from": "2026-08-01",
            "date_to": "2026-08-31", "direction": None,
            "query_type": "query", "search": "đối tác",
        })

        self.assertEqual(keys, {
            "purchase|query|0101|AA|1|1", "sold|query|0102|BB|2|2",
        })
        self.assertEqual(backend.results.call_count, 2)
        self.assertEqual(backend.results.call_args_list[0].args[0], "overview")
        self.assertEqual(backend.results.call_args_list[1].args[1]["cursor"], "page-2")


if __name__ == "__main__":
    unittest.main()
