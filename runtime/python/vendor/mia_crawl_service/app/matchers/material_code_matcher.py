from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.parsers.invoice_xml_parser import XmlInvoiceLine, parse_decimal


@dataclass(frozen=True)
class DetailInvoiceLine:
    index: int
    product_name: str
    unit: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


def extract_detail_lines(detail_payload: Mapping[str, Any]) -> list[DetailInvoiceLine]:
    detail = _unwrap_detail(detail_payload)
    products = detail.get('hdhhdvu')
    if not isinstance(products, list):
        raise ValueError('Invoice detail JSON does not contain an hdhhdvu list')
    result: list[DetailInvoiceLine] = []
    for index, value in enumerate(products):
        product = value if isinstance(value, Mapping) else {}
        result.append(
            DetailInvoiceLine(
                index=index,
                product_name=str(
                    _first_value(product, ('ten', 'THHDVu', 'thhdvu')) or ''
                ).strip(),
                unit=_optional_text(
                    _first_value(product, ('dvtinh', 'DVTinh', 'unit'))
                ),
                quantity=parse_decimal(
                    _first_value(product, ('sluong', 'SLuong', 'quantity'))
                ),
                unit_price=parse_decimal(
                    _first_value(product, ('dgia', 'DGia', 'unit_price'))
                ),
                amount=parse_decimal(
                    _first_value(product, ('thtien', 'ThTien', 'amount'))
                ),
            )
        )
    return result


def match_material_codes(
    detail_lines: Sequence[DetailInvoiceLine],
    xml_lines: Sequence[XmlInvoiceLine],
) -> list[dict[str, Any]]:
    """Match conservatively and return one result per detail line."""
    matched: dict[int, tuple[XmlInvoiceLine, str]] = {}
    used_xml: set[int] = set()
    ambiguous_detail: set[int] = set()

    # Index is safe only after field verification. Different line counts do not
    # automatically invalidate unique composite/name matches later.
    for detail_line in detail_lines:
        if detail_line.index >= len(xml_lines):
            continue
        xml_line = xml_lines[detail_line.index]
        if _index_verified(detail_line, xml_line):
            matched[detail_line.index] = (xml_line, 'index_verified')
            used_xml.add(xml_line.index)

    for detail_line in detail_lines:
        if detail_line.index in matched:
            continue
        candidates = [
            line
            for line in xml_lines
            if line.index not in used_xml and _composite_equal(detail_line, line)
        ]
        if len(candidates) == 1:
            matched[detail_line.index] = (candidates[0], 'composite_key')
            used_xml.add(candidates[0].index)
        elif len(candidates) > 1:
            ambiguous_detail.add(detail_line.index)

    unresolved_name_counts = Counter(
        normalize_text(line.product_name)
        for line in detail_lines
        if line.index not in matched and normalize_text(line.product_name)
    )
    for detail_line in detail_lines:
        if detail_line.index in matched:
            continue
        normalized_name = normalize_text(detail_line.product_name)
        if not normalized_name:
            continue
        candidates = [
            line
            for line in xml_lines
            if line.index not in used_xml
            and normalize_text(line.product_name) == normalized_name
        ]
        if len(candidates) == 1 and unresolved_name_counts[normalized_name] == 1:
            matched[detail_line.index] = (candidates[0], 'normalized_name')
            used_xml.add(candidates[0].index)
            ambiguous_detail.discard(detail_line.index)
        elif candidates:
            ambiguous_detail.add(detail_line.index)

    results: list[dict[str, Any]] = []
    for detail_line in detail_lines:
        match = matched.get(detail_line.index)
        if match is not None:
            xml_line, method = match
            results.append({
                'line_index': detail_line.index,
                'material_code': xml_line.material_code,
                'detail_product_name': detail_line.product_name,
                'xml_product_name': xml_line.product_name or None,
                'match_method': method,
                'match_status': 'matched',
            })
            continue
        results.append({
            'line_index': detail_line.index,
            'material_code': None,
            'detail_product_name': detail_line.product_name,
            'xml_product_name': None,
            'match_method': None,
            'match_status': (
                'ambiguous'
                if detail_line.index in ambiguous_detail
                else 'unmatched'
            ),
        })
    return results


