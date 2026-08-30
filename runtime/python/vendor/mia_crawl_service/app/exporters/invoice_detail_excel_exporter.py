from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import unicodedata
from copy import copy
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from PIL import ImageFont

from app.parsers.invoice_detail_excel_row_builder import InvoiceDetailExcelRowBuilder

logger = logging.getLogger(__name__)

HEADER_ALIASES = {
    'stt': 'stt',
    'số thứ tự': 'stt',
    'mẫu số hd': 'khmshdon',
    'ký hiệu hóa đơn': 'khhdon',
    'số hóa đơn': 'shdon',
    'ngày lập hóa đơn': 'ntao',
    'ngày người bán ký số': 'nky',
    'mccqt': 'mhdon',
    'ngày cqt ký số': 'nky',
    'đơn vị tiền tệ': 'dvtte',
    'tỷ giá': 'tgia',
    'tên người bán': 'nbten',
    'mst người bán': 'nbmst',
    'địa chỉ người bán': 'nbdchi',
    'tên người mua': 'nmten',
    'mst người mua': 'nmmst',
    'địa chỉ người mua': 'nmdchi',
    'mã vt': 'm_VT',
    'tên hàng hóa, dịch vụ': 'ten',
    'đơn vị tính': 'dvtinh',
    'số lượng': 'sluong',
    'đơn giá': 'dgia',
    'chiết khấu': 'stckhau',
    'thuế suất': 'tsuat',
    'thành tiền chưa thuế': 'thtien',
    'tiền thuế': 'tthue',
    'tổng tiền cktm': 'ttcktmai',
    'tổng tiền phí': 'tgtphi',
    'tổng tiền thanh toán': 'tgtttbso',
    'trạng thái hóa đơn': 'tthai',
    'kết quả kiểm tra hóa đơn': 'ttxly',
    'url tra cứu hóa đơn': 'url',
    'mã tra cứu': 'mk',
    'ghi chú 1': 'ghichu',
    'hình thức thanh toán': 'thtttoan',
    'tính chất': 'tchat',
    'ghi chú 2': 'dgiai',
    'số lô': 'slo',
    'hạn dùng': 'hdung',
}
INTERNAL_KEYS = frozenset(HEADER_ALIASES.values())
LEFT_ALIGNED_KEYS = frozenset({
    'mhdon',
    'nbten',
    'nbdchi',
    'nmten',
    'nmdchi',
    'm_VT',
    'ten',
})


