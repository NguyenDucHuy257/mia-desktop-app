"""Source-native result presentation and Excel export for the desktop host.

Result paging comes from the vendored JobResultReader. Overview rows are
flattened from the source database row plus invoice_overview_attributes and only
filesystem/raw transport fields are removed. Detail Excel is rendered by the
source InvoiceDetailExcelExporter against persisted source detail records.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any


BLOCKED_RESULT_FIELDS = {
    "password", "token", "authorization", "proxy", "proxy_url",
    "internal_session_id", "session_hash", "raw_json_path",
    "raw_detail_path", "xml_path", "database_path", "db_path",
    "detail_path",
}

OVERVIEW_BASE_COLUMNS = (
    "id", "company_tax_code", "direction", "query_type", "invoice_category",
    "nbmst", "khhdon", "shdon", "khmshdon", "nlap", "nlap_date",
    "detail_fetched", "created_at", "updated_at",
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
        not isinstance(decoded, list) or len(decoded) != 3
        or decoded[0] != 1 or not isinstance(decoded[1], int)
        or decoded[1] < 0
        or (decoded[2] is not None and not isinstance(decoded[2], str))
    ):
        raise ValueError("invalid_result_cursor")
    return decoded[1], decoded[2]


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
    directions = (
        [str(selected_direction)]
        if selected_direction
        else [
            direction for direction in ("purchase", "sold")
            if direction in set(base_job.parameters.get("directions") or ())
        ]
    )
    if not directions:
        directions = list(base_job.parameters.get("directions") or ("purchase", "sold"))
    query_types = list(base_job.parameters.get("query_types") or ("query", "sco-query"))
    database_path = backend.data_root / base_job.company_tax_code / "db" / "invoices.sqlite3"
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


def _overview_columns(context) -> list[str]:
    columns = [name for name in OVERVIEW_BASE_COLUMNS if _public_field_name(name)]
    database_path = context["database_path"]
    if not database_path.is_file():
        return columns
    directions = list(context["directions"])
    query_types = list(context["query_types"])
    if not directions or not query_types:
        return columns
    direction_marks = ",".join("?" for _ in directions)
    query_marks = ",".join("?" for _ in query_types)
    params = [
        context["base_job"].company_tax_code,
        context["date_from"], context["date_to"],
        *directions, *query_types,
    ]
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='invoice_overview_attributes'"
            ).fetchone()
            if not table:
                return columns
            rows = connection.execute(f"""
                SELECT DISTINCT a.field_name
                FROM invoice_overview_attributes a
                JOIN invoice_overview_items i ON i.id=a.invoice_item_id
                WHERE i.company_tax_code=? AND i.nlap_date BETWEEN ? AND ?
                  AND i.direction IN ({direction_marks})
                  AND i.query_type IN ({query_marks})
                ORDER BY a.field_name
            """, params).fetchall()
    except sqlite3.Error:
        return columns
    seen = set(columns)
    for row in rows:
        name = str(row[0])
        if _public_field_name(name) and name not in seen:
            seen.add(name)
            columns.append(name)
    return columns


def _detail_columns() -> list[str]:
    from app.repositories.invoice_detail_repository import DETAIL_LINE_FIELDS

    return ["direction", "stt", *[
        name for name in DETAIL_LINE_FIELDS if _public_field_name(name)
    ]]


def read_results(backend, kind: str, query: dict[str, Any]) -> dict[str, Any]:
    """Page public source DB records, preserving exact source values."""
    if kind not in {"overview", "details"}:
        raise ValueError("invalid_result_kind")
    limit = int(query.get("limit", 50))
    if not 1 <= limit <= 50:
        raise ValueError("invalid_result_limit")
    context = _result_context(backend, query)
    if context is None:
        return {
            "items": [], "columns": [],
            "pagination": {"limit": limit, "has_more": False, "next_cursor": None},
        }

    search = str(query.get("search") or "").strip().casefold()
    direction_index, source_cursor = _decode_page_cursor(query.get("cursor"))
    directions = context["directions"]
    columns = _overview_columns(context) if kind == "overview" else _detail_columns()
    if direction_index >= len(directions):
        return {
            "items": [], "columns": columns,
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
            context["reader"].overview_page(job, limit=remaining, cursor=next_source_cursor)
            if kind == "overview"
            else context["reader"].detail_page(job, limit=remaining, cursor=next_source_cursor)
        )

        for item in page.get("items") or ():
            fields = _safe_fields(item)
            # Detail rows do not carry direction in the source public row; the
            # source reader is intentionally invoked with one direction at a
            # time so this value remains exact rather than inferred.
            fields["direction"] = direction
            if not _search_matches(fields, search):
                continue
            raw_id = fields.get("id")
            if isinstance(raw_id, int):
                row_id: int | str = raw_id
            else:
                fingerprint = json.dumps(
                    fields, ensure_ascii=False, default=str, sort_keys=True
                ).encode("utf-8")
                row_id = hashlib.sha256(fingerprint).hexdigest()[:20]
            output.append({"row_id": row_id, "direction": direction, "fields": fields})

        page_more = bool((page.get("pagination") or {}).get("has_more"))
        page_cursor = (page.get("pagination") or {}).get("next_cursor")
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
        "pagination": {
            "limit": limit,
            "has_more": bool(has_more),
            "next_cursor": (
                _encode_page_cursor(next_direction_index, next_source_cursor)
                if has_more else None
            ),
        },
    }

    # Source overview has a cheap exact count. It is only exposed when no text
    # search is active because a source cursor page does not provide a filtered
    # total for arbitrary text matching.
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
    context, direction: str, query_type: str, search: str
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
                rows.append(fields)
        pagination = page.get("pagination") or {}
        if not pagination.get("has_more") or not pagination.get("next_cursor"):
            return rows
        cursor = str(pagination["next_cursor"])


def _available_path(destination: Path, filename: str) -> Path:
    candidate = destination / filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for copy in range(1, 1000):
        if not candidate.exists():
            return candidate
        candidate = destination / f"{stem} ({copy}){suffix}"
    raise OSError("artifact_name_exhausted")


def _excel_value(value: Any):
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def _write_overview_excel(
    rows: list[dict[str, Any]], columns: list[str], target: Path
) -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Tong quan"
    sheet.append(columns)
    for row in rows:
        sheet.append([_excel_value(row.get(column)) for column in columns])
    sheet.freeze_panes = "A2"
    if columns and rows:
        sheet.auto_filter.ref = f"A1:{sheet.cell(1, len(columns)).column_letter}{len(rows) + 1}"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        workbook.save(temporary)
        temporary.replace(target)
    finally:
        workbook.close()
        temporary.unlink(missing_ok=True)


def _filter_detail_records(records: list[dict[str, Any]], search: str):
    if not search:
        return records
    result = []
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
    """Build Excel only when requested, from persisted source-owned data."""
    from app.config.crawl_config import QUERY_TYPE_TO_CATEGORY
    from app.exporters.invoice_detail_excel_exporter import InvoiceDetailExcelExporter
    from app.repositories.invoice_detail_query_repository import InvoiceDetailQueryRepository
    from app.services.overview_downloader import OUTPUT_NAMES

    destination = Path(value["destination"])
    if not destination.is_absolute():
        raise ValueError("invalid_artifact_directory")
    scopes = value.get("result_scopes") or []
    if not isinstance(scopes, list) or not scopes or set(scopes) - {"overview", "details"}:
        raise ValueError("invalid_result_export_scope")
    connection_ids = value.get("connection_ids") or []
    if len(connection_ids) != 1:
        raise ValueError("invalid_result_export_account")

    query = {
        "connection_id": str(connection_ids[0]),
        "date_from": str(value["date_from"]),
        "date_to": str(value["date_to"]),
        "direction": value.get("direction"),
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
                scoped_context = {
                    **context,
                    "directions": [direction],
                    "query_types": [query_type],
                }
                _write_overview_excel(rows, _overview_columns(scoped_context), target)
                files.append(str(target))

    if "details" in scopes:
        repository = InvoiceDetailQueryRepository(context["database_path"])
        exporter = InvoiceDetailExcelExporter(
            Path(__file__).resolve().parent
            / "vendor" / "mia_crawl_service" / "resources" / "templates" / "invoice_detail.xlsx"
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
                    f"THỐNG KÊ CHI TIẾT HÓA ĐƠN {direction_label} - {type_label}.xlsx",
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
