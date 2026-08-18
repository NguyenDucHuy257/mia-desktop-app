import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mia_artifacts import ArtifactExporter, safe_excel_value
from mia_storage import Storage


class ArtifactExporterTests(unittest.TestCase):
    def test_excel_preserves_unicode_blocks_formula_injection_and_suffixes_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root / "mia.sqlite3")
            storage.initialize()
            storage.create_account({"account_id": "account-1", "tax_code": "0101234567", "encrypted_password": "cipher", "timestamp": "now"})
            storage.import_overviews({"connection_id": "account-1", "timestamp": "now", "items": [{
                "direction": "purchase", "business_key": "=MỞ-CALC", "payload": {"company": "Công ty Việt Nam"},
            }]})
            destination = root / "output"
            exporter = ArtifactExporter(storage, root)
            first = exporter.export({"destination": str(destination), "connection_ids": ["account-1"], "kinds": ["excel"]})
            second = exporter.export({"destination": str(destination), "connection_ids": ["account-1"], "kinds": ["excel"]})
            self.assertEqual(first["count"], 1)
            self.assertIn(" (1).xlsx", second["files"][0])
            workbook = load_workbook(first["files"][0], read_only=True)
            values = list(workbook["Tong quan"].values)
            self.assertEqual(values[1][2], "'=MỞ-CALC")
            self.assertIn("Công ty Việt Nam", values[1][3])
            workbook.close()

    def test_rejects_relative_destination_invalid_kind_and_duplicate_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root / "mia.sqlite3")
            storage.initialize()
            exporter = ArtifactExporter(storage, root)
            with self.assertRaisesRegex(ValueError, "invalid_artifact_directory"):
                exporter.export({"destination": "relative", "connection_ids": ["a"], "kinds": ["xml"]})
            with self.assertRaisesRegex(ValueError, "invalid_artifact_kind"):
                exporter.export({"destination": str(root), "connection_ids": ["a"], "kinds": ["exe"]})
            with self.assertRaisesRegex(ValueError, "invalid_artifact_accounts"):
                exporter.export({"destination": str(root), "connection_ids": ["a", "a"], "kinds": ["xml"]})

    def test_safe_excel_value_covers_all_formula_prefixes(self):
        for prefix in ("=", "+", "-", "@"):
            self.assertTrue(safe_excel_value(prefix + "payload").startswith("'"))


if __name__ == "__main__":
    unittest.main()