def unmatched_material_codes(
    detail_lines: Sequence[DetailInvoiceLine],
) -> list[dict[str, Any]]:
    return [
        {
            'line_index': line.index,
            'material_code': None,
            'detail_product_name': line.product_name,
            'xml_product_name': None,
            'match_method': None,
            'match_status': 'unmatched',
        }
        for line in detail_lines
    ]


def normalize_text(value: object) -> str:
    text = unicodedata.normalize('NFC', str(value or ''))
    return re.sub(r'\s+', ' ', text.strip()).casefold()


def _index_verified(detail: DetailInvoiceLine, xml: XmlInvoiceLine) -> bool:
    if not normalize_text(detail.product_name):
        return False
    if normalize_text(detail.product_name) != normalize_text(xml.product_name):
        return False
    comparisons = _attribute_comparisons(detail, xml)
    # A same-name line is not enough evidence, especially when an invoice has
    # duplicate product names. At least one of unit/quantity/price/amount must
    # be present on both sides and agree before index matching is accepted.
    return bool(comparisons) and all(comparisons)


def _composite_equal(detail: DetailInvoiceLine, xml: XmlInvoiceLine) -> bool:
    if normalize_text(detail.product_name) != normalize_text(xml.product_name):
        return False
    comparable = _attribute_comparisons(detail, xml, include_missing=True)
    present_count = sum(
        left is not None and right is not None
        for left, right in (
            (detail.unit, xml.unit),
            (detail.quantity, xml.quantity),
            (detail.unit_price, xml.unit_price),
            (detail.amount, xml.amount),
        )
    )
    return present_count >= 2 and all(comparable)


def _attribute_comparisons(
    detail: DetailInvoiceLine,
    xml: XmlInvoiceLine,
    *,
    include_missing: bool = False,
) -> list[bool]:
    comparisons: list[bool] = []
    pairs: tuple[tuple[object, object, bool], ...] = (
        (detail.unit, xml.unit, True),
        (detail.quantity, xml.quantity, False),
        (detail.unit_price, xml.unit_price, False),
        (detail.amount, xml.amount, False),
    )
    for left, right, is_text in pairs:
        if left is None or right is None:
            if include_missing and (left is None) != (right is None):
                comparisons.append(False)
            continue
        comparisons.append(
            normalize_text(left) == normalize_text(right)
            if is_text
            else _decimal_equal(left, right)
        )
    return comparisons


def _decimal_equal(left: object, right: object) -> bool:
    left_decimal = left if isinstance(left, Decimal) else parse_decimal(left)
    right_decimal = right if isinstance(right, Decimal) else parse_decimal(right)
    if left_decimal is None or right_decimal is None:
        return False
    difference = abs(left_decimal - right_decimal)
    tolerance = max(Decimal('0.000001'), abs(right_decimal) * Decimal('0.000001'))
    return difference <= tolerance


def _unwrap_detail(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value: Any = payload.get('detail', payload)
    # Stored detail documents wrap the API response; some responses themselves
    # wrap the actual invoice object in data_ct.
    for _ in range(3):
        if not isinstance(value, Mapping):
            break
        if isinstance(value.get('data_ct'), Mapping):
            value = value['data_ct']
            continue
        if isinstance(value.get('detail'), Mapping):
            value = value['detail']
            continue
        break
    if not isinstance(value, Mapping):
        raise ValueError('Invoice detail JSON root is not an object')
    return value


def _first_value(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, ''):
            return value
    return None


def _optional_text(value: object) -> str | None:
    text = str(value or '').strip()
    return text or None
