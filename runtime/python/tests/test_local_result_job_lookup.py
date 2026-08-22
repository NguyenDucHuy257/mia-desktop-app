import hashlib
import tempfile
import unittest
from pathlib import Path

from app.job_engine.models import CreateJobRequest, JobStageSpec
from mia_local_job_repository import LocalSequentialJobRepository


class LocalResultJobLookupTests(unittest.TestCase):
    def test_cancelled_job_is_not_reconciled_but_remains_available_to_results(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalSequentialJobRepository(Path(directory) / "control.sqlite3")
            repository.migrate()
            idempotency_key_hash = hashlib.sha256(b"result-test-key").hexdigest()
            request_fingerprint = hashlib.sha256(
                b"result-test-fingerprint"
            ).hexdigest()
            job = repository.create_admitted_job(
                CreateJobRequest(
                    account_key="conn_result_test",
                    company_tax_code="0100000000",
                    job_type="invoice_crawl",
                    parameters={
                        "connection_id": "conn_result_test",
                        "date_from": "2026-08-01",
                        "date_to": "2026-08-31",
                        "directions": ["purchase"],
                        "query_types": ["query"],
                    },
                    owner_id="mia-desktop-local",
                    idempotency_key_hash=idempotency_key_hash,
                    request_fingerprint=request_fingerprint,
                    pipeline_version=2,
                ),
                (),
                stages=[JobStageSpec("auth")],
            )
            cancelled = repository.request_cancellation(job.job_id)
            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(repository.list_jobs_for_reconciliation(), [])

            latest = repository.latest_invoice_job_for_account(
                "conn_result_test", owner_id="mia-desktop-local"
            )
            self.assertIsNotNone(latest)
            self.assertEqual(latest.job_id, job.job_id)
            self.assertEqual(latest.status, "cancelled")
            self.assertEqual(latest.company_tax_code, "0100000000")

            self.assertIsNone(repository.latest_invoice_job_for_account(
                "conn_result_test", owner_id="other-owner"
            ))


if __name__ == "__main__":
    unittest.main()
