import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mia_local_job_repository import LocalSequentialJobRepository
from mia_local_worker import LocalWorkerLoop


class LocalDesktopArchitectureTests(unittest.TestCase):
    def test_local_job_repository_has_no_web_worker_slot_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "source-control.sqlite3"
            repository = LocalSequentialJobRepository(database)
            repository.migrate()

            with closing(sqlite3.connect(database)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }

            self.assertIn("crawl_jobs", tables)
            self.assertNotIn("worker_slots", tables)
            self.assertNotIn("account_execution_leases", tables)
            self.assertNotIn("account_execution_fences", tables)
            self.assertNotIn("api_request_audit", tables)

    def test_backend_injects_one_named_local_worker_and_local_dtos(self):
        import mia_backend
        import mia_source_backend
        import app.external_api.models as source_models

        self.assertEqual(mia_source_backend.WORKER_ID, "desktop-local-worker")
        repository = mia_source_backend.create_job_engine_repository(
            sqlite_path=Path(tempfile.gettempdir()) / "mia-local-shape.sqlite3"
        )
        self.assertIsInstance(repository, LocalSequentialJobRepository)
        self.assertIs(mia_source_backend.WorkerLoop, LocalWorkerLoop)
        self.assertEqual(source_models.__name__, "mia_local_source_models")

    def test_local_worker_loop_drives_only_one_supervisor(self):
        stop_event = threading.Event()
        repository = Mock()
        supervisor = Mock()
        supervisor.worker_id = "desktop-local-worker"
        supervisor.repository = repository
        supervisor.run_once.return_value = SimpleNamespace(job=None)

        iterations = LocalWorkerLoop(
            supervisor,
            idle_backoff_seconds=0.001,
            error_backoff_seconds=0.001,
            orphan_scan_seconds=0.001,
        ).run(stop_event, max_iterations=1)

        self.assertEqual(iterations, 1)
        supervisor.set_stop_event.assert_called_once_with(stop_event)
        supervisor.run_once.assert_called_once_with()
        repository.recover_expired_leases.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
