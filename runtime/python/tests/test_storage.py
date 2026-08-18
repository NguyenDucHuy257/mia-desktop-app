import logging
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mia_logging import configure_logging
from mia_storage import SCHEMA_VERSION, Storage, StorageError


class StorageTests(unittest.TestCase):
    def test_creates_and_reopens_current_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "mia.sqlite3")
            self.assertEqual(storage.initialize(), {"schema_version": SCHEMA_VERSION, "integrity": "ok"})
            self.assertEqual(storage.initialize(), {"schema_version": SCHEMA_VERSION, "integrity": "ok"})
            self.assertEqual(storage.status()["integrity"], "ok")

    def test_migrates_an_existing_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mia.sqlite3"
            sqlite3.connect(database).close()
            self.assertEqual(Storage(database).initialize()["schema_version"], SCHEMA_VERSION)

    def test_reports_locked_and_corrupt_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mia.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("BEGIN EXCLUSIVE")
            with self.assertRaisesRegex(StorageError, "database_locked"):
                Storage(database).initialize()
            connection.rollback()
            connection.close()
            database.write_bytes(b"not a sqlite database")
            with self.assertRaisesRegex(StorageError, "database_corrupt"):
                Storage(database).initialize()

    def test_rolls_back_interrupted_transactions(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "mia.sqlite3"
            Storage(database).initialize()
            connection = sqlite3.connect(database)
            connection.execute("BEGIN")
            connection.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?)",
                ("account", "tax-code", b"encrypted", "unchecked", "now", "now"),
            )
            connection.close()
            with closing(sqlite3.connect(database)) as reopened:
                self.assertEqual(reopened.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)

    def test_redacts_credentials_and_tax_codes_from_rotating_log(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = configure_logging(Path(directory), "INFO")
            logger.info("password=unsafe token=unsafe 0100000000-001")
            for handler in logger.handlers:
                handler.flush()
            content = (Path(directory) / "runtime.log").read_text(encoding="utf-8")
            self.assertNotIn("unsafe", content)
            self.assertNotIn("0100000000-001", content)
            self.assertGreaterEqual(content.count("[REDACTED]"), 2)
            logging.shutdown()


if __name__ == "__main__":
    unittest.main()
