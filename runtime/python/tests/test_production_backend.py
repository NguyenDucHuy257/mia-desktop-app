import base64
import os
import tempfile
import unittest
import requests
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mia_backend import ProductionBackend, package_retry_delay


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

    def test_sync_mode_is_validated_and_mapped_to_source_refresh_options(self):
        backend = object.__new__(ProductionBackend)
        backend.service = Mock()
        backend.service.create_job.return_value = object()
        backend.public_job = Mock(return_value={"job_id": "job_sync_mode"})
        base = {
            "idempotency_key": "desktop-sync-mode",
            "intent": {
                "connection_id": "conn_account_1",
                "date_from": "2026-01-01",
                "date_to": "2026-02-28",
                "directions": ["purchase"],
                "query_types": ["query"],
                "scopes": ["overview"],
                "data_types": ["invoice"],
            },
        }

        backend.start({**base, "intent": {**base["intent"], "sync_mode": "new"}})
        new_body = backend.service.create_job.call_args.args[0]
        self.assertTrue(new_body.force_refresh)
        self.assertFalse(new_body.refresh_latest_month)

        backend.start({**base, "intent": {**base["intent"], "sync_mode": "supplement"}})
        supplement_body = backend.service.create_job.call_args.args[0]
        self.assertFalse(supplement_body.force_refresh)
        self.assertTrue(supplement_body.refresh_latest_month)

        with self.assertRaisesRegex(ValueError, "invalid_sync_mode"):
            backend.start({**base, "intent": {**base["intent"], "sync_mode": "replace"}})
        with self.assertRaisesRegex(ValueError, "sync_mode_requires_one_direction"):
            backend.start({
                **base,
                "intent": {
                    **base["intent"], "sync_mode": "new",
                    "directions": ["purchase", "sold"],
                },
            })

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
        targets = [
            {"artifact_key": "purchase|query|0101|AA|1|1"},
            {"artifact_key": "sold|query|0102|BB|2|2"},
        ]
        with patch("mia_source_results.read_artifact_targets", return_value=targets) as reader:
            keys = backend.artifact_keys_for_export({
                "connection_ids": ["conn_1"], "date_from": "2026-08-01",
                "date_to": "2026-08-31", "direction": None,
                "query_type": "query", "search": "đối tác",
            })

        self.assertEqual(keys, {
            "purchase|query|0101|AA|1|1", "sold|query|0102|BB|2|2",
        })
        reader.assert_called_once()
        self.assertEqual(reader.call_args.args[1]["query_type"], "query")
        self.assertEqual(reader.call_args.args[1]["search"], "đối tác")

    def test_invoice_package_batch_uses_overview_targets_without_creating_a_job(self):
        backend = object.__new__(ProductionBackend)
        backend.data_root = Path(tempfile.gettempdir()) / "mia-artifact-package-test"
        target = {
            "artifact_key": "purchase|query|0101|AA|1|1",
            "direction": "purchase", "query_type": "query", "nbmst": "0101",
            "khhdon": "AA", "shdon": "1", "khmshdon": "1",
            "nlap": "2026-08-01", "nlap_date": "2026-08-01",
        }
        backend.artifact_targets_for_export = Mock(return_value=[target])
        backend.accounts = Mock()
        backend.accounts.session_hash.return_value = (Mock(), "session-hash")
        backend._result_job = Mock(return_value=SimpleNamespace(parameters={"scope": "overview"}))
        backend.handler = Mock()
        backend.handler.run_xml_unit.return_value = {"outcome": "downloaded"}
        backend.logger = Mock()
        events = []
        request = {
            "connection_ids": ["conn_1"], "kinds": ["xml", "html"],
            "date_from": "2026-08-01", "date_to": "2026-08-31",
            "direction": "purchase", "query_type": "query", "search": "",
        }

        with patch("mia_backend.replace", return_value=SimpleNamespace(parameters={"session_hash": "session-hash"}, company_tax_code="0101")):
            result = backend.ensure_invoice_packages(request, progress_callback=events.append)

        backend.handler.authenticate_job.assert_called_once()
        backend.handler.run_xml_unit.assert_called_once()
        payload = backend.handler.run_xml_unit.call_args.args[1]
        self.assertEqual(payload["nbmst"], "0101")
        self.assertTrue(payload["export_xml"])
        self.assertTrue(payload["export_html"])
        self.assertEqual(result["keys"], {target["artifact_key"]})
        self.assertEqual(
            [(event["kind"], event["status"]) for event in events],
            [("xml", "running"), ("html", "running"),
             ("xml", "completed"), ("html", "completed")],
        )

    def _package_backend(self, side_effect):
        backend = object.__new__(ProductionBackend)
        backend.data_root = Path(tempfile.gettempdir()) / "mia-package-retry-test"
        target = {
            "artifact_key": "purchase|query|0101|AA|1|1",
            "direction": "purchase", "query_type": "query", "nbmst": "0101",
            "khhdon": "AA", "shdon": "1", "khmshdon": "1",
            "nlap": "2026-08-01", "nlap_date": "2026-08-01",
        }
        backend.artifact_targets_for_export = Mock(return_value=[target])
        backend.accounts = Mock()
        backend.accounts.session_hash.return_value = (Mock(), "session-hash")
        backend._result_job = Mock(return_value=SimpleNamespace(parameters={}))
        backend.handler = Mock()
        backend.handler.run_xml_unit.side_effect = side_effect
        backend.logger = Mock()
        request = {
            "connection_ids": ["conn_1"], "kinds": ["xml", "html"],
            "date_from": "2026-08-01", "date_to": "2026-08-31",
            "direction": "purchase", "query_type": "query", "search": "",
        }
        return backend, request

    @staticmethod
    def _transient_500():
        response = requests.Response()
        response.status_code = 500
        error = requests.HTTPError("HTTP 500")
        error.response = response
        return error

    def test_package_retry_uses_bounded_backoff_then_succeeds(self):
        backend, request = self._package_backend([
            self._transient_500(), self._transient_500(), {"outcome": "downloaded"},
        ])
        waits = []
        with patch("mia_backend.replace", return_value=SimpleNamespace(parameters={}, company_tax_code="0101")):
            result = backend.ensure_invoice_packages(
                request, retry_wait=lambda delay, _cancel: waits.append(delay)
            )
        self.assertEqual(waits, [1, 2])
        self.assertEqual(backend.handler.run_xml_unit.call_count, 3)
        self.assertEqual(result["failed"], 0)

    def test_missing_original_outcome_skips_without_retry_or_warning(self):
        backend, request = self._package_backend([{"outcome": "unavailable"}])
        waits = []
        ready = []
        with patch("mia_backend.replace", return_value=SimpleNamespace(parameters={}, company_tax_code="0101")):
            result = backend.ensure_invoice_packages(
                request, retry_wait=lambda delay, _cancel: waits.append(delay),
                ready_callback=lambda *_args: ready.append(_args),
            )
        self.assertEqual(waits, [])
        self.assertEqual(backend.handler.run_xml_unit.call_count, 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["outcomes"]["missing_original"], 1)
        self.assertEqual(ready[0][1:], ("missing_original", "missing_original"))
        backend.logger.warning.assert_not_called()

    def test_missing_original_exception_skips_without_retry(self):
        class InvoicePackageUnavailableError(RuntimeError):
            pass
        backend, request = self._package_backend([
            InvoicePackageUnavailableError("Không tồn tại hồ sơ gốc của hóa đơn."),
        ])
        waits = []
        with patch("mia_backend.replace", return_value=SimpleNamespace(parameters={}, company_tax_code="0101")):
            result = backend.ensure_invoice_packages(
                request, retry_wait=lambda delay, _cancel: waits.append(delay),
            )
        self.assertEqual(waits, [])
        self.assertEqual(backend.handler.run_xml_unit.call_count, 1)
        self.assertEqual(result["outcomes"]["missing_original"], 1)

    def test_existing_unavailable_row_is_retried_without_deleting_cache_fields(self):
        backend, request = self._package_backend([{"outcome": "downloaded"}])
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        backend.data_root = Path(temporary.name)
        target = backend.artifact_targets_for_export.return_value[0]
        database = backend.data_root / "0101" / "db" / "invoices.sqlite3"
        database.parent.mkdir(parents=True)
        connection = sqlite3.connect(database)
        try:
            connection.execute("""CREATE TABLE invoice_package_items (
                company_tax_code TEXT, direction TEXT, query_type TEXT, nbmst TEXT,
                khhdon TEXT, shdon TEXT, khmshdon TEXT, unavailable INTEGER,
                unavailable_reason TEXT, error_message TEXT, updated_at TEXT,
                xml_path TEXT, html_path TEXT, xml_fetched INTEGER, html_fetched INTEGER
            )""")
            connection.execute(
                "INSERT INTO invoice_package_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("0101", target["direction"], target["query_type"], target["nbmst"],
                 target["khhdon"], target["shdon"], target["khmshdon"], 1,
                 "old miss", "old miss", "now", "keep.xml", "keep.html", 0, 0),
            )
            connection.commit()
        finally:
            connection.close()
        with patch("mia_backend.replace", return_value=SimpleNamespace(parameters={}, company_tax_code="0101")):
            backend.ensure_invoice_packages(request, retry_wait=lambda *_args: None)
        self.assertEqual(backend.handler.run_xml_unit.call_count, 1)
        connection = sqlite3.connect(database)
        try:
            row = connection.execute(
                "SELECT unavailable,error_message,xml_path,html_path FROM invoice_package_items"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, (0, None, "keep.xml", "keep.html"))

    def test_exhausted_transient_package_is_warning_not_failure(self):
        backend, request = self._package_backend([self._transient_500()] * 7)
        waits = []
        ready = []
        with patch("mia_backend.replace", return_value=SimpleNamespace(parameters={}, company_tax_code="0101")):
            result = backend.ensure_invoice_packages(
                request, retry_wait=lambda delay, _cancel: waits.append(delay),
                ready_callback=lambda *_args: ready.append(_args),
            )
        self.assertEqual(waits, [1, 2, 4, 6, 8, 10])
        self.assertEqual(backend.handler.run_xml_unit.call_count, 7)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["outcomes"]["source_retry_exhausted"], 1)
        self.assertEqual(result["outcomes"]["source_confirmed_unavailable"], 0)
        self.assertEqual(ready[0][1:], ("unavailable", "source_retry_exhausted"))

    def test_package_retry_delay_caps_at_ten_seconds(self):
        self.assertEqual(
            [package_retry_delay(attempt) for attempt in range(1, 10)],
            [1, 2, 4, 6, 8, 10, 10, 10, 10],
        )

    def test_confirmed_missing_package_is_not_retry_exhausted(self):
        response = requests.Response()
        response.status_code = 404
        error = requests.HTTPError("HTTP 404")
        error.response = response
        backend, request = self._package_backend([error])
        ready = []
        with patch("mia_backend.replace", return_value=SimpleNamespace(parameters={}, company_tax_code="0101")):
            result = backend.ensure_invoice_packages(
                request, ready_callback=lambda *_args: ready.append(_args),
            )
        self.assertEqual(result["outcomes"]["source_confirmed_unavailable"], 1)
        self.assertEqual(result["outcomes"]["source_retry_exhausted"], 0)
        self.assertEqual(ready[0][1:], ("unavailable", "source_confirmed_unavailable"))

    def test_package_retry_wait_can_cancel_without_another_attempt(self):
        backend, request = self._package_backend([self._transient_500()])
        def cancel_wait(_delay, _cancel):
            raise ValueError("artifact_cancelled")
        with patch("mia_backend.replace", return_value=SimpleNamespace(parameters={}, company_tax_code="0101")):
            with self.assertRaisesRegex(ValueError, "artifact_cancelled"):
                backend.ensure_invoice_packages(request, retry_wait=cancel_wait)
        self.assertEqual(backend.handler.run_xml_unit.call_count, 1)


if __name__ == "__main__":
    unittest.main()
