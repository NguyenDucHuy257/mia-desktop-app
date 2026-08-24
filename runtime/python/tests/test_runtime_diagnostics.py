import io
import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mia_runtime
from mia_logging import close_logging, configure_logging


class RuntimeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.previous = (
            mia_runtime.storage,
            mia_runtime.crawler,
            mia_runtime.data_directory,
            mia_runtime.logger,
            mia_runtime.production_backend,
        )

    def tearDown(self):
        (
            mia_runtime.storage,
            mia_runtime.crawler,
            mia_runtime.data_directory,
            mia_runtime.logger,
            mia_runtime.production_backend,
        ) = self.previous

    def test_slow_artifact_snapshot_does_not_starve_batch_status_rpc(self):
        snapshot_release = threading.Event()
        status_seen = threading.Event()
        snapshot_written = threading.Event()

        def dispatch(method, _params):
            if method == "artifacts.snapshot":
                snapshot_release.wait(1)
                return {"accounts": []}, False
            if method == "artifacts.batch.status":
                status_seen.set()
                return {"status": "running"}, False
            if method == "system.shutdown":
                return {"stopping": True}, True
            raise AssertionError(method)

        requests = b"".join(
            (json.dumps({"jsonrpc": "2.0", "id": index, "method": method, "params": {}}) + "\n").encode()
            for index, method in enumerate((
                "artifacts.snapshot", "artifacts.batch.status", "system.shutdown",
            ), start=1)
        )
        try:
            with patch.object(mia_runtime, "dispatch", side_effect=dispatch), \
                 patch.object(mia_runtime, "write_message", side_effect=lambda value: snapshot_written.set() if value.get("id") == 1 else None), \
                 patch.object(mia_runtime.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(requests))):
                self.assertEqual(mia_runtime.serve(), 0)
                self.assertTrue(status_seen.wait(0.5))
                snapshot_release.set()
                self.assertTrue(snapshot_written.wait(0.5))
        finally:
            snapshot_release.set()

    def test_production_backend_is_the_single_source_runtime_instance(self):
        backend = Mock()
        mia_runtime.data_directory = Path("C:/tmp/mia-test")
        mia_runtime.logger = None
        mia_runtime.production_backend = None
        with patch.object(
            mia_runtime, "ProductionBackend", return_value=backend
        ) as constructor:
            first = mia_runtime._production_backend()
            second = mia_runtime._production_backend()
        self.assertIs(first, backend)
        self.assertIs(second, backend)
        constructor.assert_called_once_with(mia_runtime.data_directory, None)

    def test_source_job_start_delegates_the_source_intent_without_credentials(self):
        backend = Mock()
        backend.start.return_value = {
            "job_id": "job-new",
            "connection_id": "conn_123456",
            "status": "queued",
            "stage": None,
            "overall_percent": 0,
            "message": "Đang chờ worker xử lý",
        }
        mia_runtime.data_directory = Path("C:/tmp/mia-test")
        mia_runtime.production_backend = backend
        request = {
            "idempotency_key": "desktop-source-v1:test",
            "intent": {
                "connection_id": "conn_123456",
                "date_from": "2025-05-01",
                "date_to": "2025-10-31",
                "directions": ["purchase", "sold"],
                "query_types": ["query", "sco-query"],
                "scopes": ["overview", "detail"],
                "data_types": ["invoice"],
                "force_refresh": False,
                "refresh_latest_month": True,
            },
        }
        result, should_stop = mia_runtime.dispatch("source.jobs.start", request)
        self.assertFalse(should_stop)
        self.assertEqual(result["job_id"], "job-new")
        backend.start.assert_called_once_with(request)
        serialized = str(backend.start.call_args)
        self.assertNotIn("password", serialized)
        self.assertNotIn("username", serialized)

    def test_source_account_routes_delegate_to_source_backend(self):
        backend = Mock()
        backend.create_connection.return_value = {
            "connection_id": "conn_123456",
            "username": "0101234567",
            "company_name": "Synthetic Company",
        }
        mia_runtime.data_directory = Path("C:/tmp/mia-test")
        mia_runtime.production_backend = backend
        result, should_stop = mia_runtime.dispatch("source.accounts.create", {
            "username": "0101234567",
            "password": "portal-password",
        })
        self.assertFalse(should_stop)
        self.assertEqual(result["connection_id"], "conn_123456")
        backend.create_connection.assert_called_once_with(
            "0101234567", "portal-password"
        )

    def test_source_job_status_logs_source_progress_fields(self):
        backend = Mock()
        backend.get.return_value = {
            "job_id": "job-1",
            "connection_id": "conn_123456",
            "status": "running",
            "stage": "overview",
            "overall_percent": 12.5,
            "message": "running:overview",
            "current_month": {
                "key": "2026-08",
                "index": 1,
                "total": 1,
                "processed": 10,
                "planned": 100,
                "percent": 10.0,
            },
            "error": None,
        }
        mia_runtime.data_directory = Path("C:/tmp/mia-test")
        mia_runtime.production_backend = backend
        with patch.object(mia_runtime, "_crawler_logger") as logger_factory:
            result, _ = mia_runtime.dispatch(
                "source.jobs.status", {"job_id": "job-1"}
            )
        self.assertEqual(result["overall_percent"], 12.5)
        logger_factory.return_value.info.assert_called()

    def test_runtime_and_crawler_logs_redact_sensitive_values_and_vendor_namespaces(self):
        with tempfile.TemporaryDirectory() as directory:
            try:
                root = Path(directory)
                runtime = configure_logging(root, "INFO")
                crawler = logging.getLogger("mia_crawler")
                vendor_app = logging.getLogger("app.crawlers.invoice_crawler")
                vendor_pipeline = logging.getLogger("mia.worker_runtime.pipeline")
                vendor_job_engine = logging.getLogger("mia.job_engine")
                runtime.error("password=secret tax=0111380276 token=abc")
                crawler.error("authorization=BearerValue mst=0111380276")
                vendor_app.info("vendor_app_event status=200")
                vendor_pipeline.info("vendor_pipeline_event stage=overview")
                vendor_job_engine.info("job_engine_event lease=recovered")
                handlers = {
                    *runtime.handlers,
                    *crawler.handlers,
                    *logging.getLogger("app").handlers,
                    *logging.getLogger("mia.worker_runtime").handlers,
                    *logging.getLogger("mia.job_engine").handlers,
                }
                for handler in handlers:
                    handler.flush()
                runtime_text = (root / "runtime.log").read_text(encoding="utf-8")
                crawler_text = (root / "crawler.log").read_text(encoding="utf-8")
                combined = runtime_text + crawler_text
                self.assertNotIn("secret", combined)
                self.assertNotIn("0111380276", combined)
                self.assertNotIn("BearerValue", combined)
                self.assertNotIn("token=abc", combined)
                self.assertIn("[REDACTED]", combined)
                self.assertIn("vendor_app_event", crawler_text)
                self.assertIn("vendor_pipeline_event", crawler_text)
                self.assertIn("job_engine_event", crawler_text)
            finally:
                close_logging()


if __name__ == "__main__":
    unittest.main()
