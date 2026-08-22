"""Manual, non-CI benchmark for source vs desktop detail Excel rendering."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(REPOSITORY / "runtime" / "python"),
    str(REPOSITORY / "runtime" / "python" / "vendor" / "mia_crawl_service"),
]

import app.exporters.invoice_detail_excel_exporter as source_module
from app.exporters.invoice_detail_excel_exporter import InvoiceDetailExcelExporter
from mia_progressive_excel_exporter import ProgressiveInvoiceDetailExcelExporter
from mia_source_results import _ExcelSafeDetailRowBuilder, _source_template_dir


def _payload(number: int, line_count: int) -> dict:
    return {
        "detail": {
            "khmshdon": "1", "khhdon": "K26T", "shdon": str(number),
            "tdlap": "2026-08-01T08:00:00+07:00", "dvtte": "VND",
            "nbten": "Synthetic seller", "nmten": "Synthetic buyer",
            "tthai": 1, "ttxly": 5, "ttkhac": [],
            "hdhhdvu": [
                {
                    "ten": f"Synthetic item {index % 25}", "dvtinh": "Unit",
                    "sluong": 1, "dgia": 100, "tsuat": 8,
                    "thtien": 100, "tthue": 8, "tchat": 1,
                }
                for index in range(line_count)
            ],
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("source", "progressive"), required=True)
    parser.add_argument("--invoice-count", type=int, default=300)
    parser.add_argument("--lines-per-invoice", type=int, default=27)
    args = parser.parse_args()
    if args.invoice_count < 1 or args.lines_per_invoice < 1:
        parser.error("counts must be positive")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        records = []
        for number in range(1, args.invoice_count + 1):
            raw = root / f"detail-{number}.json"
            raw.write_text(
                json.dumps(_payload(number, args.lines_per_invoice)),
                encoding="utf-8",
            )
            records.append({
                "raw_detail_path": str(raw), "shdon": str(number),
                "khmshdon": "1", "khhdon": "K26T",
                "material_codes_json": "[]",
            })

        output = root / f"{args.mode}.xlsx"
        width_calls = 0
        original_width = source_module._display_width

        def measured_width(value, **kwargs):
            nonlocal width_calls
            width_calls += 1
            return original_width(value, **kwargs)

        source_module._display_width = measured_width
        try:
            exporter = (
                InvoiceDetailExcelExporter(
                    _source_template_dir() / "invoice_detail.xlsx",
                    _ExcelSafeDetailRowBuilder(),
                )
                if args.mode == "source"
                else ProgressiveInvoiceDetailExcelExporter(
                    _source_template_dir() / "invoice_detail.xlsx",
                    _ExcelSafeDetailRowBuilder(),
                    progress=lambda *_args: None,
                )
            )
            started = time.perf_counter()
            summary = exporter.export(
                records, output, "2026-08-01", "2026-08-31"
            )
            duration = time.perf_counter() - started
        finally:
            source_module._display_width = original_width

        print(json.dumps({
            "mode": args.mode,
            "invoice_count": summary["invoice_count"],
            "generated_row_count": summary["row_count"],
            "column_count": 38,
            "display_width_calls": width_calls,
            "duration_seconds": round(duration, 6),
            "output_bytes": output.stat().st_size,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
