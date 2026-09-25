import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOT = RUNTIME_ROOT / "vendor" / "mia_crawl_service"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from mia_source_results import (
    _combined_overview_schema,
    _detail_template_schema,
    _overview_display_value,
    _overview_template_schema,
    _safe_fields,
)
from app.services.overview_downloader import _cash_register_buyer_name, _processing_result
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.repositories.invoice_detail_repository import InvoiceDetailRepository


class SourceResultTemplateTests(unittest.TestCase):
    def test_overview_check_result_uses_processing_status_for_all_invoice_types(self):
        self.assertEqual(
            _overview_display_value(
                {"ttxly": 5}, "kqcht",
                category="electronic", direction="purchase",
            ),
            _processing_result({"ttxly": 5}),
        )
        self.assertEqual(
            _overview_display_value(
                {}, "kqcht", category="cash_register", direction="sold",
            ),
            "Cục Thuế đã nhận hóa đơn có mã khởi tạo từ máy tính tiền",
        )

    def test_overview_display_does_not_use_payment_total_for_missing_taxable_total(self):
        self.assertEqual(
            _overview_display_value(
                {"khmshdon": "2", "tgtcthue": "", "tgtttbso": 235000},
                "tgtcthue", category="electronic", direction="purchase",
            ),
            "",
        )

    def test_overview_processing_result_falls_back_to_processing_status(self):
        self.assertEqual(_processing_result({"ttxly": 0}), "Tổng cục Thuế đã nhận")
        self.assertEqual(_processing_result({"ttxly": "5"}), "Đã cấp mã hóa đơn")
        self.assertEqual(
            _processing_result({"kqcht": "Kết quả nguồn", "ttxly": 0}),
            "Kết quả nguồn",
        )
        self.assertEqual(_processing_result({}), "")

    def test_cash_register_sold_buyer_name_uses_invoice_information_fallback(self):
        self.assertEqual(
            _cash_register_buyer_name({
                "nmten": "",
                "nmtnmua": "Tên người mua: Bán cho người tiêu dùng",
            }),
            "Bán cho người tiêu dùng",
        )
        self.assertEqual(
            _cash_register_buyer_name({
                "nmten": "Khách hàng trên hóa đơn",
                "nmtnmua": "Thông tin dự phòng",
            }),
            "Khách hàng trên hóa đơn",
        )

    def test_direct_invoice_keeps_taxable_total_blank_until_detail_is_available(self):
        fields = _safe_fields({
            "khmshdon": 2,
            "tgtcthue": None,
            "tgtttbso": 67864,
        })
        self.assertIsNone(fields["tgtcthue"])

    def test_detail_lines_supply_missing_taxable_total_without_payment_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "invoices.sqlite3"
            repository = InvoiceOverviewRepository(database)
            repository.upsert_items(
                company_tax_code="0100000000",
                direction="sold",
                query_type="query",
                invoice_category="electronic",
                raw_json_path="",
                items=[{
                    "nbmst": "0100000000", "khhdon": "C26", "shdon": "1",
                    "khmshdon": "2", "nlap": "2026-09-01",
                    "_public_fields": {
                        "khmshdon": "2", "tgtcthue": None, "tgtttbso": 67864,
                    },
                }],
                timestamp="2026-09-13T00:00:00+00:00",
            )
            detail_repository = InvoiceDetailRepository(database)
            detail_repository.replace_normalized_detail_success(
                company_tax_code="0100000000", direction="sold",
                query_type="query", invoice_category="electronic",
                nbmst="0100000000", khhdon="C26", shdon="1",
                khmshdon="2", nlap="2026-09-01", nlap_date="2026-09-01",
                raw_detail_path="", http_status=200,
                fetched_at="2026-09-13T00:00:00+00:00",
                lines=[{"thtien": "50000.25"}, {"thtien": "10000.75"}],
            )
            total = detail_repository.taxable_total_by_invoice_key(
                "0100000000", "sold", "query", "0100000000", "C26", "1", "2",
            )
            self.assertEqual(str(total), "60001.00")
            missing = repository.get_items_missing_taxable_total(
                company_tax_code="0100000000", direction="sold", query_type="query",
                from_date="2026-09-01", to_date="2026-09-01",
            )
            self.assertEqual(len(missing), 1)
            repository.set_taxable_total_from_detail(missing[0]["id"], str(total))
            connection = sqlite3.connect(database)
            try:
                value_json = connection.execute(
                    "SELECT value_json FROM invoice_overview_attributes "
                    "WHERE field_name='tgtcthue'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(json.loads(value_json), "60001.00")

    def test_combined_overview_schema_contains_columns_from_both_sources(self):
        purchase_keys = [key for key, _label in _combined_overview_schema("purchase")]
        sold_keys = [key for key, _label in _combined_overview_schema("sold")]
        self.assertIn("nbdchi", purchase_keys)
        self.assertIn("nmcmnd", purchase_keys)
        self.assertIn("nmcmnd", sold_keys)
        self.assertIn("dvtte", sold_keys)
        self.assertIn("tgtphi", sold_keys)

    def test_electronic_overview_schema_comes_from_source_template(self):
        schema = _overview_template_schema("electronic", "purchase")
        self.assertEqual(schema[0][0], "stt")
        self.assertEqual(schema[0][1].strip().upper(), "STT")
        self.assertEqual(len(schema), 19)
        self.assertEqual(
            [key for key, _ in schema[:5]],
            ["stt", "khmshdon", "khhdon", "shdon", "tdlap"],
        )
        self.assertTrue(all(title.strip() for _, title in schema))

    def test_cash_register_direction_templates_keep_exact_source_shape(self):
        purchase = _overview_template_schema("cash_register", "purchase")
        sold = _overview_template_schema("cash_register", "sold")
        self.assertEqual(len(purchase), 17)
        self.assertEqual(len(sold), 17)
        self.assertEqual(purchase[0][1].strip().upper(), "STT")
        self.assertEqual(sold[0][1].strip().upper(), "STT")
        self.assertIn("nbdchi", [key for key, _ in purchase])
        self.assertIn("nmdchi", [key for key, _ in sold])

    def test_detail_schema_matches_the_prepared_source_export_workbook(self):
        schema = _detail_template_schema()
        self.assertEqual(len(schema), 38)
        self.assertEqual(schema[0][0], "stt")
        self.assertEqual(schema[0][1].strip().upper(), "STT")
        self.assertEqual(schema[1][1].strip(), "Mẫu số HD")
        self.assertTrue(all(title.strip() for _, title in schema))
        self.assertFalse(any(key.startswith("raw_") or key.endswith("_path") for key, _ in schema))


if __name__ == "__main__":
    unittest.main()
