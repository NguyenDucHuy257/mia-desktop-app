import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mia_local_job_repository import LocalSequentialJobRepository


class LocalDesktopArchitectureTests(unittest.TestCase):
    def test_local_job_repository_has_no_web_worker_slot_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "source-control.sqlite3"
            repository = LocalSequentialJobRepository(database)
            repository.migrate()

            with sqlite3.connect(database) as connection:
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

    def test_backend_injects_one_named_local_worker(self):
        import mia_backend
        import mia_source_backend

        self.assertEqual(mia_source_backend.WORKER_ID, "desktop-local-worker")
        repository = mia_source_backend.create_job_engine_repository(
            sqlite_path=Path(tempfile.gettempdir()) / "mia-local-shape.sqlite3"
        )
        self.assertIsInstance(repository, LocalSequentialJobRepository)


if __name__ == "__main__":
    unittest.main()
