from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from app.services.overview_downloader import INVOICE_STATUS_LABELS
from app.utils.date_utils import BUSINESS_TIMEZONE

logger = logging.getLogger(__name__)

MISSING_SEARCH_CODE = (
    'Không tìm thấy mã tra cứu trên file XML, vui lòng liên hệ người bán '
    'để được cung cấp file PDF gốc.'
)
SEARCH_CODE_FIELD_NAMES = frozenset(
    name.casefold()
    for name in (
        'Mã số bí mật', 'KeySearch', 'Mã TC', 'TransactionID', 'Fkey',
        'MNHDon', 'QuanLy_SoBaoMat', 'Mã bảo mật', 'Số bảo mật',
        'Mã tra cứu hóa đơn', 'chungTuLienQuan', 'InvoiceId', 'MaTraCuu',
        'MTCuu', 'SearchInvoice', 'Mã tra cứu',
    )
)
PROCESSING_STATUS_LABELS = {
    0: 'Tổng cục Thuế đã nhận',
    1: 'Đang tiến hành kiểm tra điều kiện cấp mã',
    2: 'CQT từ chối hóa đơn theo từng lần phát sinh',
    3: 'Hóa đơn đủ điều kiện cấp mã',
    4: 'Hóa đơn không đủ điều kiện cấp mã',
    5: 'Đã cấp mã hóa đơn',
    6: 'Tổng cục thuế đã nhận không mã',
    7: 'Đã kiểm tra định kỳ HĐĐT không có mã',
    8: 'Tổng cục thuế đã nhận hóa đơn có mã khởi tạo từ máy tính tiền',
}
ITEM_NATURE_LABELS = {
    1: 'Hàng hóa, dịch vụ',
    2: 'Khuyến mại',
    3: 'Chiết khấu',
    4: 'Ghi chú, diễn giải',
}


def normalize_discount_amount(value: Any) -> Any:
    number = _to_number(value)
    if number is None:
        return value
    return -abs(number)


def resolve_tax_rate(
    sp: Mapping[str, Any],
    data_ct: Mapping[str, Any],
    overview_item: Mapping[str, Any] | None = None,
) -> str:
    tax_kind = str(sp.get('ltsuat') or '').strip().upper()
    raw_rate = sp.get('tsuat')
    if tax_kind in {'KCT', 'KKKNT'}:
        return tax_kind
    if raw_rate == 121:
        return 'KKKNT'
    if tax_kind == 'KHAC' or raw_rate == 122 or str(raw_rate).upper() == 'KHAC':
        other_rate = _find_other_tax_rate(data_ct, overview_item)
        return other_rate or 'KHAC'
    if isinstance(raw_rate, str):
        stripped = raw_rate.strip()
        upper = stripped.upper()
        if upper in {'KCT', 'KKKNT'}:
            return upper
        if stripped.endswith('%'):
            return stripped
    numeric_rate = _to_number(raw_rate)
    if numeric_rate is None:
        return raw_rate if raw_rate not in (None, '') else ''
    if numeric_rate == 0:
        return '0%'
    percentage = numeric_rate * 100 if abs(numeric_rate) <= 1 else numeric_rate
    return f'{_format_number(percentage)}%'


def resolve_tax_amount(
    sp: Mapping[str, Any],
    data_ct: Mapping[str, Any],
    row_index_in_invoice: int,
    total_rows_in_invoice: int,
    accumulated_tax: Decimal,
) -> Decimal | str:
    explicit_tax = sp.get('tthue')
    explicit_number = _to_number(explicit_tax)
    if explicit_number is not None:
        return explicit_number

    total_tax = _to_number(data_ct.get('tgtthue'))
    if row_index_in_invoice == total_rows_in_invoice - 1 and total_tax is not None:
        return total_tax - accumulated_tax

    amount = _to_number(sp.get('thtien'))
    rate = _numeric_tax_fraction(sp)
    if rate is None and str(sp.get('ltsuat') or '').strip().upper() == 'KHAC':
        resolved_rate = resolve_tax_rate(sp, data_ct)
        resolved_number = _to_number(resolved_rate)
        if resolved_number is not None:
            rate = resolved_number / 100
    if amount is None or rate is None:
        return 0
    return amount * rate


def extract_search_code(data_ct: Mapping[str, Any]) -> str:
    for array_name in ('ttkhac', 'cttkhac', 'ttttkhac', 'TTKhac'):
        for item in _extra_items(data_ct.get(array_name)):
            name = _extra_name(item)
            if name.casefold() in SEARCH_CODE_FIELD_NAMES:
                value = _extra_value(item)
                if value not in (None, ''):
                    return str(value)
    return MISSING_SEARCH_CODE


def build_adjustment_note(data_ct: Mapping[str, Any]) -> str:
    original_fields = (
        data_ct.get('khmshdgoc'), data_ct.get('khhdgoc'),
        data_ct.get('shdgoc'), data_ct.get('tdlhdgoc'),
    )
    if any(value not in (None, '') for value in original_fields):
        original_date = format_vietnamese_date(data_ct.get('tdlhdgoc'))
        return (
            'Điều chỉnh cho ký hiệu mẫu số hóa đơn '
            f'{data_ct.get("khmshdgoc") or ""}, ký hiệu hóa đơn '
            f'{data_ct.get("khhdgoc") or ""}, số hóa đơn '
            f'{data_ct.get("shdgoc") or ""}, ngày lập {original_date}'
        )
    note = _find_extra_value(data_ct.get('ttkhac'), {'ghi chú hóa đơn'})
    return str(note) if note not in (None, '') else ' '


