from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


def _text(value: Any) -> str:
    return unicodedata.normalize('NFKC', str(value or '')).strip().casefold()


def _compact(value: Any) -> str:
    return re.sub(r'[\s\-_.\\/]+', '', _text(value))


def _number(value: Any) -> str:
    text = _compact(value)
    if not re.fullmatch(r'[+]?\d+(?:\.0+)?', text):
        return text
    try:
        return format(Decimal(text).quantize(Decimal('1')), 'f')
    except (InvalidOperation, ValueError):
        return text


def canonical_invoice_identity(
    company_tax_code: Any,
    direction: Any,
    fields: Mapping[str, Any],
) -> tuple[str, str, str, str, str, str]:
    """Source-independent business identity used by Overview and Detail.

    query/sco-query is collection provenance, not part of the invoice identity.
    A duplicate returned by both endpoints must remain one business invoice.
    """
    return (
        _compact(company_tax_code),
        _compact(direction),
        _compact(fields.get('nbmst')),
        _number(fields.get('khmshdon')),
        _compact(fields.get('khhdon')),
        _number(fields.get('shdon')),
    )


def masked_invoice_identity(identity: tuple[str, ...]) -> str:
    import hashlib
    return hashlib.sha256('|'.join(identity).encode('utf-8')).hexdigest()[:12]


def invoice_status_is_excluded(value: Any) -> bool:
    """Only statuses explicitly meaning cancelled or replaced-by-another invoice."""
    text = _text(value)
    if text in {'4', '6'}:
        return True
    ascii_text = ''.join(
        char for char in unicodedata.normalize('NFD', text)
        if unicodedata.category(char) != 'Mn'
    )
    words = re.sub(r'[^a-z0-9]+', ' ', ascii_text).strip()
    return ('bi thay the' in words or re.search(r'(^| )huy( |$)', words) is not None)
