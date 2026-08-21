import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import mia_runtime
from mia_logging import close_logging, configure_logging


class RuntimeDiagnosticsTests(unittest.TestCase):
    def tearDown(self):
        mia_runtime.production_backend = None
        mia_runtime.data_directory = None

    def test_latest_jobs_returns_newest_record_per_connection(self):
        jobs = [
            SimpleNamespace(job_id="job_old", account_key="a", created_at="2026-08-20T10:00:00Z", parameters={"connection_id": "a"}),
            SimpleNamespace(job_id="job_new", account_key="a", created_at="2026-08-21T10:00:00Z", parameters={"connection_id": "a"}),
            SimpleNamespace(job_id="job_b", account_key="b", created_at="2026-08-21T09:00:00Z", parameters={"connection_id": "b"}),
        ]
        backend = SimpleNamespace(
            repository=SimpleNamespace(list_jobs_for_reconciliation=lambda: jobs),
            public_job=lambda job: {"job_id": job.job_id, "connection_id": job.parameters["connection_id"]},
        )
        result = mia_runtime._latest_jobs(backend)
        self.assertEqual({item["job_id"] for item in result}, {"job_new", "job_b"})

    def test_production_backend_recovers_expired_leases_before_worker_start(self):
        events = []
        recovery = SimpleNamespace(
            recovered_jobs=1,
            recovered_tasks=0,
            failed_tasks=0,
            cancelled_jobs=0,
            promoted_jobs=1,
        )
        repository = SimpleNamespace(
            recover_expired_leases=lambda: events.append("recover") or recovery,
        )
        planner = SimpleNamespace(plan=lambda **_kwargs: SimpleNamespace(decisions=[]))
        worker = SimpleNamespace(start=lambda: events.append("worker_start"))
        backend = SimpleNamespace(
            repository=repository,
            pipeline=SimpleNamespace(planner=planner),
            worker=worker,
        )
        mia_runtime.data_directory = Path("C:/tmp/mia-test")
        with patch.object(mia_runtime, "ProductionBackend", return_value=backend) as constructor:
            result = mia_runtime._production_backend()
        self.assertIs(result, backend)
        self.assertEqual(events, ["recover", "worker_start"])
        constructor.assert_called_once_with(mia_runtime.data_directory, mia_runtime.logger, start_worker=False)

    def test_source_job_start_does_not_run_coverage_planner_on_rpc_thread(self):
        planner = SimpleNamespace(plan=Mock(side_effect=AssertionError("planner must not run on start RPC")))
        backend = SimpleNamespace(
            pipeline=SimpleNamespace(planner=planner),
            start=Mock(return_value={
                "job_id": "job-new",
                "connection_id": "connection-a",
                "status": "queued",
            }),
        )
        mia_runtime.data_directory = Path("C:/tmp/mia-test")
        mia_runtime.production_backend = backend
        result, should_stop = mia_runtime.dispatch("source.jobs.start", {
            "username": "0101234567",
            "password": "secret",
            "idempotency_key": "desktop-v4:test",
            "intent": {
                "connection_id": "connection-a",
                "date_from": "2025-05-01",
                "date_to": "2025-10-31",
                "directions": ["purchase", "sold"],
                "query_types": ["query", "sco-query"],
                "scopes": ["overview", "detail"],
                "data_types": ["invoice"],
                "force_refresh": False,
            },
        })
        self.assertFalse(should_stop)
        self.assertEqual(result["job_id"], "job-new")
        planner.plan.assert_not_called()
        backend.start.assert_called_once()

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
                # RotatingFileHandler keeps an exclusive file handle on Windows.
                # Close it before TemporaryDirectory attempts to delete the logs.
                close_logging()


if __name__ == "__main__":
    unittest.main()