def format_vietnamese_date(value: Any) -> str:
    if value in (None, ''):
        return ''
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value.strftime('%d/%m/%Y')
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            try:
                return datetime.strptime(text, '%d/%m/%Y').strftime('%d/%m/%Y')
            except ValueError:
                return ''
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(BUSINESS_TIMEZONE)
    return parsed.strftime('%d/%m/%Y')


class InvoiceDetailExcelRowBuilder:
    """Flatten one persisted invoice detail into one row per product/service."""

    def build_rows(
        self,
        detail_payload: dict[str, Any],
        detail_record: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        data_ct = self._unwrap_detail(detail_payload)
        products = data_ct.get('hdhhdvu')
        if not isinstance(products, list) or not products:
            logger.warning(
                'Skipping invoice detail without hdhhdvu nbmst=%s khhdon=%s shdon=%s',
                detail_record.get('nbmst'), detail_record.get('khhdon'),
                detail_record.get('shdon'),
            )
            return []

        created_date = format_vietnamese_date(
            data_ct.get('tdlap') or data_ct.get('ntao') or detail_record.get('nlap')
        )
        signed_date = format_vietnamese_date(data_ct.get('nky')) or created_date
        invoice_status = _mapped_status(data_ct.get('tthai'), INVOICE_STATUS_LABELS)
        # Some detail payloads omit ttxly although the persisted overview row
        # contains it. Preserve the source result instead of exporting a blank
        # "Kết quả kiểm tra hóa đơn" column.
        processing_status = _mapped_status(
            data_ct.get('ttxly') if data_ct.get('ttxly') not in (None, '') else detail_record.get('ttxly'),
            PROCESSING_STATUS_LABELS,
        )
        search_code = extract_search_code(data_ct)
        adjustment_note = build_adjustment_note(data_ct)
        material_codes = self._material_codes_by_index(
            detail_record.get('material_codes_json')
        )
        lookup_url = _first_value(
            data_ct,
            ('url', 'URL', 'urltracuu', 'url_tra_cuu', 'linktracuu', 'linkTraCuu'),
        )
        accumulated_tax = Decimal(0)
        rows: list[dict[str, Any]] = []
        for row_index, product_value in enumerate(products):
            if not isinstance(product_value, Mapping):
                logger.warning(
                    'Skipping invalid hdhhdvu row invoice=%s row=%d',
                    detail_record.get('shdon'), row_index,
                )
                continue
            product = product_value
            nature_code = _integer_code(product.get('tchat'))
            line_amount = product.get('thtien')
            if nature_code == 3:
                line_amount = normalize_discount_amount(line_amount)
            tax_amount = resolve_tax_amount(
                product, data_ct, row_index, len(products), accumulated_tax
            )
            numeric_tax = _to_number(tax_amount)
            if numeric_tax is not None:
                accumulated_tax += numeric_tax
            rows.append({
                'khmshdon': data_ct.get('khmshdon', detail_record.get('khmshdon', '')),
                'khhdon': data_ct.get('khhdon', detail_record.get('khhdon', '')),
                'shdon': str(data_ct.get('shdon', detail_record.get('shdon', ''))),
                'ntao': created_date,
                'nky': signed_date,
                'mhdon': data_ct.get('mhdon', ''),
                'dvtte': data_ct.get('dvtte', ''),
                'tgia': data_ct.get('tgia', ''),
                'nbten': data_ct.get('nbten') or data_ct.get('nmtnban') or '',
                'nbmst': data_ct.get('nbmst', detail_record.get('nbmst', '')),
                'nbdchi': data_ct.get('nbdchi', ''),
                'nmten': data_ct.get('nmten') or data_ct.get('nmtnmua') or '',
                'nmmst': data_ct.get('nmmst', ''),
                'nmdchi': data_ct.get('nmdchi', ''),
                # MVT has one source of truth: material_codes_json in SQLite.
                # Never infer it here from detail JSON or an XML package.
                'm_VT': material_codes.get(row_index, ''),
                'ten': product.get('ten', ''),
                'dvtinh': product.get('dvtinh', ''),
                'sluong': product.get('sluong', ''),
                'dgia': product.get('dgia', ''),
                'stckhau': product.get('stckhau', ''),
                'tsuat': resolve_tax_rate(product, data_ct, detail_record),
                'thtien': line_amount,
                'tthue': tax_amount,
                'ttcktmai': data_ct.get('ttcktmai', ''),
                'tgtphi': data_ct.get('tgtphi', ''),
                'tgtttbso': data_ct.get('tgtttbso', ''),
                'tthai': invoice_status,
                'ttxly': processing_status,
                'url': lookup_url,
                'mk': search_code,
                'ghichu': _first_value(data_ct, ('ghichu', 'gchu', 'gchdgoc')),
                'thtttoan': data_ct.get('thtttoan', ''),
                'tchat': ITEM_NATURE_LABELS.get(nature_code, product.get('tchat', '')),
                'dgiai': adjustment_note,
                'slo': _product_extra(product, ('slo', 'soLo', 'lotNumber'),
                                      ('Lot', 'Extra1', 'BatchNo', 'SoLo')),
                'hdung': _product_extra(product, ('hdung', 'hanDung', 'expirationDate'),
                                        ('ExpireDate', 'Extra2', 'Expiry', 'HanDung')),
            })
        return rows

    def build_rows_with_outcome(
        self,
        detail_payload: dict[str, Any],
        detail_record: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], str]:
        """Keep a valid empty result distinct from a missing/malformed result."""
        data_ct = self._unwrap_detail(detail_payload)
        products = data_ct.get('hdhhdvu')
        if not isinstance(products, list):
            return [], 'incomplete'
        rows = self.build_rows(detail_payload, detail_record)
        if products and not rows:
            return [], 'incomplete'
        return rows, ('with_lines' if rows else 'valid_empty')

    @staticmethod
    def _material_codes_by_index(value: Any) -> dict[int, str]:
        if value in (None, ''):
            return {}
        try:
            items = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning('Ignoring invalid material_codes_json during Excel export')
            return {}
        if not isinstance(items, list):
            return {}
        result: dict[int, str] = {}
        for item in items:
            if not isinstance(item, Mapping) or item.get('match_status') != 'matched':
                continue
            index = item.get('line_index')
            code = str(item.get('material_code') or '').strip()
            if isinstance(index, bool) or not isinstance(index, int) or not code:
                continue
            try:
                if float(code.replace(',', '.')) == 0:
                    continue
            except ValueError:
                pass
            result[index] = code
        return result

    @staticmethod
    def _unwrap_detail(detail_payload: Mapping[str, Any]) -> Mapping[str, Any]:
        detail: Any = detail_payload.get('detail', detail_payload)
        if isinstance(detail, Mapping) and isinstance(detail.get('data_ct'), Mapping):
            detail = detail['data_ct']
        if not isinstance(detail, Mapping):
            raise ValueError('Invoice detail payload must contain an object')
        return detail


