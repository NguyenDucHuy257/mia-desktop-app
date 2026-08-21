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

    def test_migrate_retires_legacy_uuid_jobs_without_touching_source_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "source-control.sqlite3"
            repository = LocalSequentialJobRepository(database)
            repository.migrate()
            timestamp = "2026-08-21T12:00:00.000000+00:00"

            with closing(sqlite3.connect(database)) as connection:
                for job_id, account_key, status, worker_id, lease_token in (
                    ("legacy-job", "d965453c-1ceb-44d0-9157-3707fc6d9571", "running", "desktop-local-worker", "legacy-lease"),
                    ("source-job", "conn_e9c844dd097a438e8b0974819e84c092", "queued", None, None),
                ):
                    connection.execute(
                        """
                        INSERT INTO crawl_jobs (
                            job_id, account_key, company_tax_code, job_type,
                            queue_order, parameters_json, status, current_stage,
                            worker_id, lease_token, lease_started_at,
                            lease_expires_at, available_at, created_at, updated_at,
                            pipeline_version, progress_state, progress_updated_at
                        ) VALUES (?, ?, '0100000000', 'invoice_crawl', 1, '{}', ?,
                                  'detail', ?, ?, ?, ?, ?, ?, ?, 2, '{}', ?)
                        """,
                        (
                            job_id,
                            account_key,
                            status,
                            worker_id,
                            lease_token,
                            timestamp if worker_id else None,
                            timestamp if worker_id else None,
                            timestamp,
                            timestamp,
                            timestamp,
                            timestamp,
                        ),
                    )
                connection.commit()

            repository.migrate()

            with closing(sqlite3.connect(database)) as connection:
                connection.row_factory = sqlite3.Row
                legacy = connection.execute(
                    "SELECT status, worker_id, lease_token, last_error_code FROM crawl_jobs WHERE job_id = 'legacy-job'"
                ).fetchone()
                source = connection.execute(
                    "SELECT status, worker_id, lease_token, last_error_code FROM crawl_jobs WHERE job_id = 'source-job'"
                ).fetchone()

            self.assertEqual(legacy["status"], "cancelled")
            self.assertIsNone(legacy["worker_id"])
            self.assertIsNone(legacy["lease_token"])
            self.assertEqual(legacy["last_error_code"], "desktop_legacy_job_retired")
            self.assertEqual(source["status"], "queued")
            self.assertIsNone(source["last_error_code"])

    def test_local_repository_rejects_new_legacy_account_jobs(self):
        repository = LocalSequentialJobRepository(Path(tempfile.gettempdir()) / "mia-local-reject.sqlite3")
        request = SimpleNamespace(account_key="legacy-account-id")
        with self.assertRaisesRegex(ValueError, "source_connection_required"):
            repository.create_admitted_job(request)

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