class InvoiceDetailExcelExporter:
    """Render persisted detail JSON rows into the legacy detail workbook."""

    def __init__(
        self,
        template_path: Path,
        row_builder: InvoiceDetailExcelRowBuilder | None = None,
    ) -> None:
        self.template_path = Path(template_path)
        self.row_builder = row_builder or InvoiceDetailExcelRowBuilder()

    def export(
        self,
        detail_records: list[dict[str, Any]],
        output_path: Path,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        parsed_from, parsed_to = self._validate_inputs(output_path, from_date, to_date)
        workbook = load_workbook(self.template_path)
        try:
            worksheet = workbook.worksheets[0]
            header_row = self._find_header_row(worksheet)
            if header_row < 5:
                # The supplied legacy template only has its header row. Shift it
                # once to reserve A4 for the period; data loops never insert rows.
                worksheet.insert_rows(header_row, amount=5 - header_row)
                header_row = 5
            self._ensure_serial_number_column(worksheet, header_row)
            column_keys = self._column_keys(worksheet, header_row)
            self._format_title_and_headers(
                worksheet,
                header_row,
                len(column_keys),
                f'Từ ngày {parsed_from:%d/%m/%Y} đến ngày {parsed_to:%d/%m/%Y}',
            )
            prototype_row = header_row + 1
            prototype_styles, prototype_height = self._prepare_prototype(
                worksheet, header_row, prototype_row, column_keys
            )

            rows: list[dict[str, Any]] = []
            invoice_groups: list[tuple[int, int]] = []
            invoice_count = 0
            skipped_count = 0
            for record in detail_records:
                raw_path = Path(str(record.get('raw_detail_path') or ''))
                try:
                    payload = json.loads(raw_path.read_text(encoding='utf-8'))
                    if not isinstance(payload, dict):
                        raise ValueError('detail JSON root is not an object')
                    invoice_rows = self.row_builder.build_rows(payload, record)
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                    skipped_count += 1
                    logger.warning(
                        'Skipping unreadable invoice detail path=%s error=%s',
                        raw_path, type(error).__name__,
                    )
                    continue
                if not invoice_rows:
                    skipped_count += 1
                    continue
                invoice_count += 1
                group_start = len(rows)
                for invoice_row in invoice_rows:
                    invoice_row['stt'] = invoice_count
                rows.extend(invoice_rows)
                invoice_groups.append((group_start, len(rows) - 1))

            for row_offset, row_values in enumerate(rows):
                target_row = prototype_row + row_offset
                if prototype_height is not None:
                    worksheet.row_dimensions[target_row].height = prototype_height
                for column_index, key in enumerate(column_keys, start=1):
                    cell = worksheet.cell(target_row, column_index)
                    cell._style = copy(prototype_styles[column_index - 1])
                    value = self._safe_excel_value(row_values.get(key, ''))
                    cell.value = value
                    if key == 'url' and isinstance(value, str) and value.startswith(('http://', 'https://')):
                        cell.hyperlink = value

            self._merge_invoice_serial_numbers(
                worksheet, prototype_row, invoice_groups
            )
            self._fit_cells(
                worksheet,
                header_row=header_row,
                first_data_row=prototype_row,
                last_data_row=prototype_row + len(rows) - 1,
                column_keys=column_keys,
            )

            if rows:
                worksheet.auto_filter.ref = (
                    f'A{header_row}:{worksheet.cell(header_row, len(column_keys)).column_letter}'
                    f'{prototype_row + len(rows) - 1}'
                )
            self._save_atomically(workbook, output_path)
        finally:
            workbook.close()

        summary = {
            'output_path': str(output_path.resolve()),
            'invoice_count': invoice_count,
            'row_count': len(rows),
            'skipped_count': skipped_count,
        }
        logger.info('Exported invoice detail Excel summary=%s', summary)
        return summary

    @staticmethod
    def _ensure_serial_number_column(worksheet: Any, header_row: int) -> None:
        """Add the invoice-level serial-number column once, ahead of the template."""
        first_header = _normalize_header(worksheet.cell(header_row, 1).value)
        if HEADER_ALIASES.get(first_header) == 'stt':
            return

        worksheet.insert_cols(1, amount=1)
        source = worksheet.cell(header_row, 2)
        target = worksheet.cell(header_row, 1)
        target._style = copy(source._style)
        target.value = 'STT'

    @staticmethod
    def _format_title_and_headers(
        worksheet: Any,
        header_row: int,
        column_count: int,
        period_text: str,
    ) -> None:
        last_column = get_column_letter(column_count)
        for merged_range in list(worksheet.merged_cells.ranges):
            if merged_range.min_row <= 4 <= merged_range.max_row:
                worksheet.unmerge_cells(str(merged_range))
        worksheet.merge_cells(f'A4:{last_column}4')
        title = worksheet['A4']
        title.value = period_text
        title.font = Font(name='Times New Roman', size=14, bold=True)
        title.alignment = Alignment(
            horizontal='center', vertical='center', wrap_text=True
        )
        worksheet.row_dimensions[4].height = 24

        for column in range(1, column_count + 1):
            cell = worksheet.cell(header_row, column)
            cell.font = Font(name='Times New Roman', size=11, bold=True)
            cell.alignment = Alignment(
                horizontal='center', vertical='center', wrap_text=True
            )
        worksheet.row_dimensions[header_row].height = 28

    @staticmethod
    def _merge_invoice_serial_numbers(
        worksheet: Any,
        first_data_row: int,
        invoice_groups: list[tuple[int, int]],
    ) -> None:
        for start_offset, end_offset in invoice_groups:
            start_row = first_data_row + start_offset
            end_row = first_data_row + end_offset
            if end_row > start_row:
                worksheet.merge_cells(
                    start_row=start_row,
                    start_column=1,
                    end_row=end_row,
                    end_column=1,
                )
            cell = worksheet.cell(start_row, 1)
            cell.alignment = Alignment(
                horizontal='center', vertical='center', wrap_text=False,
                shrink_to_fit=False,
            )
            cell.font = Font(name='Times New Roman', size=10, bold=True)

    @staticmethod
    def _fit_cells(
        worksheet: Any,
        *,
        header_row: int,
        first_data_row: int,
        last_data_row: int,
        column_keys: list[str],
    ) -> None:
        """Keep fixed-size text and fit each column to a robust data width."""
        effective_last_row = max(header_row, last_data_row)
        fitted_widths: list[float] = []
        for column, _key in enumerate(column_keys, start=1):
            data_widths = [
                _display_width(worksheet.cell(row, column).value)
                for row in range(first_data_row, effective_last_row + 1)
                if not _is_empty_excel_value(worksheet.cell(row, column).value)
            ]
            column_dimension = worksheet.column_dimensions[get_column_letter(column)]
            if not data_widths:
                column_dimension.width = 0
                column_dimension.hidden = True
                fitted_widths.append(0)
                continue
            # 255 is Excel's intrinsic maximum column width, not a per-column
            # business convention. Pixel measurement already includes padding.
            width = min(max(_robust_column_width(data_widths), 3), 255)
            column_dimension.width = width
            column_dimension.hidden = False
            fitted_widths.append(width)

        for row in range(first_data_row, effective_last_row + 1):
            required_lines = 1
            for column, width in enumerate(fitted_widths, start=1):
                cell = worksheet.cell(row, column)
                if _is_empty_excel_value(cell.value):
                    continue
                content_width = _display_width(cell.value)
                usable_width = max(width, 1)
                if content_width <= usable_width + 0.01:
                    continue
                # A cell can exceed Excel's 255-character column-width limit.
                # Wrap only that exceptional cell; ordinary rows stay one line.
                alignment = copy(cell.alignment)
                alignment.wrap_text = True
                alignment.shrink_to_fit = False
                cell.alignment = alignment
                required_lines = max(
                    required_lines,
                    math.ceil(content_width / usable_width),
                )
            worksheet.row_dimensions[row].height = min(20 * required_lines, 409)

        worksheet.sheet_view.zoomScale = 90
        worksheet.sheet_view.zoomScaleNormal = 90
        worksheet.page_setup.orientation = 'landscape'

    def _validate_inputs(
        self,
        output_path: Path,
        from_date: str,
        to_date: str,
    ) -> tuple[date, date]:
        if not self.template_path.is_file():
            raise FileNotFoundError(f'Missing invoice detail template: {self.template_path}')
        if output_path.suffix.lower() != '.xlsx':
            raise ValueError('output_path must use the .xlsx extension')
        try:
            parsed_from = date.fromisoformat(from_date)
            parsed_to = date.fromisoformat(to_date)
        except (TypeError, ValueError) as error:
            raise ValueError('from_date and to_date must use YYYY-MM-DD') from error
        if from_date != parsed_from.isoformat() or to_date != parsed_to.isoformat():
            raise ValueError('from_date and to_date must use YYYY-MM-DD')
        if parsed_from > parsed_to:
            raise ValueError('from_date must not be after to_date')
        return parsed_from, parsed_to

    @staticmethod
    def _find_header_row(worksheet: Any) -> int:
        best_row = 0
        best_score = 0
        for row_index in range(1, min(worksheet.max_row, 30) + 1):
            score = sum(
                _normalize_header(worksheet.cell(row_index, column).value) in HEADER_ALIASES
                for column in range(1, worksheet.max_column + 1)
            )
            if score > best_score:
                best_row, best_score = row_index, score
        if best_score < 5:
            raise RuntimeError(
                f'Cannot identify invoice detail header row in {worksheet.title!r}'
            )
        return best_row

    @staticmethod
    def _column_keys(worksheet: Any, header_row: int) -> list[str]:
        keys: list[str] = []
        unknown_headers: list[str] = []
        for column in range(1, worksheet.max_column + 1):
            raw_header = worksheet.cell(header_row, column).value
            normalized = _normalize_header(raw_header)
            key = HEADER_ALIASES.get(normalized)
            if key is None and str(raw_header or '').strip() in INTERNAL_KEYS:
                key = str(raw_header).strip()
            if key is None:
                unknown_headers.append(str(raw_header))
            else:
                keys.append(key)
        if unknown_headers:
            raise RuntimeError(
                'Unknown invoice detail template headers: ' + ', '.join(unknown_headers)
            )
        return keys

    @staticmethod
    def _prepare_prototype(
        worksheet: Any,
        header_row: int,
        prototype_row: int,
        column_keys: list[str],
    ) -> tuple[list[Any], float | None]:
        column_count = len(column_keys)
        has_prototype_style = any(
            worksheet.cell(prototype_row, column).has_style
            for column in range(1, column_count + 1)
        )
        if not has_prototype_style:
            for column in range(1, column_count + 1):
                header_cell = worksheet.cell(header_row, column)
                prototype_cell = worksheet.cell(prototype_row, column)
                prototype_cell._style = copy(header_cell._style)
                data_font = copy(header_cell.font)
                data_font.bold = False
                prototype_cell.font = data_font
                prototype_cell.fill = PatternFill(fill_type=None)
                prototype_cell.value = None
        for column in range(1, column_count + 1):
            prototype_cell = worksheet.cell(prototype_row, column)
            data_font = copy(prototype_cell.font)
            data_font.name = 'Times New Roman'
            data_font.size = 10
            data_font.bold = False
            prototype_cell.font = data_font
            data_alignment = copy(prototype_cell.alignment)
            if column_keys[column - 1] in LEFT_ALIGNED_KEYS:
                data_alignment.horizontal = 'left'
            data_alignment.vertical = 'center'
            data_alignment.wrap_text = False
            data_alignment.shrink_to_fit = False
            prototype_cell.alignment = data_alignment
        style_source_row = prototype_row
        styles = [
            copy(worksheet.cell(style_source_row, column)._style)
            for column in range(1, column_count + 1)
        ]
        height = worksheet.row_dimensions[style_source_row].height
        last_existing_row = max(worksheet.max_row, prototype_row)
        for row_index in range(prototype_row, last_existing_row + 1):
            for column in range(1, column_count + 1):
                cell = worksheet.cell(row_index, column)
                cell.value = None
                cell.hyperlink = None
                cell._style = copy(styles[column - 1])
        return styles, height

    @staticmethod
    def _safe_excel_value(value: Any) -> Any:
        if isinstance(value, str) and value.startswith('='):
            return "'" + value
        return value

    @staticmethod
    def _save_atomically(workbook: Any, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=output_path.parent,
                prefix=f'.{output_path.stem}.',
                suffix='.xlsx.tmp',
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            workbook.save(temporary_path)
            os.replace(temporary_path, output_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def _normalize_header(value: Any) -> str:
    text = unicodedata.normalize('NFKC', str(value or '')).strip().casefold()
    return re.sub(r'\s+', ' ', text)


def _is_empty_excel_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _robust_column_width(widths: list[float]) -> float:
    """Return a representative width without letting rare outliers dominate."""
    if not widths:
        return 0.0
    ordered = sorted(widths)
    if len(ordered) < 4:
        return ordered[-1]

    q1 = _percentile(ordered, 0.25)
    q3 = _percentile(ordered, 0.75)
    iqr = q3 - q1
    outlier_cutoff = q3 + (1.5 * iqr)
    typical_widths = [width for width in ordered if width <= outlier_cutoff]
    if not typical_widths:
        return _percentile(ordered, 0.5)
    return typical_widths[-1]


def _percentile(ordered_values: list[float], fraction: float) -> float:
    if not ordered_values:
        return 0.0
    if len(ordered_values) == 1:
        return ordered_values[0]
    position = (len(ordered_values) - 1) * fraction
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return ordered_values[lower_index]
    lower_value = ordered_values[lower_index]
    upper_value = ordered_values[upper_index]
    return lower_value + ((upper_value - lower_value) * (position - lower_index))


def _display_width(
    value: Any,
    *,
    font_size: int = 10,
    bold: bool = False,
) -> float:
    """Measure visible Times New Roman text and convert pixels to Excel width."""
    if value is None:
        return 0.0
    lines = str(value).splitlines() or ['']
    font = _times_new_roman_font(font_size, bold)
    if font is None:
        return float(max((len(line) for line in lines), default=0) + 1.5)
    text_pixels = max((font.getlength(line) for line in lines), default=0.0)
    # Excel's default column unit is approximately seven pixels at 96 DPI.
    # Ten extra pixels provide only border/cell padding, not business sizing.
    return float((text_pixels + 10) / 7)


@lru_cache(maxsize=8)
def _times_new_roman_font(
    font_size: int,
    bold: bool,
) -> ImageFont.FreeTypeFont | None:
    pixel_size = math.ceil(font_size * 96 / 72)
    font_name = 'timesbd.ttf' if bold else 'times.ttf'
    windows_directory = Path(os.environ.get('WINDIR', r'C:\Windows'))
    candidates = (windows_directory / 'Fonts' / font_name, Path(font_name))
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), pixel_size)
        except OSError:
            continue
    logger.warning(
        'Times New Roman font file was not found; using character-count Excel fit'
    )
    return None
