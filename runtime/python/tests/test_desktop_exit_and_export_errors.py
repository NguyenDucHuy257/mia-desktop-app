import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import mia_backend
from mia_backend import ProductionBackend


class DesktopExitCancellationTests(unittest.TestCase):
    def test_exit_cancellation_requests_source_cancel_for_every_nonterminal_job(self):
        backend = object.__new__(ProductionBackend)
        backend.repository = Mock()
        backend.service = Mock()
        backend.logger = Mock()
        owner = mia_backend.source_backend_module.OWNER_ID
        backend.repository.list_jobs_for_reconciliation.return_value = [
            SimpleNamespace(job_id="job-running", owner_id=owner, status="running"),
            SimpleNamespace(job_id="job-queued", owner_id=owner, status="queued"),
            SimpleNamespace(job_id="job-complete", owner_id=owner, status="completed"),
            SimpleNamespace(job_id="job-other-owner", owner_id="other", status="running"),
        ]

        count = backend.cancel_active_jobs_for_exit()

        self.assertEqual(count, 2)
        self.assertEqual(
            backend.service.cancel_job.call_args_list,
            [
                call("job-running", owner_id=owner),
                call("job-queued", owner_id=owner),
            ],
        )

    def test_close_can_request_cancellation_before_source_shutdown(self):
        backend = object.__new__(ProductionBackend)
        backend.cancel_active_jobs_for_exit = Mock(return_value=1)
        with patch.object(mia_backend.SourceBackend, "close") as source_close:
            backend.close(cancel_jobs=True)

        backend.cancel_active_jobs_for_exit.assert_called_once_with()
        source_close.assert_called_once_with(backend)


class ResultExportErrorTests(unittest.TestCase):
    def test_no_matching_rows_survives_as_public_export_error_code(self):
        backend = object.__new__(ProductionBackend)
        backend.logger = Mock()
        with patch(
            "mia_source_results.export_results",
            side_effect=ValueError("result_export_empty"),
        ):
            result = backend.export_results({})

        self.assertEqual(result["error_code"], "result_export_empty")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["files"], [])

    def test_permission_failure_survives_as_public_export_error_code(self):
        backend = object.__new__(ProductionBackend)
        backend.logger = Mock()
        with patch(
            "mia_source_results.export_results",
            side_effect=PermissionError("private path detail must not cross IPC"),
        ):
            result = backend.export_results({})

        self.assertEqual(result["error_code"], "artifact_write_denied")
        self.assertNotIn("private path", str(result))


if __name__ == "__main__":
    unittest.main()
