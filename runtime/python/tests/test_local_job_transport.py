import unittest
from types import SimpleNamespace

from mia_backend import ProductionBackend


class LocalJobTransportTests(unittest.TestCase):
    def test_public_job_accepts_source_string_timestamps(self):
        job = SimpleNamespace(
            job_id="job-source-string-time",
            account_key="conn_account_1",
            company_tax_code="0100000000",
            parameters={
                "connection_id": "conn_account_1",
                "date_from": "2026-08-01",
                "date_to": "2026-08-21",
                "directions": ["purchase"],
                "query_types": ["query"],
                "result_scope": "detail",
                "include_xml": False,
                "force_refresh": False,
            },
            progress_state={"message": "Đang chờ worker xử lý", "current_month": None},
            current_stage=None,
            last_error_code=None,
            last_error_message=None,
            status="queued",
            progress_percent=0,
            progress_updated_at="2026-08-21T10:00:01+00:00",
            updated_at="2026-08-21T10:00:01+00:00",
            created_at="2026-08-21T10:00:00+00:00",
            lease_generation=0,
        )

        value = ProductionBackend.public_job(job)

        self.assertEqual(value["created_at"], "2026-08-21T10:00:00+00:00")
        self.assertEqual(value["updated_at"], "2026-08-21T10:00:01+00:00")
        self.assertEqual(value["message"], "Đang chờ worker xử lý")
        self.assertEqual(value["overall_percent"], 0)


if __name__ == "__main__":
    unittest.main()
