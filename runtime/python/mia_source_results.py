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
import os
import tempfile
from dataclasses import replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any


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

    if kind == "overview" and not search:
        total_count = 0
        for direction in directions:
            count_page = context["reader"].overview_page(
                _job_for(context, direction), limit=1, cursor=None
            )
            total_count += int(count_page.get("total_count") or 0)
        response["total_count"] = total_count
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
    for copy_index in range(1, 1000):
        if not candidate.exists():
            return candidate
        candidate = destination / f"{stem} ({copy_index}){suffix}"
    raise OSError("artifact_name_exhausted")


def _write_overview_excel_from_source_template(
    rows: list[dict[str, Any]],
    *,
    direction: str,
    category: str,
    date_from: str,
    date_to: str,
    target: Path,
) -> None:
    from app.services.overview_downloader import OverviewDownloader

    renderer = OverviewDownloader(
        crawler=None,
        headers_provider=lambda: {},
        template_dir=_source_template_dir(),
    )
    begin = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if category == "electronic":
        content = renderer._electronic_records_to_xlsx(rows, direction, begin, end)
    elif category == "cash_register":
        content = renderer._cash_records_to_xlsx(rows, direction, begin, end)
    else:
        raise ValueError("invalid_overview_export_category")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_bytes(content)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


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


def export_results(backend, value: dict[str, Any]) -> dict[str, Any]:
    """Build Excel on demand from persisted source-owned data only."""
    from app.config.crawl_config import QUERY_TYPE_TO_CATEGORY
    from app.exporters.invoice_detail_excel_exporter import InvoiceDetailExcelExporter
    from app.repositories.invoice_detail_query_repository import InvoiceDetailQueryRepository
    from app.services.overview_downloader import OUTPUT_NAMES

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
    context = _result_context(backend, query)
    if context is None:
        raise ValueError("result_job_not_found")

    search = str(value.get("search") or "").strip().casefold()
    destination.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    if "overview" in scopes:
        for direction in context["directions"]:
            for query_type in context["query_types"]:
                rows = _all_overview_fields(context, direction, query_type, search)
                if not rows:
                    continue
                category = QUERY_TYPE_TO_CATEGORY.get(query_type)
                source_name = OUTPUT_NAMES.get((direction, category))
                if not source_name:
                    source_name = f"DANH SÁCH HÓA ĐƠN {direction} {query_type}.xlsx"
                target = _available_path(destination, source_name)
                _write_overview_excel_from_source_template(
                    rows,
                    direction=direction,
                    category=str(category),
                    date_from=context["date_from"],
                    date_to=context["date_to"],
                    target=target,
                )
                files.append(str(target))

    if "details" in scopes:
        repository = InvoiceDetailQueryRepository(context["database_path"])
        exporter = InvoiceDetailExcelExporter(
            _source_template_dir() / "invoice_detail.xlsx"
        )
        for direction in context["directions"]:
            for query_type in context["query_types"]:
                records = repository.get_detail_records_for_export(
                    context["base_job"].company_tax_code,
                    direction,
                    query_type,
                    context["date_from"],
                    context["date_to"],
                )
                records = _filter_detail_records(records, search)
                if not records:
                    continue
                direction_label = "MUA VÀO" if direction == "purchase" else "BÁN RA"
                type_label = "HĐĐT" if query_type == "query" else "MÁY TÍNH TIỀN"
                target = _available_path(
                    destination,
                    f"THỐNG KÊ CHI TIẾT HÓA ĐƠN "
                    f"{direction_label} - {type_label}.xlsx",
                )
                exporter.export(
                    detail_records=records,
                    output_path=target,
                    from_date=context["date_from"],
                    to_date=context["date_to"],
                )
                files.append(str(target))

    if not files:
        raise ValueError("result_export_empty")
    return {"count": len(files), "files": files}


__all__ = ["export_results", "read_results"]
