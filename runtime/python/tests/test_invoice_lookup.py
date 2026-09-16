import sys
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "mia_crawl_service"))

from mia_invoice_lookup import extract_lookup_code, is_safe_lookup_url, normalize_lookup_key, resolve_invoice_lookup, root_tax_code
from mia_source_results import _ExcelSafeDetailRowBuilder, _LookupEnrichedResultReader
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository

HTTPS = "https" + "://"
HTTP = "http" + "://"


class InvoiceLookupResolverTests(unittest.TestCase):
    def payload(self, **values):
        return {"detail": {"data_ct": {"hdhhdvu": [{"ten": "A"}], **values}}}

    def test_extracts_legacy_code_aliases_from_all_arrays(self):
        cases = [("cttkhac", "Số bảo mật", "0CED1B119E104CA2"), ("ttkhac", "Mã tra cứu hóa đơn", "Z2608031511507"), ("ttttkhac", "Matracuu", "ABC"), ("TTKhac", "ma_tra_cuu", "DEF"), ("cttkhac", "MãTraCứu", "GHI")]
        for array, name, expected in cases:
            with self.subTest(array=array, name=name):
                result = extract_lookup_code(self.payload(**{array: [{"ttruong": name, "dlieu": expected}]}))
                self.assertEqual(result, {"value": expected, "source": f"{array}.{name}"})

    def test_lookup_page_alias_is_not_a_code(self):
        self.assertIsNone(extract_lookup_code(self.payload(cttkhac=[{"ttruong": "Trangtracuu", "dlieu": HTTPS + "example.com"}])))

    def test_direct_invoice_url_has_highest_precedence(self):
        result = resolve_invoice_lookup(self.payload(nbmst="0105987432", urltracuu=HTTPS + "invoice.example/lookup", cttkhac=[{"ttruong": "Mã tra cứu", "dlieu": "A 1"}]))
        self.assertEqual((result.url, result.code, result.matched_by), (HTTPS + "invoice.example/lookup", "A 1", "invoice"))

    def test_easyinvoice_builds_encoded_direct_url(self):
        result = resolve_invoice_lookup(self.payload(nbmst="0105987432", mhdon="A/B 1"))
        self.assertEqual(result.url, HTTPS + "0105987432hd.easyinvoice.com.vn/Search/?strFkey=A%2FB%201")
        self.assertEqual(result.code, "A/B 1")

    def test_vnpt_template_and_seller_exact_override(self):
        template = resolve_invoice_lookup(self.payload(nbmst="0300514849-006", cttkhac=[{"ttruong": "Fkey", "dlieu": "ABC+1"}]))
        self.assertEqual(template.url, HTTPS + "snpmb-tt78.vnpt-invoice.com.vn/?strFkey=ABC%2B1")
        exact = resolve_invoice_lookup(self.payload(nbmst="0310471746", msttcgp="0105987432"))
        self.assertEqual((exact.url, exact.matched_by), (HTTPS + "hddt.bachhoaxanh.com", "seller_exact"))

    def test_seller_root_provider_and_provider_fallback(self):
        root = resolve_invoice_lookup(self.payload(nbmst="0104918404-999"))
        self.assertEqual(root.url, HTTPS + "hoadon.winmart.vn/")
        self.assertNotIn("serial=", root.url)
        provider = resolve_invoice_lookup(self.payload(nbmst="999", msttcgp="0312303803"))
        self.assertEqual(provider.url, HTTPS + "tracuu.wininvoice.vn")
        fallback = resolve_invoice_lookup(self.payload(nbmst="999", tvandnkntt="0312303803"))
        self.assertEqual((fallback.url, fallback.matched_by), (HTTPS + "tracuu.wininvoice.vn", "tvandnkntt"))

    def test_source_workbook_dynamic_and_root_rules(self):
        provider_template = resolve_invoice_lookup(self.payload(nbmst="0310000000", msttcgp="0100684378"))
        self.assertEqual(provider_template.url, HTTPS + "0310000000-tt78.vnpt-invoice.com.vn/")
        ajinomoto = resolve_invoice_lookup(self.payload(nbmst="3600244645-001"))
        self.assertEqual(ajinomoto.url, HTTPS + "ajinomotosg-tt78.vnpt-invoice.com.vn/")

    def test_manual_lookup_does_not_invent_code(self):
        result = resolve_invoice_lookup(self.payload(nbmst="0304741634", mhdon="SHOULD-NOT-BE-CODE"))
        self.assertEqual(result.mode, "manual")
        self.assertIsNone(result.code)

    def test_local_xml_guid_requires_no_network(self):
        result = resolve_invoice_lookup(self.payload(nbmst="999", msttcgp="0101360697"), available_xml='<HDon><DLHDon Id="GUID 123" /></HDon>')
        self.assertEqual((result.url, result.code), (HTTPS + "van.ehoadon.vn/Lookup?InvoiceGUID=GUID%20123", "GUID 123"))

    def test_malformed_and_local_urls_are_not_clickable(self):
        for value in ("javascript:alert(1)", "file:///tmp/a", HTTP + "127.0.0.1/test", HTTPS + "user:pass@example.com"):
            self.assertFalse(is_safe_lookup_url(value))
        self.assertTrue(is_safe_lookup_url(HTTPS + "example.com/a"))

    def test_normalizers(self):
        self.assertEqual(root_tax_code(" 0104918404-025 "), "0104918404")
        self.assertEqual(normalize_lookup_key("Mã_Tra-Cứu"), "matracuu")

    def test_suspicious_key_is_flagged_without_guessing(self):
        result = resolve_invoice_lookup(self.payload(nbmst="104918404"))
        self.assertIn("suspicious_tax_code", result.warnings)
        self.assertEqual(result.url, HTTPS + "hoadon.winmart.vn/")

    def test_multiple_detail_lines_share_one_invoice_lookup(self):
        payload = self.payload(nbmst="0310471746", cttkhac=[{"ttruong": "Số bảo mật", "dlieu": "CODE-1"}], hdhhdvu=[{"ten": "A"}, {"ten": "B"}])
        rows = _ExcelSafeDetailRowBuilder().build_rows(payload, {"nbmst": "0310471746"})
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["url"] for row in rows}, {HTTPS + "hddt.bachhoaxanh.com"})
        self.assertEqual({row["mk"] for row in rows}, {"CODE-1"})

    def test_normalized_existing_data_is_enriched_from_raw_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "invoices.sqlite3"
            raw = root / "detail.json"
            raw.write_text(json.dumps(self.payload(nbmst="0310471746", khhdon="AA", shdon="7", khmshdon="1", cttkhac=[{"ttruong": "Mã tra cứu", "dlieu": "OLD-DATA-CODE"}]), ensure_ascii=False), encoding="utf-8")
            timestamp = "2026-08-26T00:00:00+00:00"
            InvoiceOverviewRepository(database).upsert_items(
                company_tax_code="0100000000", direction="purchase", query_type="query", invoice_category="electronic",
                raw_json_path=root / "overview.json", items=[{"nbmst": "0310471746", "khhdon": "AA", "shdon": "7", "khmshdon": "1", "nlap": "2026-08-01", "nlap_date": "2026-08-01", "_public_fields": {}}], timestamp=timestamp,
            )
            InvoiceDetailRepository(database).replace_normalized_detail_success(
                company_tax_code="0100000000", direction="purchase", query_type="query", invoice_category="electronic",
                nbmst="0310471746", khhdon="AA", shdon="7", khmshdon="1", nlap="2026-08-01", nlap_date="2026-08-01",
                raw_detail_path=raw, http_status=200, fetched_at=timestamp,
                lines=[{"nbmst": "0310471746", "khhdon": "AA", "shdon": "7", "khmshdon": "1", "ten": "A", "url": "", "mk": ""}],
            )
            job = SimpleNamespace(company_tax_code="0100000000", parameters={"date_from": "2026-08-01", "date_to": "2026-08-31", "directions": ["purchase"], "query_types": ["query"]})
            result = _LookupEnrichedResultReader(database).detail_page(job, limit=50, cursor=None)
            self.assertEqual(result["items"][0]["url"], HTTPS + "hddt.bachhoaxanh.com")
            self.assertEqual(result["items"][0]["mk"], "OLD-DATA-CODE")


if __name__ == "__main__":
    unittest.main()
