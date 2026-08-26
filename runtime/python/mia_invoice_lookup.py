"""Desktop-owned invoice lookup resolver shared by Results and Excel export."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from mia_invoice_lookup_provider_rules import PROVIDER_RULES
from mia_invoice_lookup_seller_rules import (
    SELLER_EXACT_RULES,
    SELLER_ROOT_RULES,
    SUSPICIOUS_RULE_KEYS,
)


LEGACY_MISSING_CODE_PREFIX = "khong tim thay ma tra cuu tren file xml"
EXTRA_ARRAYS = ("ttkhac", "cttkhac", "ttttkhac", "TTKhac")
CODE_ALIASES = frozenset({
    "masobimat", "keysearch", "matc", "transactionid", "fkey", "mnhdon",
    "quanlysobaomat", "mabaomat", "sobaomat", "matracuuhoadon",
    "chungtulienquan", "invoiceid", "matracuu", "mtcuu", "searchinvoice",
})
URL_ALIASES = frozenset({"url", "urltracuu", "linktracuu", "trangtracuu"})
HTTP_PREFIX = "http" + "://"
HTTPS_PREFIX = "https" + "://"


@dataclass(frozen=True)
class InvoiceLookupResult:
    status: str
    url: str | None
    code: str | None
    mode: str
    matched_by: str | None = None
    rule_id: str | None = None
    url_source: str | None = None
    code_source: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["warnings"] = list(self.warnings)
        return value


def normalize_tax_code(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def root_tax_code(value: Any) -> str:
    return normalize_tax_code(value).split("-", 1)[0]


def normalize_lookup_key(value: Any) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or ""))
    without_marks = "".join(char for char in decomposed if unicodedata.category(char) != "Mn").replace("đ", "d").replace("Đ", "D")
    return "".join(char.lower() for char in without_marks if char.isalnum())


def is_safe_lookup_url(value: Any, *, allow_localhost: bool = False) -> bool:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 2048:
        return False
    try:
        parsed = urlsplit(value.strip())
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return False
    if not allow_localhost and (host == "localhost" or host == "127.0.0.1" or host == "::1"):
        return False
    return True


def extract_lookup_code(detail_payload: Mapping[str, Any]) -> dict[str, str] | None:
    detail = _unwrap_detail(detail_payload)
    for array_name in EXTRA_ARRAYS:
        items = detail.get(array_name)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            name = _first(item, ("ttruong", "ten", "name"))
            if normalize_lookup_key(name) not in CODE_ALIASES:
                continue
            value = str(_first(item, ("dlieu", "value", "giaTri")) or "").strip()
            if value and not normalize_lookup_key(value).startswith(LEGACY_MISSING_CODE_PREFIX.replace(" ", "")):
                return {"value": value, "source": f"{array_name}.{name}"}
    return None


def resolve_invoice_lookup(
    detail_payload: Mapping[str, Any],
    detail_record: Mapping[str, Any] | None = None,
    available_xml: str | bytes | Path | None = None,
) -> InvoiceLookupResult:
    detail = _unwrap_detail(detail_payload)
    record = detail_record or {}
    warnings: list[str] = []
    seller = normalize_tax_code(detail.get("nbmst") or record.get("nbmst"))
    provider = normalize_tax_code(detail.get("msttcgp"))
    fallback_provider = normalize_tax_code(detail.get("tvandnkntt"))
    code_match = extract_lookup_code(detail)
    code = code_match["value"] if code_match else None
    code_source = code_match["source"] if code_match else None

    direct = _direct_url(detail)
    if direct:
        direct_url = direct[0]
        if direct_url.rstrip().endswith("strFkey="):
            direct_url = direct_url + quote(code, safe="") if code else direct_url.rsplit("?", 1)[0]
        return _result(direct_url, code, "direct", "invoice", "direct", direct[1], code_source, warnings)

    rule: dict[str, str] | None = None
    matched_by: str | None = None
    rule_id: str | None = None
    if seller in SELLER_EXACT_RULES:
        rule = _rule_from_legacy(SELLER_EXACT_RULES[seller], seller)
        matched_by, rule_id = "seller_exact", seller
    elif root_tax_code(seller) in SELLER_ROOT_RULES:
        rule = dict(SELLER_ROOT_RULES[root_tax_code(seller)])
        matched_by, rule_id = "seller_root", root_tax_code(seller)
    elif provider in PROVIDER_RULES:
        rule = _rule_from_legacy(PROVIDER_RULES[provider], provider)
        matched_by, rule_id = "msttcgp", provider
    elif fallback_provider in PROVIDER_RULES:
        rule = _rule_from_legacy(PROVIDER_RULES[fallback_provider], fallback_provider)
        matched_by, rule_id = "tvandnkntt", fallback_provider

    if rule_id in SUSPICIOUS_RULE_KEYS:
        warnings.append("suspicious_tax_code")
    if rule is None:
        return _result(None, code, "code" if code else "manual", None, None, None, code_source, warnings)
    if rule.get("url"):
        rule = {**rule, "url": _decode_rule_url(rule["url"])}

    strategy = rule.get("strategy", "static")
    mhdon = str(detail.get("mhdon") or record.get("mhdon") or "").strip()
    if not code and (strategy in {"easyinvoice", "vnpt_dynamic"} or _needs_mhdon_fallback(rule)):
        code = mhdon or None
        code_source = "mhdon" if code else None

    if strategy == "manual":
        return _result(rule.get("url"), None, "manual", matched_by, rule_id, matched_by, None, warnings)
    if strategy == "easyinvoice":
        base = f"{HTTPS_PREFIX}{seller}hd.easyinvoice.com.vn/Search/" if seller else None
        url = _with_query_value(base, "strFkey", code)
        return _result(url, code, "direct" if code else "code", matched_by, rule_id, matched_by, code_source, warnings)
    if strategy == "vnpt_dynamic":
        base = f"{HTTPS_PREFIX}{seller}-tt78.vnpt-invoice.com.vn/" if seller else None
        url = _with_query_value(base, "strFkey", code)
        return _result(url, code, "direct" if code else "code", matched_by, rule_id, matched_by, code_source, warnings)
    if strategy == "xml_guid":
        guid = _xml_guid(available_xml)
        url = f"{HTTPS_PREFIX}van.ehoadon.vn/Lookup?InvoiceGUID={quote(guid, safe='')}" if guid else rule.get("url")
        return _result(url, guid or code, "xml_guid" if guid else "code", matched_by, rule_id, "local_xml" if guid else matched_by, "local_xml.DLHDon@Id" if guid else code_source, warnings)

    url = rule.get("url")
    if isinstance(url, str) and "{code}" in url:
        url = url.replace("{code}", quote(code, safe="")) if code else url.split("{code}", 1)[0]
    elif isinstance(url, str) and url.rstrip().endswith("strFkey="):
        url = url + quote(code, safe="") if code else url.rsplit("?", 1)[0]
    if code and code.endswith("*") and isinstance(url, str) and "tt78" in url:
        url = _with_query_value(HTTPS_PREFIX + "hoadon.petrolimex.com.vn/SearchInvoicebycode/Index", "strFkey", code)
    return _result(url, code, "direct" if code and "strFkey=" in str(url) else "code", matched_by, rule_id, matched_by, code_source, warnings)


def _rule_from_legacy(value: str, rule_id: str) -> dict[str, str]:
    text = _decode_rule_url(str(value or "").strip())
    if text.casefold() == "easy":
        return {"strategy": "easyinvoice"}
    if rule_id == "0101360697" and "bit.ly/hdtracuuVan" in text:
        return {"strategy": "xml_guid", "url": HTTPS_PREFIX + "van.ehoadon.vn/Lookup"}
    if not is_safe_lookup_url(text):
        return {"strategy": "invalid", "url": text}
    if "lottemart-nsg" in text and "/Portal/Index" in text:
        return {"strategy": "manual", "url": text}
    return {"strategy": "template" if text.endswith("strFkey=") else "static", "url": text}


def _decode_rule_url(value: str) -> str:
    if value.startswith("https|"):
        return HTTPS_PREFIX + value.removeprefix("https|")
    if value.startswith("http|"):
        return HTTP_PREFIX + value.removeprefix("http|")
    return value


def _needs_mhdon_fallback(rule: Mapping[str, str]) -> bool:
    url = str(rule.get("url") or "").casefold()
    return "vietinvoice.vn" in url or "vininvoice.vn" in url or "tt78" in url


def _direct_url(detail: Mapping[str, Any]) -> tuple[str, str] | None:
    for name in ("url", "URL", "urltracuu", "url_tra_cuu", "linktracuu", "linkTraCuu", "Trangtracuu"):
        value = detail.get(name)
        if is_safe_lookup_url(value):
            return str(value).strip(), name
    for array_name in EXTRA_ARRAYS:
        items = detail.get(array_name)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping):
                continue
            name = _first(item, ("ttruong", "ten", "name"))
            if normalize_lookup_key(name) not in URL_ALIASES:
                continue
            value = _first(item, ("dlieu", "value", "giaTri"))
            if is_safe_lookup_url(value):
                return str(value).strip(), f"{array_name}.{name}"
    return None


def _result(url, code, mode, matched_by, rule_id, url_source, code_source, warnings):
    safe_url = str(url).strip() if is_safe_lookup_url(url) else None
    if url and not safe_url:
        warnings.append("invalid_lookup_url")
    status = "resolved" if safe_url and (code or mode == "manual") else "partial" if safe_url or code else "unresolved"
    return InvoiceLookupResult(status, safe_url, code or None, mode, matched_by, rule_id, url_source, code_source, tuple(dict.fromkeys(warnings)))


def _with_query_value(base: str | None, key: str, value: str | None) -> str | None:
    if not base or not value:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{key}={quote(value, safe='')}"


def _xml_guid(value: str | bytes | Path | None) -> str | None:
    if value is None:
        return None
    try:
        if isinstance(value, Path) or isinstance(value, str) and Path(value).is_file():
            text = Path(value).read_text(encoding="utf-8", errors="replace")
        elif isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = str(value)
    except (OSError, ValueError):
        return None
    match = re.search(r"<(?:(?:\w+):)?DLHDon\b[^>]*\bId=[\"']([^\"']+)[\"']", text)
    return match.group(1).strip() if match else None


def _unwrap_detail(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    detail: Any = payload.get("detail", payload)
    if isinstance(detail, Mapping) and isinstance(detail.get("data_ct"), Mapping):
        detail = detail["data_ct"]
    return detail if isinstance(detail, Mapping) else {}


def _first(mapping: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = mapping.get(name)
        if value not in (None, ""):
            return value
    return None


__all__ = [
    "InvoiceLookupResult", "extract_lookup_code", "is_safe_lookup_url",
    "normalize_lookup_key", "normalize_tax_code", "resolve_invoice_lookup",
    "root_tax_code",
]
