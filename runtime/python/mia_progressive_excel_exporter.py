"""Desktop-only progress and performance adapter for source Excel rendering.

The vendored exporter remains authoritative for row construction and atomic
staging. This adapter observes its existing work units while the desktop's
Detail-only finalizer applies the fixed customer workbook format.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any, Callable


VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

import app.exporters.invoice_detail_excel_exporter as source_module
from mia_detail_excel_format import FixedDetailExcelExporter


logger = logging.getLogger("mia.excel_export")
ProgressCallback = Callable[[str, int, int], None]


class _ProgressRowBuilder:
    def __init__(self, delegate, owner: "ProgressiveInvoiceDetailExcelExporter") -> None:
        self.delegate = delegate
        self.owner = owner

    def build_rows(self, detail_payload, detail_record):
        started = time.perf_counter()
        try:
            rows = self.delegate.build_rows(detail_payload, detail_record)
            self.owner.generated_row_count += len(rows)
            return rows
        finally:
            self.owner.row_builder_seconds += time.perf_counter() - started
            self.owner.processed_invoice_count += 1
            emit_every = max(1, self.owner.invoice_total // 200)
            if (
                self.owner.processed_invoice_count == self.owner.invoice_total
                or self.owner.processed_invoice_count - self.owner._last_emitted_invoice >= emit_every
            ):
                self.owner._last_emitted_invoice = self.owner.processed_invoice_count
                self.owner._emit(
                    "build_rows",
                    self.owner.processed_invoice_count,
                    self.owner.invoice_total,
                )


class ProgressiveInvoiceDetailExcelExporter(FixedDetailExcelExporter):
    """Expose real source work units without changing source workbook output."""

    def __init__(self, template_path, row_builder=None, *, progress: ProgressCallback) -> None:
        super().__init__(template_path, row_builder=row_builder)
        self.progress = progress
        self.invoice_total = 0
        self.processed_invoice_count = 0
        self.generated_row_count = 0
        self.column_count = 0
        self.json_read_seconds = 0.0
        self.row_builder_seconds = 0.0
        self.worksheet_write_seconds = 0.0
        self.style_copy_seconds = 0.0
        self.fit_seconds = 0.0
        self.save_seconds = 0.0
        self.template_load_seconds = 0.0
        self.merge_seconds = 0.0
        self._write_style_copies = 0
        self._write_started_at: float | None = None
        self._raw_paths: set[Path] = set()
        self._last_emitted_row = -1
        self._last_emitted_invoice = 0

    def export(self, detail_records, output_path, from_date, to_date):
        self.invoice_total = len(detail_records)
        self._raw_paths = {
            Path(str(record.get("raw_detail_path") or ""))
            for record in detail_records
            if record.get("raw_detail_path")
        }
        original_builder = self.row_builder
        original_copy = source_module.copy
        original_load_workbook = source_module.load_workbook
        original_read_text = Path.read_text
        self.row_builder = _ProgressRowBuilder(original_builder, self)

        def measured_load_workbook(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original_load_workbook(*args, **kwargs)
            finally:
                self.template_load_seconds += time.perf_counter() - started
                self._emit("load_template", 1, 1)

        def measured_read_text(path: Path, *args, **kwargs):
            started = time.perf_counter()
            try:
                return original_read_text(path, *args, **kwargs)
            finally:
                if path in self._raw_paths:
                    self.json_read_seconds += time.perf_counter() - started

        def measured_copy(value):
            started = time.perf_counter()
            try:
                return original_copy(value)
            finally:
                self.style_copy_seconds += time.perf_counter() - started
                if self.processed_invoice_count == self.invoice_total and self.column_count:
                    if self._write_started_at is None:
                        self._write_started_at = time.perf_counter()
                    self._write_style_copies += 1
                    completed_rows = min(
                        self.generated_row_count,
                        self._write_style_copies // self.column_count,
                    )
                    emit_every = max(1, self.generated_row_count // 200)
                    if (
                        completed_rows == self.generated_row_count
                        or completed_rows - self._last_emitted_row >= emit_every
                    ):
                        self._last_emitted_row = completed_rows
                        self._emit("write_rows", completed_rows, self.generated_row_count)

        source_module.load_workbook = measured_load_workbook
        source_module.copy = measured_copy
        Path.read_text = measured_read_text
        total_started = time.perf_counter()
        self._emit("load_template", 0, 1)
        try:
            result = super().export(detail_records, output_path, from_date, to_date)
            total_seconds = time.perf_counter() - total_started
            logger.info(
                "excel_export_summary scope=details invoice_count=%s row_count=%s "
                "column_count=%s json_read_ms=%.1f row_builder_ms=%.1f "
                "worksheet_write_ms=%.1f style_copy_ms=%.1f merge_ms=%.1f "
                "fit_columns_ms=%.1f workbook_save_ms=%.1f template_load_ms=%.1f "
                "total_ms=%.1f",
                result.get("invoice_count", 0),
                result.get("row_count", 0),
                self.column_count,
                self.json_read_seconds * 1000,
                self.row_builder_seconds * 1000,
                self.worksheet_write_seconds * 1000,
                self.style_copy_seconds * 1000,
                self.merge_seconds * 1000,
                self.fit_seconds * 1000,
                self.save_seconds * 1000,
                self.template_load_seconds * 1000,
                total_seconds * 1000,
            )
            return result
        finally:
            Path.read_text = original_read_text
            source_module.copy = original_copy
            source_module.load_workbook = original_load_workbook
            self.row_builder = original_builder

    def _prepare_prototype(self, worksheet, header_row, prototype_row, column_keys):
        self.column_count = len(column_keys)
        return super()._prepare_prototype(
            worksheet, header_row, prototype_row, column_keys
        )

    def _merge_invoice_serial_numbers(self, worksheet, first_data_row, invoice_groups):
        if self._write_started_at is not None:
            self.worksheet_write_seconds += time.perf_counter() - self._write_started_at
            self._write_started_at = None
        self._emit("write_rows", self.generated_row_count, self.generated_row_count)
        started = time.perf_counter()
        try:
            return super()._merge_invoice_serial_numbers(
                worksheet, first_data_row, invoice_groups
            )
        finally:
            self.merge_seconds += time.perf_counter() - started

    def _fit_cells(
        self,
        worksheet: Any,
        *,
        header_row: int,
        first_data_row: int,
        last_data_row: int,
        column_keys: list[str],
    ) -> None:
        """Report the fixed-format phase without measuring runtime cell content."""
        started = time.perf_counter()
        column_count = len(column_keys)
        self._emit("format", 0, column_count)
        super()._fit_cells(
            worksheet,
            header_row=header_row,
            first_data_row=first_data_row,
            last_data_row=last_data_row,
            column_keys=column_keys,
        )
        self._emit("format", column_count, column_count)
        self.fit_seconds += time.perf_counter() - started

    def _save_atomically(self, workbook, output_path):
        self._emit("save", 0, 1)
        started = time.perf_counter()
        try:
            result = super()._save_atomically(workbook, output_path)
        finally:
            self.save_seconds += time.perf_counter() - started
        self._emit("save", 1, 1)
        return result

    def _emit(self, phase: str, processed: int, total: int) -> None:
        try:
            self.progress(phase, max(0, int(processed)), max(0, int(total)))
        except Exception:
            logger.exception("excel_export_progress_callback_failed phase=%s", phase)


__all__ = ["ProgressiveInvoiceDetailExcelExporter"]