def _find_other_tax_rate(
    data_ct: Mapping[str, Any],
    overview_item: Mapping[str, Any] | None,
) -> str | None:
    sources: Sequence[Any] = (
        data_ct.get('thttltsuat'),
        overview_item.get('thttltsuat') if overview_item else None,
    )
    for source in sources:
        if not isinstance(source, list):
            continue
        for item in source:
            if not isinstance(item, Mapping):
                continue
            value = str(item.get('tsuat') or '').strip()
            if value.upper().startswith('KHAC:') and value.endswith('%'):
                return value.split(':', 1)[1]
    return None


def _numeric_tax_fraction(sp: Mapping[str, Any]) -> Decimal | None:
    tax_kind = str(sp.get('ltsuat') or '').strip().upper()
    if tax_kind in {'KCT', 'KKKNT'}:
        return None
    numeric = _to_number(sp.get('tsuat'))
    if numeric is None or numeric in {121, 122}:
        return None
    return numeric / Decimal(100) if abs(numeric) > 1 else numeric


def _to_number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value in (None, ''):
        return None
    if isinstance(value, Decimal):
        return Decimal(0) if value == 0 else value
    if isinstance(value, (int, float)):
        value = str(value)
    try:
        text = str(value).strip().replace(',', '')
        if text.endswith('%'):
            text = text[:-1]
        number = Decimal(text)
    except (TypeError, ValueError, InvalidOperation):
        return None
    return Decimal(0) if number == 0 else number


def _format_number(value: Decimal) -> str:
    return format(value, 'f').rstrip('0').rstrip('.') if value != value.to_integral() else format(value, '.0f')


def _integer_code(value: Any) -> int | None:
    number = _to_number(value)
    return int(number) if number is not None and number == number.to_integral() else None


def _mapped_status(value: Any, labels: Mapping[int, str]) -> Any:
    code = _integer_code(value)
    return labels.get(code, value if value is not None else '')


def _extra_items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _extra_name(item: Mapping[str, Any]) -> str:
    return str(_first_value(item, ('ttruong', 'ten', 'name')) or '').strip()


def _extra_value(item: Mapping[str, Any]) -> Any:
    return _first_value(item, ('dlieu', 'value', 'giaTri'))


def _find_extra_value(value: Any, names: set[str]) -> Any:
    normalized_names = {name.casefold() for name in names}
    for item in _extra_items(value):
        if _extra_name(item).casefold() in normalized_names:
            return _extra_value(item)
    return None


def _first_value(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, ''):
            return value
    return ''


def _product_extra(
    product: Mapping[str, Any],
    direct_names: Sequence[str],
    extra_names: Sequence[str],
) -> Any:
    direct = _first_value(product, direct_names)
    if direct not in (None, ''):
        return direct
    return _find_extra_value(product.get('ttkhac'), set(extra_names)) or ''
