import json
import tempfile
import unittest
from pathlib import Path
from openpyxl import load_workbook

from mia_detail_excel_exporter import (
    CustomerDetailExcelExporter,
    DETAIL_EXPORT_COLUMNS,
    DETAIL_TEMPLATE_WIDTHS,
)
from app.exporters.invoice_detail_excel_exporter import InvoiceDetailExcelExporter
from mia_progressive_excel_exporter import ProgressiveInvoiceDetailExcelExporter
from mia_source_results import (
    _ExcelSafeDetailRowBuilder,
    _ExportProgressReporter,
    _combine_detail_workbooks_atomically,
    _combine_source_workbooks_atomically,
    _source_template_dir,
    _write_overview_excel_from_source_template,
)


def detail_payload(number: str, products: int = 2) -> dict:
    return {
        "detail": {
            "khmshdon": "1", "khhdon": "K26T", "shdon": number,
            "tdlap": "2026-08-01T08:00:00+07:00", "dvtte": "VND",
            "nbten": "Người bán", "nbmst": "0100000000",
            "nmten": "Người mua", "nmmst": "0200000000",
            "tthai": 1, "ttxly": 5, "ttkhac": [],
            "url": "https" + "://example.test/invoice/" + number,
            "hdhhdvu": [
                {
                    "ten": f"Hàng hóa {index}", "dvtinh": "Cái",
                    "sluong": 1, "dgia": 100, "tsuat": 8,
                    "thtien": 100, "tthue": 8, "tchat": 1,
                }
                for index in range(products)
            ],
        }
    }


