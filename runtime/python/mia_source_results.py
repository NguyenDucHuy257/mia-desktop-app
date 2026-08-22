"""Source-native result presentation and Excel export for the desktop host.

Result tables deliberately use the same prepared workbook schemas as the
vendored source exporters. Column order and Vietnamese titles therefore come
from source XLSX templates rather than a desktop label map. Raw/path transport
fields never cross the local JSON-RPC presentation boundary.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable


logger = logging.getLogger("mia.excel_export")
ExportProgressCallback = Callable[[dict[str, Any]], None]


_RESULT_COUNT_CACHE: OrderedDict[tuple[Any, ...], int] = OrderedDict()
_RESULT_COUNT_CACHE_LIMIT = 64


BLOCKED_RESULT_FIELDS = {
    "password", "token", "authorization", "proxy", "proxy_url",
    "internal_session_id", "session_hash", "raw_json_path",
    "raw_detail_path", "xml_path", "database_path", "db_path",
    "detail_path",
}

ELECTRONIC_TEMPLATE_KEYS = (
    "stt", "khmshdon", "khhdon", "shdon", "tdlap",
    "nbmst", "nbten", "nmmst", "nmten", "nmdchi",
    "tgtcthue", "tgtthue", "ttcktmai", "tgtphi", "tgtttbso",
    "dvtte", "tgia", "tthai", "kqcht",
)
CASH_PURCHASE_TEMPLATE_KEYS = (
    "stt", "khmshdon", "khhdon", "shdon", "tdlap",
    "nbmst", "nbten", "nbdchi", "nmmst", "nmten", "nmcmnd",
    "tgtcthue", "tgtthue", "ttcktmai", "tgtttbso", "tthai", "kqcht",
)
CASH_SOLD_TEMPLATE_KEYS = (
    "stt", "khmshdon", "khhdon", "shdon", "tdlap",
    "nbmst", "nbten", "nmmst", "nmten", "nmdchi", "nmcmnd",
    "tgtcthue", "tgtthue", "ttcktmai", "tgtttbso", "tthai", "kqcht",
)

# openpyxl rejects these XML control characters when assigning a cell. Real
# portal/company text occasionally contains them, so sanitize only at the Excel
# presentation boundary and leave the source-owned SQLite/raw payload untouched.
_EXCEL_ILLEGAL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")


def _excel_safe_value(value: Any) -> Any:
    if isinstance(value, str):
        text = _EXCEL_ILLEGAL_CHARACTER_RE.sub("", value)
        return "'" + text if text.startswith("=") else text
    if isinstance(value, (dict, list, tuple, set)):
        text = json.dumps(value, ensure_ascii=False, default=str)
        text = _EXCEL_ILLEGAL_CHARACTER_RE.sub("", text)
        return "'" + text if text.startswith("=") else text
    return value


def _excel_safe_record(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _excel_safe_value(field) for key, field in value.items()}


class _ExcelSafeDetailRowBuilder:
    """Delegate source row construction, sanitizing only final Excel cells."""

    def __init__(self) -> None:
        from app.parsers.invoice_detail_excel_row_builder import InvoiceDetailExcelRowBuilder

        self._delegate = InvoiceDetailExcelRowBuilder()

    def build_rows(
        self, payload: dict[str, Any], record: dict[str, Any]
    ) -> list[dict[str, Any]]:
        return [
            _excel_safe_record(row)
            for row in self._delegate.build_rows(payload, record)
        ]


def _public_field_name(name: str) -> bool:
    key = str(name).casefold()
    return (
        key not in BLOCKED_RESULT_FIELDS
        and not key.startswith("raw_")
        and not key.endswith("_path")
    )


def _safe_fields(item: dict[str, Any]) -> dict[str, Any]:
    value = dict(item)
    attributes = value.pop("attributes", {})
    if isinstance(attributes, dict):
        for key, attribute in attributes.items():
            value.setdefault(str(key), attribute)
    return {
        str(key): field_value
        for key, field_value in value.items()
        if _public_field_name(str(key))
    }


def _search_matches(fields: dict[str, Any], search: str) -> bool:
    if not search:
        return True
    return search in json.dumps(
        fields, ensure_ascii=False, default=str, separators=(",", ":")
    ).casefold()


def _encode_page_cursor(direction_index: int, source_cursor: str | None) -> str:
    payload = json.dumps([1, direction_index, source_cursor], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).rstrip(b"=").decode("ascii")


def _decode_page_cursor(value: str | None) -> tuple[int, str | None]:
    if not value:
        return 0, None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception as error:
        raise ValueError("invalid_result_cursor") from error
    if (
        not isinstance(decoded, list)
        or len(decoded) != 3
        or decoded[0] != 1
        or not isinstance(decoded[1], int)
        or decoded[1] < 0
        or (decoded[2] is not None and not isinstance(decoded[2], str))
    ):
        raise ValueError("invalid_result_cursor")
    return decoded[1], decoded[2]


def _source_template_dir() -> Path:
    return (
        Path(__file__).resolve().parent
        / "vendor" / "mia_crawl_service" / "resources" / "templates"
    )


def _result_context(backend, query: dict[str, Any]):
    from app.external_api.results import JobResultReader

    base_job = backend._result_job(str(query["connection_id"]))
    if base_job is None:
        return None

    date_from = str(query.get("date_from") or base_job.parameters["date_from"])
    date_to = str(query.get("date_to") or base_job.parameters["date_to"])
    if date.fromisoformat(date_from) > date.fromisoformat(date_to):
        raise ValueError("invalid_result_range")

    selected_direction = query.get("direction")
    if selected_direction not in (None, "purchase", "sold"):
        raise ValueError("invalid_result_direction")
    base_directions = list(
        base_job.parameters.get("directions") or ("purchase", "sold")
    )
    directions = (
        [str(selected_direction)]
        if selected_direction
        else [
            direction for direction in ("purchase", "sold")
            if direction in set(base_directions)
        ]
    )
    if not directions:
        directions = base_directions

    selected_query_type = query.get("query_type")
    if selected_query_type not in (None, "query", "sco-query"):
        raise ValueError("invalid_result_query_type")
    base_query_types = list(
        base_job.parameters.get("query_types") or ("query", "sco-query")
    )
    query_types = (
        [str(selected_query_type)] if selected_query_type else base_query_types
    )

    database_path = (
        backend.data_root / base_job.company_tax_code / "db" / "invoices.sqlite3"
    )
    return {
        "base_job": base_job,
        "date_from": date_from,
        "date_to": date_to,
        "directions": directions,
        "query_types": query_types,
        "reader": JobResultReader(database_path),
        "database_path": database_path,
    }


def _job_for(context, direction: str, *, query_types: list[str] | None = None):
    base_job = context["base_job"]
    return replace(
        base_job,
        parameters={
            **base_job.parameters,
            "date_from": context["date_from"],
            "date_to": context["date_to"],
            "directions": [direction],
            "query_types": list(query_types or context["query_types"]),
        },
    )


def _overview_template_keys(category: str, direction: str) -> tuple[str, ...]:
    if category == "electronic":
        return ELECTRONIC_TEMPLATE_KEYS
    if category == "cash_register" and direction == "purchase":
        return CASH_PURCHASE_TEMPLATE_KEYS
    if category == "cash_register" and direction == "sold":
        return CASH_SOLD_TEMPLATE_KEYS
    raise ValueError("invalid_overview_template")


@lru_cache(maxsize=8)
def _overview_template_schema(
    category: str, direction: str
) -> tuple[tuple[str, str], ...]:
    from openpyxl import load_workbook
    from app.services.overview_downloader import OverviewDownloader, _find_header_row

    renderer = OverviewDownloader(
        crawler=None,
        headers_provider=lambda: {},
        template_dir=_source_template_dir(),
    )
    template_path = renderer._template_path(category, direction)
    if not template_path.is_file():
        raise FileNotFoundError(f"Missing source overview template: {template_path}")

    workbook = load_workbook(template_path, read_only=True, data_only=False)
    try:
        sheet = workbook.active
        header_row = _find_header_row(sheet)
        titles = [
            str(sheet.cell(header_row, column).value or "").strip()
            for column in range(1, sheet.max_column + 1)
        ]
    finally:
        workbook.close()

    keys = _overview_template_keys(category, direction)
    if len(titles) != len(keys):
        raise RuntimeError(
            f"Source overview template schema mismatch: {category}/{direction} "
            f"has {len(titles)} headers, expected {len(keys)}"
        )
    return tuple((key, title or key) for key, title in zip(keys, titles))


@lru_cache(maxsize=1)
def _detail_template_schema() -> tuple[tuple[str, str], ...]:
    from openpyxl import load_workbook
    from app.exporters.invoice_detail_excel_exporter import InvoiceDetailExcelExporter

    template_path = _source_template_dir() / "invoice_detail.xlsx"
    if not template_path.is_file():
        raise FileNotFoundError(f"Missing source detail template: {template_path}")

    workbook = load_workbook(template_path, read_only=False, data_only=False)
    try:
        sheet = workbook.worksheets[0]
        header_row = InvoiceDetailExcelExporter._find_header_row(sheet)
        if header_row < 5:
            sheet.insert_rows(header_row, amount=5 - header_row)
            header_row = 5
        InvoiceDetailExcelExporter._ensure_serial_number_column(sheet, header_row)
        keys = InvoiceDetailExcelExporter._column_keys(sheet, header_row)
        titles = [
            str(sheet.cell(header_row, column).value or "").strip()
            for column in range(1, sheet.max_column + 1)
        ]
    finally:
        workbook.close()

    if len(keys) != len(titles):
        raise RuntimeError("Source detail template schema mismatch")
    return tuple((key, title or key) for key, title in zip(keys, titles))


def _result_schema(kind: str, context) -> tuple[tuple[str, str], ...]:
    if kind == "details":
        return _detail_template_schema()

    from app.config.crawl_config import query_type_to_category

    query_type = context["query_types"][0]
    context["query_types"] = [query_type]
    category = query_type_to_category(query_type)
    direction = context["directions"][0] if context["directions"] else "purchase"
    return _overview_template_schema(category, direction)


def _overview_display_value(
    fields: dict[str, Any], key: str, *, category: str
) -> Any:
    from app.services.overview_downloader import INVOICE_STATUS_LABELS, OverviewDownloader

    if key == "stt":
        return fields.get("stt")
    if key == "tdlap":
        source = fields.get("tdlap") or fields.get("nlap") or fields.get("nlap_date")
        return OverviewDownloader._format_portal_date(source)
    if key == "tthai":
        value = fields.get(key)
        return INVOICE_STATUS_LABELS.get(value, str(value or ""))
    if key == "kqcht" and category == "cash_register":
        return (
            fields.get(key)
            or "Cục Thuế đã nhận hóa đơn có mã khởi tạo từ máy tính tiền"
        )
    return fields.get(key)


def _project_fields(
    kind: str,
    fields: dict[str, Any],
    schema: tuple[tuple[str, str], ...],
    context,
) -> dict[str, Any]:
    if kind == "details":
        return {key: fields.get(key) for key, _ in schema}

    from app.config.crawl_config import query_type_to_category

    category = query_type_to_category(context["query_types"][0])
    return {
        key: _overview_display_value(fields, key, category=category)
        for key, _ in schema
    }


def read_results(backend, kind: str, query: dict[str, Any]) -> dict[str, Any]:
    if kind not in {"overview", "details"}:
        raise ValueError("invalid_result_kind")
    limit = int(query.get("limit", 50))
    if not 1 <= limit <= 50:
        raise ValueError("invalid_result_limit")

    context = _result_context(backend, query)
    if context is None:
        return {
            "items": [], "columns": [], "column_labels": {},
            "total_count": 0,
            "pagination": {"limit": limit, "has_more": False, "next_cursor": None},
        }

    schema = _result_schema(kind, context)
    columns = [key for key, _ in schema]
    column_labels = {key: title for key, title in schema}
    search = str(query.get("search") or "").strip().casefold()
    direction_index, source_cursor = _decode_page_cursor(query.get("cursor"))
    directions = context["directions"]

    if direction_index >= len(directions):
        return {
            "items": [], "columns": columns, "column_labels": column_labels,
            "total_count": _result_total_count(kind, context, search),
            "pagination": {"limit": limit, "has_more": False, "next_cursor": None},
        }

    output: list[dict[str, Any]] = []
    next_direction_index = direction_index
    next_source_cursor = source_cursor
    has_more = False

    while len(output) < limit and next_direction_index < len(directions):
        direction = directions[next_direction_index]
        job = _job_for(context, direction)
        remaining = max(1, limit - len(output))
        page = (
            context["reader"].overview_page(
                job, limit=remaining, cursor=next_source_cursor
            )
            if kind == "overview"
            else context["reader"].detail_page(
                job, limit=remaining, cursor=next_source_cursor
            )
        )

        for item in page.get("items") or ():
            safe = _safe_fields(item)
            safe["direction"] = direction
            if not _search_matches(safe, search):
                continue
            raw_id = safe.get("id")
            if isinstance(raw_id, int):
                row_id: int | str = raw_id
            else:
                fingerprint = json.dumps(
                    safe, ensure_ascii=False, default=str, sort_keys=True
                ).encode("utf-8")
                row_id = hashlib.sha256(fingerprint).hexdigest()[:20]
            output.append({
                "row_id": row_id,
                "direction": direction,
                "fields": _project_fields(kind, safe, schema, context),
            })

        pagination = page.get("pagination") or {}
        page_more = bool(pagination.get("has_more"))
        page_cursor = pagination.get("next_cursor")
        if page_more and page_cursor:
            next_source_cursor = str(page_cursor)
            has_more = True
            if len(output) >= limit:
                break
            continue

        next_direction_index += 1
        next_source_cursor = None
        has_more = next_direction_index < len(directions)

    response: dict[str, Any] = {
        "items": output[:limit],
        "columns": columns,
        "column_labels": column_labels,
        "pagination": {
            "limit": limit,
            "has_more": bool(has_more),
            "next_cursor": (
                _encode_page_cursor(next_direction_index, next_source_cursor)
                if has_more else None
            ),
        },
    }

    response["total_count"] = _result_total_count(kind, context, search)
    return response


def _all_overview_fields(
    context,
    direction: str,
    query_type: str,
    search: str,
) -> list[dict[str, Any]]:
    job = _job_for(context, direction, query_types=[query_type])
    cursor = None
    rows: list[dict[str, Any]] = []
    while True:
        page = context["reader"].overview_page(job, limit=200, cursor=cursor)
        for item in page.get("items") or ():
            fields = _safe_fields(item)
            fields["direction"] = direction
            if _search_matches(fields, search):
                fields.setdefault(
                    "tdlap", fields.get("nlap") or fields.get("nlap_date")
                )
                rows.append(fields)
        pagination = page.get("pagination") or {}
        if not pagination.get("has_more") or not pagination.get("next_cursor"):
            return rows
        cursor = str(pagination["next_cursor"])


def _available_path(destination: Path, filename: str) -> Path:
    candidate = destination / filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for copy_index in range(2, 1001):
        if not candidate.exists():
            return candidate
        candidate = destination / f"{stem} ({copy_index}){suffix}"
    raise OSError("artifact_name_exhausted")


def _grouped_result_filename(
    company_tax_code: str,
    direction: str,
    scope: str,
    date_from: str,
    date_to: str,
) -> str:
    safe_tax_code = re.sub(r"[^0-9A-Za-z._-]+", "_", company_tax_code).strip("._-")
    if not safe_tax_code:
        safe_tax_code = "MIA"
    direction_label = "Mua vào" if direction == "purchase" else "Bán ra"
    scope_label = "Tổng quan" if scope == "overview" else "Chi tiết"
    return (
        f"{safe_tax_code} - {direction_label} - {scope_label} - "
        f"{date_from}_{date_to}.xlsx"
    )


def _database_revision(database_path: Path) -> tuple[tuple[int, int], ...]:
    """Track SQLite and WAL writes so cached presentation counts stay fresh."""
    revision: list[tuple[int, int]] = []
    for path in (database_path, Path(f"{database_path}-wal")):
        try:
            stat = path.stat()
        except OSError:
            revision.append((0, 0))
        else:
            revision.append((stat.st_mtime_ns, stat.st_size))
    return tuple(revision)


def _cached_result_count(key: tuple[Any, ...], factory: Callable[[], int]) -> int:
    cached = _RESULT_COUNT_CACHE.get(key)
    if cached is not None:
        _RESULT_COUNT_CACHE.move_to_end(key)
        return cached
    value = factory()
    _RESULT_COUNT_CACHE[key] = value
    _RESULT_COUNT_CACHE.move_to_end(key)
    while len(_RESULT_COUNT_CACHE) > _RESULT_COUNT_CACHE_LIMIT:
        _RESULT_COUNT_CACHE.popitem(last=False)
    return value


def _normalized_detail_row_count(context, job) -> int | None:
    """Return the displayed line count when all available details are normalized.

    Mixed/legacy datasets return ``None`` so the caller can delegate row
    expansion to the authoritative source reader instead of guessing from the
    invoice count.
    """
    database_path = Path(context["database_path"])
    if not database_path.is_file():
        return None
    directions = tuple(job.parameters["directions"])
    query_types = tuple(job.parameters["query_types"])
    direction_slots = ",".join("?" for _ in directions)
    query_slots = ",".join("?" for _ in query_types)
    params = [
        job.company_tax_code,
        job.parameters["date_from"],
        job.parameters["date_to"],
        *directions,
        *query_types,
    ]
    try:
        with sqlite3.connect(database_path) as connection:
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(invoice_detail_items)")
            }
            line_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='invoice_detail_lines'"
            ).fetchone()
            if "normalized_ready" not in columns or not line_table:
                return None
            normalized, legacy = connection.execute(f"""
                SELECT
                    COALESCE(SUM(CASE WHEN normalized_ready=1 THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN normalized_ready<>1 THEN 1 ELSE 0 END), 0)
                FROM invoice_detail_items
                WHERE company_tax_code=? AND nlap_date BETWEEN ? AND ?
                  AND direction IN ({direction_slots})
                  AND query_type IN ({query_slots})
                  AND (error_message IS NULL OR TRIM(error_message)='')
                  AND (normalized_ready=1 OR TRIM(raw_detail_path)<>'')
            """, params).fetchone()
            if int(legacy) > 0:
                return None
            if int(normalized) == 0:
                return 0
            row = connection.execute(f"""
                SELECT COUNT(*)
                FROM invoice_detail_items d
                JOIN invoice_detail_lines l ON l.detail_item_id=d.id
                WHERE d.company_tax_code=? AND d.nlap_date BETWEEN ? AND ?
                  AND d.direction IN ({direction_slots})
                  AND d.query_type IN ({query_slots})
                  AND d.normalized_ready=1
                  AND (d.error_message IS NULL OR TRIM(d.error_message)='')
            """, params).fetchone()
            return int(row[0])
    except sqlite3.Error:
        return None


def _result_total_count(kind: str, context, search: str) -> int:
    database_path = Path(context["database_path"])
    base_job = context["base_job"]
    key = (
        kind,
        str(database_path.resolve()),
        _database_revision(database_path),
        base_job.company_tax_code,
        context["date_from"],
        context["date_to"],
        tuple(context["directions"]),
        tuple(context["query_types"]),
        search,
    )

    def count() -> int:
        total = 0
        for direction in context["directions"]:
            job = _job_for(context, direction)
            if kind == "overview" and not search:
                page = context["reader"].overview_page(job, limit=1, cursor=None)
                total += int(page.get("total_count") or 0)
                continue
            if kind == "details" and not search:
                normalized_count = _normalized_detail_row_count(context, job)
                if normalized_count is not None:
                    total += normalized_count
                    continue

            cursor = None
            while True:
                page = (
                    context["reader"].overview_page(job, limit=50, cursor=cursor)
                    if kind == "overview"
                    else context["reader"].detail_page(job, limit=50, cursor=cursor)
                )
                for item in page.get("items") or ():
                    fields = _safe_fields(item)
                    fields["direction"] = direction
                    if _search_matches(fields, search):
                        total += 1
                pagination = page.get("pagination") or {}
                if not pagination.get("has_more") or not pagination.get("next_cursor"):
                    break
                cursor = str(pagination["next_cursor"])
        return total

    return _cached_result_count(key, count)


def _combine_source_workbooks_atomically(
    staged_jobs: list[tuple[str, str, Path]],
    target: Path,
) -> None:
    """Reuse source styled-sheet copying while keeping desktop writes atomic."""
    from app.services.overview_downloader import OverviewDownloader

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".xlsx", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink(missing_ok=True)
    try:
        OverviewDownloader.combine_workbooks(staged_jobs, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


class _ExportProgressReporter:
    """Map actual workbook work units to one monotonic request percentage."""

    _DETAIL_PHASES = {
        "load_template": (0.00, 0.05),
        "build_rows": (0.05, 0.35),
        "write_rows": (0.35, 0.75),
        "format": (0.75, 0.92),
        "save": (0.92, 1.00),
    }
    _OVERVIEW_PHASES = {
        "load_template": (0.00, 0.10),
        "write_rows": (0.10, 0.80),
        "save": (0.80, 1.00),
    }

    def __init__(self, callback: ExportProgressCallback | None) -> None:
        self.callback = callback
        self.unit_total = 0
        self.unit_index = 0
        self.last_percent = 0.0
        self.scope: str | None = None
        self.phase = "prepare"

    def planning(self, processed: int, total: int) -> None:
        fraction = self._fraction(processed, total)
        self._send("query", processed, total, fraction * 5.0)

    def set_units(self, total: int) -> None:
        self.unit_total = max(1, int(total))
        self.unit_index = 0

    def start_unit(self, scope: str) -> None:
        self.scope = scope

    def unit(self, phase: str, processed: int, total: int) -> None:
        phases = self._DETAIL_PHASES if self.scope == "details" else self._OVERVIEW_PHASES
        start, end = phases.get(phase, (0.0, 0.0))
        local = start + ((end - start) * self._fraction(processed, total))
        percent = 5.0 + (
            ((self.unit_index + local) / max(1, self.unit_total)) * 95.0
        )
        self._send(phase, processed, total, percent)

    def complete_unit(self) -> None:
        self.unit_index = min(self.unit_total, self.unit_index + 1)

    def complete(self) -> None:
        self._send("completed", 1, 1, 100.0, status="completed")

    def failed(self) -> None:
        self._send(self.phase, 0, 0, self.last_percent, status="failed")

    @staticmethod
    def _fraction(processed: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return max(0.0, min(1.0, processed / total))

    def _send(
        self,
        phase: str,
        processed: int,
        total: int,
        percent: float,
        *,
        status: str = "running",
    ) -> None:
        self.phase = phase
        self.last_percent = max(self.last_percent, min(100.0, max(0.0, percent)))
        if self.callback is None:
            return
        event = {
            "status": status,
            "scope": self.scope,
            "phase": phase,
            "processed": max(0, int(processed)),
            "total": max(0, int(total)),
            "percent": round(self.last_percent, 3),
        }
        try:
            self.callback(event)
        except Exception:
            logger.exception("excel_export_progress_transport_failed phase=%s", phase)


def _write_overview_excel_from_source_template(
    rows: list[dict[str, Any]],
    *,
    direction: str,
    category: str,
    date_from: str,
    date_to: str,
    target: Path,
    progress: Callable[[str, int, int], None] | None = None,
) -> None:
    import app.services.overview_downloader as overview_module
    from app.services.overview_downloader import OverviewDownloader

    renderer = OverviewDownloader(
        crawler=None,
        headers_provider=lambda: {},
        template_dir=_source_template_dir(),
    )
    begin = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    safe_rows = [_excel_safe_record(row) for row in rows]
    original_load_workbook = overview_module._load_workbook
    original_clone_cell = overview_module._clone_cell
    column_count = len(_overview_template_keys(category, direction))
    timing = {"load": 0.0, "write": 0.0, "save": 0.0}
    cloned_cells = 0
    last_emitted_row = 1
    write_started_at: float | None = None

    def measured_load_workbook(*args, **kwargs):
        nonlocal write_started_at
        started = time.perf_counter()
        workbook = original_load_workbook(*args, **kwargs)
        timing["load"] += time.perf_counter() - started
        if progress is not None:
            progress("load_template", 1, 1)
            progress("write_rows", min(1, len(rows)), len(rows))
        write_started_at = time.perf_counter()
        original_save = workbook.save

        def measured_save(*save_args, **save_kwargs):
            if write_started_at is not None:
                timing["write"] += time.perf_counter() - write_started_at
            if progress is not None:
                progress("write_rows", len(rows), len(rows))
                progress("save", 0, 1)
            save_started = time.perf_counter()
            try:
                return original_save(*save_args, **save_kwargs)
            finally:
                timing["save"] += time.perf_counter() - save_started

        workbook.save = measured_save
        return workbook

    def measured_clone_cell(source, target_cell):
        nonlocal cloned_cells, last_emitted_row
        result = original_clone_cell(source, target_cell)
        cloned_cells += 1
        if progress is not None and column_count > 0:
            completed_rows = min(len(rows), 1 + (cloned_cells // column_count))
            emit_every = max(1, len(rows) // 200)
            if (
                completed_rows == len(rows)
                or completed_rows - last_emitted_row >= emit_every
            ):
                last_emitted_row = completed_rows
                progress("write_rows", completed_rows, len(rows))
        return result

    if progress is not None:
        progress("load_template", 0, 1)
    overview_module._load_workbook = measured_load_workbook
    overview_module._clone_cell = measured_clone_cell
    total_started = time.perf_counter()
    try:
        if category == "electronic":
            content = renderer._electronic_records_to_xlsx(
                safe_rows, direction, begin, end
            )
        elif category == "cash_register":
            content = renderer._cash_records_to_xlsx(
                safe_rows, direction, begin, end
            )
        else:
            raise ValueError("invalid_overview_export_category")
    finally:
        overview_module._load_workbook = original_load_workbook
        overview_module._clone_cell = original_clone_cell

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        save_started = time.perf_counter()
        temporary.write_bytes(content)
        temporary.replace(target)
        timing["save"] += time.perf_counter() - save_started
        if progress is not None:
            progress("save", 1, 1)
    finally:
        temporary.unlink(missing_ok=True)
    logger.info(
        "excel_export_summary scope=overview invoice_count=%s row_count=%s "
        "column_count=%s template_load_ms=%.1f worksheet_write_ms=%.1f "
        "workbook_save_ms=%.1f total_ms=%.1f",
        len(rows), len(rows), column_count, timing["load"] * 1000,
        timing["write"] * 1000, timing["save"] * 1000,
        (time.perf_counter() - total_started) * 1000,
    )


def _filter_detail_records(
    records: list[dict[str, Any]], search: str
) -> list[dict[str, Any]]:
    if not search:
        return records
    result: list[dict[str, Any]] = []
    for record in records:
        searchable = json.dumps(record, ensure_ascii=False, default=str).casefold()
        if search not in searchable:
            raw_path = Path(str(record.get("raw_detail_path") or ""))
            try:
                searchable = raw_path.read_text(encoding="utf-8").casefold()
            except (OSError, UnicodeError):
                searchable = ""
        if search in searchable:
            result.append(record)
    return result


def _export_results_impl(
    backend,
    value: dict[str, Any],
    reporter: _ExportProgressReporter,
) -> dict[str, Any]:
    """Build Excel on demand from persisted source-owned data only."""
    from app.config.crawl_config import QUERY_TYPE_TO_CATEGORY
    from app.exporters.invoice_detail_excel_exporter import InvoiceDetailExcelExporter
    from app.repositories.invoice_detail_query_repository import InvoiceDetailQueryRepository

    destination = Path(value["destination"])
    if not destination.is_absolute():
        raise ValueError("invalid_artifact_directory")

    scopes = value.get("result_scopes") or []
    if (
        not isinstance(scopes, list)
        or not scopes
        or set(scopes) - {"overview", "details"}
    ):
        raise ValueError("invalid_result_export_scope")
    connection_ids = value.get("connection_ids") or []
    if len(connection_ids) != 1:
        raise ValueError("invalid_result_export_account")

    query = {
        "connection_id": str(connection_ids[0]),
        "date_from": str(value["date_from"]),
        "date_to": str(value["date_to"]),
        "direction": value.get("direction"),
        "query_type": value.get("query_type"),
    }
    context_started = time.perf_counter()
    context = _result_context(backend, query)
    logger.info(
        "excel_export_phase scope=all phase=result_context duration_ms=%.1f",
        (time.perf_counter() - context_started) * 1000,
    )
    if context is None:
        raise ValueError("result_job_not_found")

    search = str(value.get("search") or "").strip().casefold()
    destination.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    combinations = [
        (direction, query_type)
        for direction in context["directions"]
        for query_type in context["query_types"]
    ]
    candidate_total = len(combinations) * len(scopes)
    candidate_index = 0
    plans: list[dict[str, Any]] = []
    detail_repository = InvoiceDetailQueryRepository(context["database_path"])

    # Discover real, non-empty workbook units before rendering so mixed scope
    # progress never resets and never counts a workbook that will not exist.
    for scope in scopes:
        for direction, query_type in combinations:
            query_started = time.perf_counter()
            if scope == "overview":
                payload = _all_overview_fields(context, direction, query_type, search)
            else:
                payload = detail_repository.get_detail_records_for_export(
                    context["base_job"].company_tax_code,
                    direction,
                    query_type,
                    context["date_from"],
                    context["date_to"],
                )
                payload = _filter_detail_records(payload, search)
            query_seconds = time.perf_counter() - query_started
            candidate_index += 1
            reporter.planning(candidate_index, candidate_total)
            logger.info(
                "excel_export_phase scope=%s phase=query record_count=%s duration_ms=%.1f",
                scope, len(payload), query_seconds * 1000,
            )
            if payload:
                plans.append({
                    "scope": scope,
                    "direction": direction,
                    "query_type": query_type,
                    "payload": payload,
                })

    if not plans:
        raise ValueError("result_export_empty")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for plan in plans:
        groups.setdefault((plan["scope"], plan["direction"]), []).append(plan)

    # One unit per rendered source sheet plus one real combine/save unit for
    # each final workbook. Query types no longer imply separate final files.
    reporter.set_units(len(plans) + len(groups))

    with tempfile.TemporaryDirectory(prefix=".mia-result-sheets-", dir=destination) as directory:
        staging_directory = Path(directory)
        for (scope, direction), group_plans in groups.items():
            staged_jobs: list[tuple[str, str, Path]] = []
            for sheet_index, plan in enumerate(group_plans, start=1):
                query_type = plan["query_type"]
                category = str(QUERY_TYPE_TO_CATEGORY.get(query_type) or "")
                if category not in {"electronic", "cash_register"}:
                    raise ValueError("invalid_overview_export_category")
                reporter.start_unit(scope)
                staged_target = staging_directory / (
                    f"{scope}-{direction}-{category}-{sheet_index}.xlsx"
                )
                if scope == "overview":
                    _write_overview_excel_from_source_template(
                        plan["payload"],
                        direction=direction,
                        category=category,
                        date_from=context["date_from"],
                        date_to=context["date_to"],
                        target=staged_target,
                        progress=reporter.unit,
                    )
                else:
                    if reporter.callback is None:
                        exporter = InvoiceDetailExcelExporter(
                            _source_template_dir() / "invoice_detail.xlsx",
                            row_builder=_ExcelSafeDetailRowBuilder(),
                        )
                    else:
                        from mia_progressive_excel_exporter import (
                            ProgressiveInvoiceDetailExcelExporter,
                        )
                        exporter = ProgressiveInvoiceDetailExcelExporter(
                            _source_template_dir() / "invoice_detail.xlsx",
                            row_builder=_ExcelSafeDetailRowBuilder(),
                            progress=reporter.unit,
                        )
                    exporter.export(
                        detail_records=plan["payload"],
                        output_path=staged_target,
                        from_date=context["date_from"],
                        to_date=context["date_to"],
                    )
                combine_category = category
                if scope == "details":
                    # Source's cash_register branch intentionally reuses one
                    # Overview prototype style per column. Detail workbooks can
                    # carry row-specific styles/merges, so pass the source sheet
                    # label as the presentation category and let the same
                    # combiner take its full-cell clone path.
                    combine_category = {
                        "electronic": "Hóa đơn điện tử",
                        "cash_register": "Máy tính tiền",
                    }[category]
                staged_jobs.append((direction, combine_category, staged_target))
                reporter.complete_unit()

            reporter.start_unit(scope)
            reporter.unit("format", 0, len(staged_jobs))
            target = _available_path(
                destination,
                _grouped_result_filename(
                    context["base_job"].company_tax_code,
                    direction,
                    scope,
                    context["date_from"],
                    context["date_to"],
                ),
            )
            _combine_source_workbooks_atomically(staged_jobs, target)
            reporter.unit("format", len(staged_jobs), len(staged_jobs))
            reporter.unit("save", 1, 1)
            reporter.complete_unit()
            files.append(str(target))

    reporter.complete()
    return {"count": len(files), "files": files}


def export_results(
    backend,
    value: dict[str, Any],
    *,
    progress_callback: ExportProgressCallback | None = None,
) -> dict[str, Any]:
    reporter = _ExportProgressReporter(progress_callback)
    started = time.perf_counter()
    try:
        return _export_results_impl(backend, value, reporter)
    except Exception:
        reporter.failed()
        raise
    finally:
        logger.info(
            "excel_export_phase scope=all phase=total duration_ms=%.1f",
            (time.perf_counter() - started) * 1000,
        )


__all__ = ["export_results", "read_results"]
