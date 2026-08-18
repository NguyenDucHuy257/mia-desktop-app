import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mia_crawler import CrawlCancelled, CrawlerCoordinator
from mia_storage import Storage


class CrawlerAdapterTests(unittest.TestCase):
    def test_imports_vendored_overview_and_detail_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root / "mia.sqlite3")
            storage.initialize()
            storage.create_account({
                "account_id": "account-1", "tax_code": "synthetic-tax-code",
                "encrypted_password": "cipher", "timestamp": "now",
            })
            adapter = CrawlerCoordinator(storage, root)
            overview_dir = root / "crawler-data" / "synthetic-tax-code" / "raw" / "invoice_lists" / "purchase" / "query"
            overview_dir.mkdir(parents=True)
            payload = {"nbmst": "seller", "khhdon": "series", "shdon": 1, "khmshdon": 2, "value": "safe"}
            (overview_dir / "range.json").write_text(json.dumps({"datas": [payload, payload]}), encoding="utf-8")
            adapter._import_raw("account-1", "synthetic-tax-code", root / "crawler-data", "purchase", "query")
            self.assertEqual(len(storage.query_overviews({"connection_id": "account-1"})["items"]), 1)

            detail_dir = root / "crawler-data" / "synthetic-tax-code" / "raw" / "invoice_details" / "purchase" / "query" / "range"
            detail_dir.mkdir(parents=True)
            (detail_dir / "detail.json").write_text(json.dumps({
                "invoice_key": {"nbmst": "seller", "khhdon": "series", "shdon": 1, "khmshdon": 2},
                "detail": {"lines": [{"name": "item"}]},
            }), encoding="utf-8")
            adapter._import_details("account-1", "synthetic-tax-code", root / "crawler-data", "purchase", "query")
            self.assertEqual(len(storage.query_details({"connection_id": "account-1"})["items"]), 1)

    def test_cancel_unknown_worker_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root / "mia.sqlite3")
            storage.initialize()
            self.assertFalse(CrawlerCoordinator(storage, root).cancel("job_unknown"))

    def test_cancellation_escapes_vendor_item_level_exception_handlers(self):
        self.assertTrue(issubclass(CrawlCancelled, BaseException))
        self.assertFalse(issubclass(CrawlCancelled, Exception))


if __name__ == "__main__":
    unittest.main()
