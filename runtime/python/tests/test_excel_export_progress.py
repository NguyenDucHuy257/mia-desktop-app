import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.exporters.invoice_detail_excel_exporter as source_module
from app.exporters.invoice_detail_excel_exporter import InvoiceDetailExcelExporter
from openpyxl import load_workbook

from mia_progressive_excel_exporter import ProgressiveInvoiceDetailExcelExporter
from mia_source_results import (
    _DETAIL_EXPORT_COLUMNS,
    _DETAIL_EXPORT_WIDTHS,
    _ExcelSafeDetailRowBuilder,
    _ExportProgressReporter,
    _combine_source_workbooks_atomically,
    _normalize_detail_excel_atomically,
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

    def test_progressive_detail_export_preserves_source_workbook_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = self._records(root)
            source_path = root / "source.xlsx"
            progressive_path = root / "progressive.xlsx"
            template = _source_template_dir() / "invoice_detail.xlsx"
            InvoiceDetailExcelExporter(template, _ExcelSafeDetailRowBuilder()).export(
                records, source_path, "2026-08-01", "2026-08-31"
            )
            events = []
            original_width = source_module._display_width
            width_calls = 0

            def measured_width(value, **kwargs):
                nonlocal width_calls
                width_calls += 1
                return original_width(value, **kwargs)

            def progress(phase, processed, total):
                if phase == "save" and processed == total == 1:
                    self.assertTrue(progressive_path.is_file())
                events.append((phase, processed, total))

            with patch.object(source_module, "_display_width", side_effect=measured_width):
                ProgressiveInvoiceDetailExcelExporter(
                    template,
                    _ExcelSafeDetailRowBuilder(),
                    progress=progress,
                ).export(records, progressive_path, "2026-08-01", "2026-08-31")

            source_book = load_workbook(source_path, data_only=False)
            progressive_book = load_workbook(progressive_path, data_only=False)
            try:
                source_sheet = source_book.active
                progressive_sheet = progressive_book.active
                self.assertEqual(source_sheet.max_row, progressive_sheet.max_row)
                self.assertEqual(source_sheet.max_column, progressive_sheet.max_column)
                self.assertEqual(
                    {str(value) for value in source_sheet.merged_cells.ranges},
                    {str(value) for value in progressive_sheet.merged_cells.ranges},
                )
                nonempty = 0
                for row in range(1, source_sheet.max_row + 1):
                    self.assertEqual(
                        source_sheet.row_dimensions[row].height,
                        progressive_sheet.row_dimensions[row].height,
                    )
                    for column in range(1, source_sheet.max_column + 1):
                        source_cell = source_sheet.cell(row, column)
                        progressive_cell = progressive_sheet.cell(row, column)
                        self.assertEqual(source_cell.value, progressive_cell.value)
                        self.assertEqual(source_cell._style, progressive_cell._style)
                        if row > 5 and source_cell.value not in (None, ""):
                            nonempty += 1
                for column in range(1, source_sheet.max_column + 1):
                    letter = source_sheet.cell(1, column).column_letter
                    self.assertAlmostEqual(
                        source_sheet.column_dimensions[letter].width or 0,
                        progressive_sheet.column_dimensions[letter].width or 0,
                        places=4,
                    )
                    self.assertEqual(
                        source_sheet.column_dimensions[letter].hidden,
                        progressive_sheet.column_dimensions[letter].hidden,
                    )
            finally:
                source_book.close()
                progressive_book.close()

            self.assertLessEqual(width_calls, nonempty)
            self.assertIn(("build_rows", 2, 2), events)
            self.assertIn(("write_rows", 4, 4), events)
            self.assertIn(("format", 38, 38), events)
            self.assertIn(("save", 1, 1), events)

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

    def test_detail_finalizer_matches_customer_fixed_37_column_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = self._records(root)
            payload = detail_payload("1")
            payload["detail"]["nbten"] = "N" * 200
            payload["detail"]["urltracuu"] = "https" + "://invoice.example/lookup"
            Path(records[0]["raw_detail_path"]).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
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
            _normalize_detail_excel_atomically(combined)

            workbook = load_workbook(combined, data_only=False)
            try:
                worksheet = workbook.active
                expected_headers = [label for _key, label in _DETAIL_EXPORT_COLUMNS]
                self.assertEqual(workbook.sheetnames, ["Sheet1"])
                self.assertEqual(worksheet.max_column, 37)
                self.assertEqual(worksheet.max_row, 9)
                self.assertEqual(
                    [worksheet.cell(1, column).value for column in range(1, 38)],
                    expected_headers,
                )
                self.assertEqual(worksheet["A1"].value, "Mẫu số HD")
                self.assertEqual(worksheet["AK1"].value, "Hạn dùng")
                self.assertNotIn("STT", expected_headers)
                self.assertEqual(worksheet["J2"].value, "N" * 200)
                self.assertIsInstance(worksheet["S2"].value, (int, float))
                self.assertIsInstance(worksheet["W2"].value, (int, float))
                self.assertEqual(worksheet["A1"].fill.fgColor.rgb, "FFFFFF00")
                self.assertEqual(worksheet["A1"].font.sz, 12)
                self.assertEqual(worksheet["A2"].font.sz, 12)
                self.assertFalse(worksheet.merged_cells.ranges)
                self.assertIsNone(worksheet.freeze_panes)
                self.assertIsNone(worksheet.auto_filter.ref)
                for column, expected_width in enumerate(
                    _DETAIL_EXPORT_WIDTHS, start=1
                ):
                    letter = worksheet.cell(1, column).column_letter
                    self.assertAlmostEqual(
                        worksheet.column_dimensions[letter].width,
                        expected_width,
                        places=4,
                    )
                for row in range(1, worksheet.max_row + 1):
                    self.assertIsNone(worksheet.row_dimensions[row].height)
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
        reporter.unit("format", 38, 38)
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
