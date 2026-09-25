import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = RUNTIME_ROOT / "vendor" / "mia_crawl_service"
for path in (RUNTIME_ROOT, VENDOR_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.worker_runtime.handler import InvoiceCrawlTaskHandler
from app.worker_runtime.metrics import WorkerMetrics


class OverviewTaxableTotalEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tax_code = "0100000000"
        self.database = self.root / self.tax_code / "db" / "invoices.sqlite3"
        self.overview = InvoiceOverviewRepository(self.database)
        self.overview.upsert_items(
            company_tax_code=self.tax_code, direction="purchase",
            query_type="sco-query", invoice_category="cash_register",
            raw_json_path="", timestamp="2026-09-25T00:00:00+00:00",
            items=[{
                "nbmst": "0200000000", "khhdon": "M26T", "shdon": "7",
                "khmshdon": "2", "nlap": "2026-09-25",
                "_public_fields": {"tgtcthue": None, "tgtttbso": "999999"},
            }],
        )
        self.handler = InvoiceCrawlTaskHandler.__new__(InvoiceCrawlTaskHandler)
        self.handler.data_root = self.root
        self.handler.metrics = WorkerMetrics()
        self.handler._shutdown_requested = threading.Event()
        self.job = SimpleNamespace(company_tax_code=self.tax_code)
        self.payload = {
            "session_hash": "session", "direction": "purchase",
            "query_type": "sco-query", "date_from": "2026-09-25",
            "date_to": "2026-09-25",
        }

    def tearDown(self):
        self.temp.cleanup()

    def _save_detail(self, lines):
        InvoiceDetailRepository(self.database).replace_normalized_detail_success(
            company_tax_code=self.tax_code, direction="purchase",
            query_type="sco-query", invoice_category="cash_register",
            nbmst="0200000000", khhdon="M26T", shdon="7", khmshdon="2",
            nlap="2026-09-25", nlap_date="2026-09-25", raw_detail_path="",
            http_status=200, fetched_at="2026-09-25T00:01:00+00:00",
            lines=lines,
        )

    def _stored_total(self):
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                "SELECT value_json FROM invoice_overview_attributes "
                "WHERE field_name='tgtcthue'"
            ).fetchone()
        return json.loads(row[0])

    def test_reuses_cached_detail_and_never_uses_payment_total(self):
        self._save_detail([{"thtien": "100.25"}, {"thtien": "20.75"}])
        self.handler._fetch_detail_core = lambda *_args, **_kwargs: self.fail(
            "cached detail must not trigger an HTTP detail fetch"
        )

        warnings = self.handler._enrich_missing_taxable_totals(
            self.job, self.payload, SimpleNamespace(),
        )

        self.assertEqual(warnings, [])
        self.assertEqual(self._stored_total(), "121.00")
        self.assertNotEqual(self._stored_total(), "999999")

    def test_fetches_and_persists_detail_when_cache_is_missing(self):
        calls = []

        def fake_fetch(_job, detail_payload, _lease):
            calls.append(detail_payload["shdon"])
            self._save_detail([{"thtien": "70"}, {"thtien": None}, {"thtien": "30"}])

        self.handler._fetch_detail_core = fake_fetch
        warnings = self.handler._enrich_missing_taxable_totals(
            self.job, self.payload, SimpleNamespace(),
        )

        self.assertEqual(warnings, [])
        self.assertEqual(calls, ["7"])
        self.assertEqual(self._stored_total(), "100")
        detail = InvoiceDetailRepository(self.database).get_detail_by_invoice_key(
            self.tax_code, "purchase", "sco-query", "0200000000", "M26T", "7", "2",
        )
        self.assertTrue(detail["normalized_ready"])


if __name__ == "__main__":
    unittest.main()
