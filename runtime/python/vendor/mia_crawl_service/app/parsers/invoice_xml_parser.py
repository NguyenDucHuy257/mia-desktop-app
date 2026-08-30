from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException


MAX_INVOICE_XML_BYTES = 10 * 1024 * 1024


class InvoiceXmlError(RuntimeError):
    code = 'invoice_xml_error'


class InvoiceXmlTooLargeError(InvoiceXmlError):
    code = 'invoice_xml_too_large'


class InvoiceXmlMalformedError(InvoiceXmlError):
    code = 'invoice_xml_malformed'


@dataclass(frozen=True)
class XmlInvoiceLine:
    index: int
    material_code: str | None
    product_name: str
    unit: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None


def parse_invoice_xml(path: Path | str) -> list[XmlInvoiceLine]:
    """Parse HHDVu lines regardless of XML namespace style."""
    source = Path(path)
    size = source.stat().st_size
    if size > MAX_INVOICE_XML_BYTES:
        raise InvoiceXmlTooLargeError(
            f'Invoice XML exceeds {MAX_INVOICE_XML_BYTES} byte limit'
        )
    try:
        tree = ElementTree.parse(source)
    except (ElementTree.ParseError, DefusedXmlException) as error:
        raise InvoiceXmlMalformedError('Invoice XML is malformed or unsafe') from error
    result: list[XmlInvoiceLine] = []
    for element in tree.getroot().iter():
        if _local_name(element.tag).casefold() != 'hhdvu':
            continue
        fields = {
            _local_name(child.tag).casefold(): _element_text(child)
            for child in element
        }
        result.append(
            XmlInvoiceLine(
                index=len(result),
                material_code=normalize_material_code(fields.get('mhhdvu')),
                product_name=(fields.get('thhdvu') or '').strip(),
                unit=_optional_text(fields.get('dvtinh')),
                quantity=parse_decimal(fields.get('sluong')),
                unit_price=parse_decimal(fields.get('dgia')),
                amount=parse_decimal(fields.get('thtien')),
            )
        )
    return result


def parse_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('\u00a0', '').replace(' ', '').replace("'", '')
    text = re.sub(r'[^0-9,\.\-+]', '', text)
    if not text or text in {'-', '+', '.', ','}:
        return None

    if ',' in text and '.' in text:
        decimal_separator = ',' if text.rfind(',') > text.rfind('.') else '.'
        thousands_separator = '.' if decimal_separator == ',' else ','
        text = text.replace(thousands_separator, '')
        if decimal_separator == ',':
            text = text.replace(',', '.')
    elif ',' in text:
        parts = text.split(',')
        if len(parts) > 2 and all(len(part) == 3 for part in parts[1:]):
            text = ''.join(parts)
        else:
            text = ''.join(parts[:-1]) + '.' + parts[-1]
    elif text.count('.') > 1:
        parts = text.split('.')
        if all(len(part) == 3 for part in parts[1:]):
            text = ''.join(parts)
        else:
            text = ''.join(parts[:-1]) + '.' + parts[-1]

    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def normalize_material_code(value: object) -> str | None:
    text = str(value or '').strip()
    if not text:
        return None
    compact_number = text.replace(' ', '').replace(',', '').replace('.', '')
    if compact_number and set(compact_number) <= {'0', '+', '-'}:
        return None
    try:
        if Decimal(text.replace(',', '.')) == 0:
            return None
    except InvalidOperation:
        pass
    return text


def _local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1].rsplit(':', 1)[-1]


def _element_text(element: ElementTree.Element) -> str:
    return ''.join(element.itertext()).strip()


def _optional_text(value: str | None) -> str | None:
    text = str(value or '').strip()
    return text or None
