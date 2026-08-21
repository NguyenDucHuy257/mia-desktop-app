import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import mia_runtime
from mia_logging import configure_logging


class RuntimeDiagnosticsTests(unittest.TestCase):
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

    def test_runtime_and_crawler_logs_redact_sensitive_values_and_vendor_namespaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = configure_logging(root, "INFO")
            crawler = logging.getLogger("mia_crawler")
            vendor_app = logging.getLogger("app.crawlers.invoice_crawler")
            vendor_pipeline = logging.getLogger("mia.worker_runtime.pipeline")
            runtime.error("password=secret tax=0111380276 token=abc")
            crawler.error("authorization=BearerValue mst=0111380276")
            vendor_app.info("vendor_app_event status=200")
            vendor_pipeline.info("vendor_pipeline_event stage=overview")
            handlers = {
                *runtime.handlers,
                *crawler.handlers,
                *logging.getLogger("app").handlers,
                *logging.getLogger("mia.worker_runtime").handlers,
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


if __name__ == "__main__":
    unittest.main()