class ExcelExportProgressTests(unittest.TestCase):
    def _records(self, root: Path) -> list[dict]:
        records = []
        for number in ("1", "2"):
            path = root / f"detail-{number}.json"
            path.write_text(json.dumps(detail_payload(number), ensure_ascii=False), encoding="utf-8")
            records.append({
                "raw_detail_path": str(path), "nbmst": "0100000000",
                "khhdon": "K26T", "shdon": number, "khmshdon": "1",
                "nlap": "2026-08-01T08:00:00+07:00",
                "material_codes_json": "[]",
            })
        return records

    def test_progressive_detail_export_matches_customer_legacy_workbook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = self._records(root)
            progressive_path = root / "progressive.xlsx"
            template = _source_template_dir() / "invoice_detail.xlsx"
            events = []

            def progress(phase, processed, total):
                if phase == "save" and processed == total == 1:
                    self.assertTrue(progressive_path.is_file())
                events.append((phase, processed, total))

            ProgressiveInvoiceDetailExcelExporter(
                template,
                _ExcelSafeDetailRowBuilder(),
                progress=progress,
            ).export(records, progressive_path, "2026-08-01", "2026-08-31")

            progressive_book = load_workbook(progressive_path, data_only=False)
            try:
                sheet = progressive_book.active
                expected_headers = [title for _key, title in DETAIL_EXPORT_COLUMNS]
                actual_headers = [sheet.cell(1, column).value for column in range(1, 38)]
                self.assertEqual(sheet.max_column, 37)
                self.assertEqual(actual_headers, expected_headers)
                self.assertNotIn("STT", actual_headers)
                self.assertEqual(sheet["A1"].value, "Mẫu số HD")
                self.assertEqual(sheet["AK1"].value, "Hạn dùng")
                self.assertEqual(sheet["A2"].value, "1")
                self.assertEqual(sheet["B2"].value, "K26T")
                self.assertIsInstance(sheet["S2"].value, (int, float))
                self.assertEqual(sheet["D2"].value, "01/08/2026")
                self.assertEqual(sheet.max_row, 5)
                self.assertFalse(sheet.merged_cells.ranges)
                self.assertIsNone(sheet.auto_filter.ref)
                for column, expected_width in enumerate(DETAIL_TEMPLATE_WIDTHS, start=1):
                    letter = sheet.cell(1, column).column_letter
                    self.assertAlmostEqual(
                        sheet.column_dimensions[letter].width,
                        expected_width,
                        places=6,
                    )
                self.assertEqual(
                    sheet["AD2"].hyperlink.target,
                    "https" + "://example.test/invoice/1",
                )
            finally:
                progressive_book.close()

            self.assertIn(("build_rows", 2, 2), events)
            self.assertIn(("write_rows", 4, 4), events)
            self.assertIn(("format", 37, 37), events)
            self.assertIn(("save", 1, 1), events)

    def test_detail_widths_do_not_change_for_long_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = detail_payload("000001", products=1)
            payload["detail"]["nbten"] = "Tên người bán rất dài " * 20
            raw_path = root / "detail.json"
            raw_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            output = root / "detail.xlsx"
            CustomerDetailExcelExporter(
                _source_template_dir() / "invoice_detail.xlsx",
                _ExcelSafeDetailRowBuilder(),
            ).export(
                [{
                    "raw_detail_path": str(raw_path), "nbmst": "0100000000",
                    "khhdon": "K26T", "shdon": "000001", "khmshdon": "1",
                    "nlap": "2026-08-01T08:00:00+07:00",
                    "material_codes_json": "[]",
                }],
                output,
                "2026-08-01",
                "2026-08-31",
            )
            workbook = load_workbook(output, data_only=False)
            try:
                sheet = workbook.active
                self.assertAlmostEqual(sheet.column_dimensions["J"].width, DETAIL_TEMPLATE_WIDTHS[9])
                self.assertIsNone(sheet.row_dimensions[2].height)
                self.assertFalse(sheet["J2"].alignment.wrap_text)
            finally:
                workbook.close()

    def test_source_combiner_preserves_detail_sheet_values_styles_and_merges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = self._records(root)
            template = _source_template_dir() / "invoice_detail.xlsx"
            electronic = root / "electronic.xlsx"
            cash = root / "cash.xlsx"
            combined = root / "combined.xlsx"
            for target in (electronic, cash):
                InvoiceDetailExcelExporter(
                    template, _ExcelSafeDetailRowBuilder()
                ).export(records, target, "2026-08-01", "2026-08-31")

            _combine_source_workbooks_atomically(
                [
                    ("purchase", "electronic", electronic),
                    ("purchase", "Máy tính tiền", cash),
                ],
                combined,
            )

            combined_book = load_workbook(combined, data_only=False)
            try:
                self.assertEqual(
                    combined_book.sheetnames,
                    ["Hóa đơn điện tử", "Máy tính tiền"],
                )
                for source_path, sheet_name in (
                    (electronic, "Hóa đơn điện tử"),
                    (cash, "Máy tính tiền"),
                ):
                    source_book = load_workbook(source_path, data_only=False)
                    try:
                        source_sheet = source_book.active
                        target_sheet = combined_book[sheet_name]
                        self.assertEqual(
                            {str(value) for value in source_sheet.merged_cells.ranges},
                            {str(value) for value in target_sheet.merged_cells.ranges},
                        )
                        for row in range(1, source_sheet.max_row + 1):
                            for column in range(1, source_sheet.max_column + 1):
                                source_cell = source_sheet.cell(row, column)
                                target_cell = target_sheet.cell(row, column)
                                self.assertEqual(source_cell.value, target_cell.value)
                                if source_cell.value is None:
                                    continue
                                message = f"{sheet_name}!R{row}C{column}"
                                self.assertEqual(
                                    (
                                        source_cell.font.name,
                                        source_cell.font.sz,
                                        source_cell.font.b,
                                        source_cell.font.i,
                                        source_cell.font.color.type if source_cell.font.color else None,
                                        source_cell.font.color.rgb if source_cell.font.color and source_cell.font.color.type == "rgb" else None,
                                    ),
                                    (
                                        target_cell.font.name,
                                        target_cell.font.sz,
                                        target_cell.font.b,
                                        target_cell.font.i,
                                        target_cell.font.color.type if target_cell.font.color else None,
                                        target_cell.font.color.rgb if target_cell.font.color and target_cell.font.color.type == "rgb" else None,
                                    ),
                                    message,
                                )
                                self.assertEqual(
                                    (source_cell.fill.fill_type, source_cell.fill.fgColor.type, source_cell.fill.fgColor.rgb),
                                    (target_cell.fill.fill_type, target_cell.fill.fgColor.type, target_cell.fill.fgColor.rgb),
                                    message,
                                )
                                self.assertEqual(
                                    tuple(getattr(source_cell.border, side).style for side in ("left", "right", "top", "bottom")),
                                    tuple(getattr(target_cell.border, side).style for side in ("left", "right", "top", "bottom")),
                                    message,
                                )
                                self.assertEqual(
                                    (
                                        source_cell.alignment.horizontal,
                                        source_cell.alignment.vertical,
                                        source_cell.alignment.wrap_text,
                                    ),
                                    (
                                        target_cell.alignment.horizontal,
                                        target_cell.alignment.vertical,
                                        target_cell.alignment.wrap_text,
                                    ),
                                    message,
                                )
                                self.assertEqual(
                                    source_cell.number_format,
                                    target_cell.number_format,
                                    message,
                                )
                    finally:
                        source_book.close()
            finally:
                combined_book.close()

    def test_customer_detail_combiner_keeps_one_sheet_and_one_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = self._records(root)
            template = _source_template_dir() / "invoice_detail.xlsx"
            electronic = root / "electronic.xlsx"
            cash = root / "cash.xlsx"
            combined = root / "combined.xlsx"
            for target in (electronic, cash):
                CustomerDetailExcelExporter(
                    template, _ExcelSafeDetailRowBuilder()
                ).export(records, target, "2026-08-01", "2026-08-31")

            _combine_detail_workbooks_atomically(
                [
                    ("purchase", "electronic", electronic),
                    ("purchase", "cash_register", cash),
                ],
                combined,
            )

            workbook = load_workbook(combined, data_only=False)
            try:
                sheet = workbook.active
                self.assertEqual(workbook.sheetnames, ["Sheet1"])
                self.assertEqual(sheet.max_column, 37)
                self.assertEqual(sheet.max_row, 9)
                self.assertEqual(
                    [sheet.cell(1, column).value for column in range(1, 38)],
                    [title for _key, title in DETAIL_EXPORT_COLUMNS],
                )
                self.assertEqual(sheet["A2"].value, "1")
                self.assertEqual(sheet["A6"].value, "1")
                self.assertFalse(sheet.merged_cells.ranges)
            finally:
                workbook.close()

    def test_reporter_events_are_monotonic_and_terminal(self):
        events = []
        reporter = _ExportProgressReporter(events.append)
        reporter.planning(1, 2)
        reporter.planning(2, 2)
        reporter.set_units(2)
        reporter.start_unit("overview")
        reporter.unit("write_rows", 2, 10)
        reporter.unit("save", 1, 1)
        reporter.complete_unit()
        reporter.start_unit("details")
        reporter.unit("build_rows", 1, 2)
        reporter.unit("format", 37, 37)
        reporter.unit("save", 1, 1)
        reporter.complete_unit()
        reporter.complete()
        percents = [event["percent"] for event in events]
        self.assertEqual(percents[-1], 100)
        self.assertTrue(all(0 <= value <= 100 for value in percents))
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(events[-1]["status"], "completed")

        failed = []
        failure_reporter = _ExportProgressReporter(failed.append)
        failure_reporter.planning(1, 2)
        failure_reporter.failed()
        self.assertEqual(failed[-1]["status"], "failed")
        self.assertLess(failed[-1]["percent"], 100)

    def test_overview_emits_actual_rows_and_finishes_after_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "overview.xlsx"
            events = []

            def progress(phase, processed, total):
                if phase == "save" and processed == total == 1:
                    self.assertTrue(output.is_file())
                events.append((phase, processed, total))

            _write_overview_excel_from_source_template(
                [{
                    "khmshdon": "1", "khhdon": "K26T", "shdon": "1",
                    "tdlap": "2026-08-01T08:00:00+07:00", "tthai": 1,
                }],
                direction="purchase",
                category="electronic",
                date_from="2026-08-01",
                date_to="2026-08-31",
                target=output,
                progress=progress,
            )
            self.assertIn(("load_template", 1, 1), events)
            self.assertIn(("write_rows", 1, 1), events)
            self.assertEqual(events[-1], ("save", 1, 1))


if __name__ == "__main__":
    unittest.main()
