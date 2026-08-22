import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import mia_backend
import mia_runtime
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

    def test_fresh_runtime_session_cancels_stale_jobs_before_worker_start(self):
        previous = (
            mia_runtime.storage,
            mia_runtime.data_directory,
            mia_runtime.logger,
            mia_runtime.production_backend,
        )
        logger = Mock()
        try:
            with tempfile.TemporaryDirectory() as directory, patch(
                "mia_backend.cancel_stale_jobs_for_desktop_session",
                return_value=2,
            ) as reset_jobs, patch(
                "mia_runtime.configure_logging", return_value=logger
            ):
                result, should_stop = mia_runtime.dispatch(
                    "storage.initialize",
                    {
                        "data_dir": str(Path(directory)),
                        "reset_desktop_session": True,
                    },
                )
                self.assertFalse(should_stop)
                self.assertEqual(result["schema_version"], 4)
                reset_jobs.assert_called_once_with(Path(directory), logger)
        finally:
            (
                mia_runtime.storage,
                mia_runtime.data_directory,
                mia_runtime.logger,
                mia_runtime.production_backend,
            ) = previous


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


class ArtifactExportTaskTests(unittest.TestCase):
    def test_local_artifact_task_can_be_cancelled_over_json_rpc(self):
        previous = (
            mia_runtime.storage,
            mia_runtime.data_directory,
            mia_runtime._artifact_task,
        )
        entered = threading.Event()

        def copy_until_cancelled(_value, cancel_event):
            entered.set()
            self.assertTrue(cancel_event.wait(1))
            raise ValueError("artifact_cancelled")

        try:
            mia_runtime.storage = Mock()
            mia_runtime.data_directory = Path(tempfile.gettempdir())
            mia_runtime._artifact_task = None
            with patch.object(mia_runtime, "_copy_artifacts", side_effect=copy_until_cancelled):
                started, should_stop = mia_runtime.dispatch(
                    "artifacts.export.start",
                    {"destination": str(Path(tempfile.gettempdir())), "connection_ids": ["conn_1"], "kinds": ["xml"]},
                )
                self.assertFalse(should_stop)
                self.assertTrue(entered.wait(1))
                cancelled, _ = mia_runtime.dispatch(
                    "artifacts.export.cancel", {"task_id": started["task_id"]}
                )
                self.assertEqual(cancelled["status"], "cancelling")
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    status, _ = mia_runtime.dispatch(
                        "artifacts.export.status", {"task_id": started["task_id"]}
                    )
                    if status["status"] == "cancelled":
                        break
                    time.sleep(0.01)
                self.assertEqual(status["status"], "cancelled")
                self.assertEqual(status["error"], "artifact_cancelled")
        finally:
            mia_runtime.storage, mia_runtime.data_directory, mia_runtime._artifact_task = previous


if __name__ == "__main__":
    unittest.main()
