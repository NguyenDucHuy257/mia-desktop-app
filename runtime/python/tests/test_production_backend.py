import base64
import os
import tempfile
import unittest
from pathlib import Path

from mia_backend import ProductionBackend


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


if __name__ == "__main__":
    unittest.main()
