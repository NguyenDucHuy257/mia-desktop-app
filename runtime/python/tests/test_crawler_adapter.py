import json
import tempfile
import unittest
from unittest.mock import MagicMock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mia_crawler import CrawlCancelled, CrawlerCoordinator, verify_account
from mia_storage import Storage


class CrawlerAdapterTests(unittest.TestCase):
    def test_vendored_adaptive_paging_factory_is_importable(self):
        from app.config.crawl_config import CrawlConfig
        from app.crawlers.invoice_crawler import adaptive_paging_options_from_config
        self.assertTrue(callable(adaptive_paging_options_from_config))
        self.assertTrue(callable(CrawlConfig.from_env))

    def test_account_verification_returns_only_company_name(self):
        session_type = MagicMock()
        session = session_type.return_value
        session.get_company_info.return_value = {"name": "Synthetic Company", "token": "must-not-return"}
        result = verify_account("0100000000", "synthetic-password", session_type)
        self.assertEqual(result, {"company_name": "Synthetic Company"})
        session.login.assert_called_once_with()
        self.assertNotIn("token", result)

    def test_account_verification_revalidates_credentials_at_runtime_boundary(self):
        import mia_runtime
        with self.assertRaises(mia_runtime.RpcError) as raised:
            mia_runtime.dispatch("crawler.verify_account", {"username": "../unsafe", "password": ""})
        self.assertEqual(raised.exception.message, "invalid_params")

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
