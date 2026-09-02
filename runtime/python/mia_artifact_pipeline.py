from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import sqlite3
import tempfile
import threading
from contextlib import closing
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit


QUERY_TYPES = ("query", "sco-query")
DIRECTIONS = ("purchase", "sold")
KINDS = ("xml", "html", "pdf")
TERMINAL_FORMAT_STATES = {"completed", "failed", "stopped"}
REQUIRED_HTML_ASSETS = ("sign-check.jpg", "viewinvoice-bg.jpg")


def _safe_filename(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in " ._-" else "_"
        for character in str(value)
    ).strip(" .")
    return (cleaned or "artifact")[:120]


def _filename_segment(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.casefold() in {"none", "null", "undefined"}:
        return ""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", "_", text).strip(" ._")
    return text[:80]


def _safe_failure_message(value: Any, fallback: str = "Không thể tạo file") -> str:
    text = str(value or "").strip() or fallback
    friendly = {
        "xml_cache_missing": "Không tìm thấy file XML đã tải",
        "html_bundle_incomplete": "HTML thiếu tài nguyên cần thiết",
        "artifact_write_failed": "Không thể ghi file vào thư mục lưu trữ",
        "internal_error": fallback,
    }
    text = friendly.get(text, text)
    text = re.sub(
        r"(?i)(password|token|secret|authorization|cookie|session|credential|api[_-]?key)\s*[=:]\s*\S+",
        r"\1=[redacted]", text,
    )
    return text[:300]


def _invoice_date_filename_token(target: dict[str, Any]) -> str:
    raw = str(target.get("nlap_date") or target.get("nlap") or "").strip()
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if iso:
        return "".join(iso.groups())
    vietnamese = re.match(r"^(\d{2})/(\d{2})/(\d{4})", raw)
    if vietnamese:
        day, month, year = vietnamese.groups()
        return f"{year}{month}{day}"
    return ""


def build_invoice_export_basename(target: dict[str, Any]) -> str:
    """Build the one canonical XML/HTML/PDF basename for an invoice."""
    values = (
        _invoice_date_filename_token(target),
        target.get("khmshdon"), target.get("khhdon"),
        target.get("shdon"), target.get("nbmst"),
    )
    segments = [segment for value in values if (segment := _filename_segment(value))]
    return "_".join(segments)[:240] or "invoice"


def _validate_request(value: dict[str, Any], *, destination: bool) -> dict[str, Any]:
    connection_ids = value.get("connection_ids")
    directions = value.get("directions")
    kinds = value.get("kinds", [])
    try:
        date_from = date.fromisoformat(str(value.get("date_from")))
        date_to = date.fromisoformat(str(value.get("date_to")))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_artifact_range") from error
    if date_from > date_to:
        raise ValueError("invalid_artifact_range")
    if (
        not isinstance(connection_ids, list)
        or not 1 <= len(connection_ids) <= 50
        or len(set(connection_ids)) != len(connection_ids)
        or not all(isinstance(item, str) and item for item in connection_ids)
    ):
        raise ValueError("invalid_artifact_accounts")
    if (
        not isinstance(directions, list)
        or not 1 <= len(directions) <= 2
        or len(set(directions)) != len(directions)
        or set(directions) - {"purchase", "sold"}
    ):
        raise ValueError("invalid_artifact_direction")
    if destination:
        target = Path(str(value.get("destination") or ""))
        if not target.is_absolute():
            raise ValueError("invalid_artifact_directory")
        if (
            not isinstance(kinds, list)
            or not 1 <= len(kinds) <= 3
            or len(set(kinds)) != len(kinds)
            or set(kinds) - set(KINDS)
        ):
            raise ValueError("invalid_artifact_kind")
        concurrency = value.get("pdf_concurrency", 5)
        if not isinstance(concurrency, int) or isinstance(concurrency, bool) or not 1 <= concurrency <= 100:
            raise ValueError("invalid_pdf_concurrency")
    return {
        **value,
        "connection_ids": list(connection_ids),
        "directions": list(directions),
        "kinds": list(kinds),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }


def _merge_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    merged: list[list[date]] = []
    for begin, end in sorted(intervals):
        if not merged or begin > merged[-1][1] + timedelta(days=1):
            merged.append([begin, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    return [(item[0], item[1]) for item in merged]


def _missing_intervals(
    requested_from: date, requested_to: date, covered: list[tuple[date, date]],
) -> list[tuple[date, date]]:
    cursor = requested_from
    missing: list[tuple[date, date]] = []
    for begin, end in _merge_intervals(covered):
        if end < cursor or begin > requested_to:
            continue
        begin = max(begin, requested_from)
        end = min(end, requested_to)
        if begin > cursor:
            missing.append((cursor, begin - timedelta(days=1)))
        cursor = max(cursor, end + timedelta(days=1))
        if cursor > requested_to:
            break
    if cursor <= requested_to:
        missing.append((cursor, requested_to))
    return missing


def _intersect_interval_sets(
    left: list[tuple[date, date]], right: list[tuple[date, date]],
) -> list[tuple[date, date]]:
    intersections = []
    for left_from, left_to in left:
        for right_from, right_to in right:
            begin, end = max(left_from, right_from), min(left_to, right_to)
            if begin <= end:
                intersections.append((begin, end))
    return _merge_intervals(intersections)


class _HtmlReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: set[str] = set()

    def handle_starttag(self, _tag: str, attrs) -> None:
        for name, value in attrs:
            if name.casefold() in {"src", "href"} and value:
                self.references.add(str(value))


def _local_reference(root: Path, value: str) -> Path | None:
    parsed = urlsplit(value.strip())
    if parsed.scheme.casefold() in {"data", "http", "https", "mailto", "javascript"}:
        return None
    candidate = (root / unquote(parsed.path.replace("\\", "/"))).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def html_dependency_files(html_path: Path) -> tuple[Path, ...] | None:
    """Return a verified offline bundle for one HTML invoice."""
    if not html_path.is_file():
        return None
    try:
        html_bytes = html_path.read_bytes()
        parser = _HtmlReferenceParser()
        parser.feed(html_bytes.decode("utf-8", errors="replace"))
    except OSError:
        return None
    files = {html_path.resolve()}
    for name in REQUIRED_HTML_ASSETS:
        files.add((html_path.parent / name).resolve())
    pending = list(parser.references)
    seen_refs: set[str] = set()
    while pending:
        reference = pending.pop()
        if reference in seen_refs:
            continue
        seen_refs.add(reference)
        path = _local_reference(html_path.parent, reference)
        if path is None:
            continue
        if not path.is_file():
            return None
        files.add(path)
        if path.suffix.casefold() == ".css":
            try:
                css = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
            pending.extend(re.findall(r"url\(\s*['\"]?([^)'\"]+)", css, re.I))
    return tuple(sorted(files, key=str)) if all(path.is_file() for path in files) else None


def html_fingerprint(html_path: Path) -> str | None:
    dependencies = html_dependency_files(html_path)
    if dependencies is None:
        return None
    digest = hashlib.sha256()
    for path in dependencies:
        try:
            relative = path.resolve().relative_to(html_path.parent.resolve())
            digest.update(str(relative).replace("\\", "/").encode("utf-8"))
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        except (OSError, ValueError):
            return None
    return digest.hexdigest()


def _pdf_cache_paths(data_root: Path, tax_code: str, artifact_key: str) -> tuple[Path, Path]:
    key = hashlib.sha256(artifact_key.encode("utf-8")).hexdigest()
    root = data_root / tax_code / "exports" / "artifact_pdf_cache"
    return root / f"{key}.pdf", root / f"{key}.json"


def pdf_cache_valid(
    data_root: Path, tax_code: str, artifact_key: str, html_path: Path,
) -> bool:
    fingerprint = html_fingerprint(html_path)
    if fingerprint is None:
        return False
    pdf_path, metadata_path = _pdf_cache_paths(data_root, tax_code, artifact_key)
    if not pdf_path.is_file() or not metadata_path.is_file():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        with pdf_path.open("rb") as stream:
            header = stream.read(5)
            stream.seek(max(0, pdf_path.stat().st_size - 1024))
            trailer = stream.read()
    except (OSError, ValueError, TypeError):
        return False
    return (
        metadata.get("html_fingerprint") == fingerprint
        and header == b"%PDF-" and b"%%EOF" in trailer
    )


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_atomically(
    source: Path, destination: Path, export_basename: str | None = None,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    basename = _safe_filename(export_basename) if export_basename else _safe_filename(source.stem)
    target = destination / (basename + source.suffix.casefold())
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=destination
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, target)
        if os.name != "nt":
            directory_fd = os.open(destination, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def _package_row(database: Path, tax_code: str, target: dict[str, Any]) -> dict[str, Any] | None:
    if not database.is_file():
        return None
    try:
        with closing(sqlite3.connect(database, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """SELECT * FROM invoice_package_items
                   WHERE company_tax_code=? AND direction=? AND query_type=?
                     AND nbmst=? AND khhdon=? AND shdon=? AND khmshdon=?
                   LIMIT 1""",
                (
                    tax_code, target["direction"], target["query_type"],
                    target["nbmst"], target["khhdon"], target["shdon"],
                    target["khmshdon"],
                ),
            ).fetchone()
    except sqlite3.Error:
        return None
    return dict(row) if row is not None else None


def _package_map(database: Path, tax_code: str) -> dict[str, dict[str, Any]]:
    if not database.is_file():
        return {}
    try:
        with closing(sqlite3.connect(database, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_package_items'"
            ).fetchone()
            if exists is None:
                return {}
            rows = connection.execute(
                """SELECT * FROM invoice_package_items WHERE company_tax_code=?""",
                (tax_code,),
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {
        "|".join(str(row[name]) for name in (
            "direction", "query_type", "nbmst", "khhdon", "shdon", "khmshdon"
        )): dict(row)
        for row in rows
    }


def invalidate_incomplete_html_bundle(
    data_root: Path, tax_code: str, target: dict[str, Any],
) -> bool:
    """Requeue package metadata when HTML exists but its offline bundle does not."""
    database = Path(data_root) / tax_code / "db" / "invoices.sqlite3"
    package = _package_row(database, tax_code, target)
    if not package or int(package.get("unavailable") or 0):
        return False
    if not int(package.get("html_fetched") or 0):
        return False
    html_path = Path(str(package.get("html_path") or ""))
    if html_dependency_files(html_path) is not None:
        return False
    try:
        with closing(sqlite3.connect(database, timeout=5)) as connection:
            with connection:
                connection.execute(
                    """UPDATE invoice_package_items
                       SET html_fetched=0, html_path=NULL,
                           error_message='HTML bundle incomplete; queued for package repair',
                           updated_at=datetime('now')
                       WHERE id=?""",
                    (package["id"],),
                )
    except sqlite3.Error:
        return False
    return True


class ArtifactInspector:
    def __init__(self, backend: Any) -> None:
        self.backend = backend
        self.data_root = Path(backend.data_root)

    def snapshot(self, raw: dict[str, Any]) -> dict[str, Any]:
        value = _validate_request(dict(raw), destination=False)
        return {
            "accounts": [self._account_snapshot(connection_id, value) for connection_id in value["connection_ids"]],
            "date_from": value["date_from"],
            "date_to": value["date_to"],
            "directions": value["directions"],
        }

    def coverage(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Return only persisted Overview readiness without scanning artifact files.

        Coverage is latency-sensitive UI state. XML/HTML/PDF cache validation can
        require filesystem and PDF fingerprint checks for every invoice, so it
        intentionally stays in ``snapshot`` and cannot delay this response.
        """
        value = _validate_request(dict(raw), destination=False)
        accounts = []
        for connection_id in value["connection_ids"]:
            tax_code = self.backend.connection_tax_code(connection_id)
            database = self.data_root / tax_code / "db" / "invoices.sqlite3"
            missing = self._coverage_missing(database, tax_code, value)
            accounts.append({
                "connection_id": connection_id,
                "ready": not missing,
                "missing_ranges": [
                    {"date_from": begin.isoformat(), "date_to": end.isoformat()}
                    for begin, end in missing
                ],
            })
        return {
            "accounts": accounts,
            "date_from": value["date_from"],
            "date_to": value["date_to"],
            "directions": value["directions"],
        }

    def vat_return_coverage(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Coverage contract for the future single VAT-return workbook.

        Unlike artifact coverage, this contract is direction- and scope-aware.
        It only trusts finalized Overview checkpoints and successfully completed
        persisted Detail months. Row counts are deliberately irrelevant so a
        finalized empty period remains covered.
        """
        value = _validate_request({**dict(raw), "directions": list(DIRECTIONS)}, destination=False)
        accounts = []
        for connection_id in value["connection_ids"]:
            tax_code = self.backend.connection_tax_code(connection_id)
            database = self.data_root / tax_code / "db" / "invoices.sqlite3"
            directions = {}
            for direction in DIRECTIONS:
                requested_from = date.fromisoformat(value["date_from"])
                requested_to = date.fromisoformat(value["date_to"])
                missing_overview = _missing_intervals(
                    requested_from, requested_to,
                    self._vat_overview_intervals(database, tax_code, direction),
                )
                missing_detail = _missing_intervals(
                    requested_from, requested_to,
                    self._vat_detail_intervals(database, tax_code, direction),
                )
                overview_ranges = [{"date_from": begin.isoformat(), "date_to": end.isoformat()} for begin, end in missing_overview]
                detail_ranges = [{"date_from": begin.isoformat(), "date_to": end.isoformat()} for begin, end in missing_detail]
                missing = [
                    *({"scope": "overview", **item} for item in overview_ranges),
                    *({"scope": "details", **item} for item in detail_ranges),
                ]
                directions[direction] = {
                    "direction": direction,
                    "overview_ready": not overview_ranges,
                    "detail_ready": not detail_ranges,
                    "ready": not missing,
                    "missing_overview_ranges": overview_ranges,
                    "missing_detail_ranges": detail_ranges,
                    "missing": missing,
                }
            accounts.append({"connection_id": connection_id, **directions})
        return {
            "accounts": accounts, "date_from": value["date_from"],
            "date_to": value["date_to"],
        }

    @staticmethod
    def _vat_overview_intervals(database: Path, tax_code: str, direction: str) -> list[tuple[date, date]]:
        if not database.is_file():
            return []
        try:
            with closing(sqlite3.connect(database, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_overview_checkpoints'"
                ).fetchone() is None:
                    return []
                rows = [dict(row) for row in connection.execute(
                    """SELECT query_type,status_filter,from_date,to_date,checkpoint_status,
                              fetched_count,expected_total,page_number
                       FROM invoice_overview_checkpoints
                       WHERE company_tax_code=? AND direction=?""",
                    (tax_code, direction),
                ).fetchall()]
        except sqlite3.Error:
            return []
        # These are the source crawler's persisted query scopes: three normal
        # e-invoice status partitions and one cash-register partition.
        required_sources = (("query", "5"), ("query", "6"), ("query", "8"), ("sco-query", "all"))
        source_intervals = []
        for query_type, status_filter in required_sources:
            source_intervals.append(_merge_intervals([
                (date.fromisoformat(row["from_date"]), date.fromisoformat(row["to_date"]))
                for row in rows
                if row["query_type"] == query_type
                and str(row.get("status_filter") or "") == status_filter
                and row["checkpoint_status"] == "finalized"
                and (row.get("expected_total") is None or int(row.get("fetched_count") or 0) == int(row["expected_total"]))
                and (int(row.get("fetched_count") or 0) == 0 or int(row.get("page_number") or 0) >= 1)
            ]))
        if not all(source_intervals):
            return []
        covered = source_intervals[0]
        for intervals in source_intervals[1:]:
            covered = _intersect_interval_sets(covered, intervals)
        return covered

    @staticmethod
    def _vat_detail_intervals(database: Path, tax_code: str, direction: str) -> list[tuple[date, date]]:
        if not database.is_file():
            return []
        try:
            with closing(sqlite3.connect(database, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_detail_checkpoints'"
                ).fetchone() is None:
                    return []
                rows = [dict(row) for row in connection.execute(
                    """SELECT query_type,from_date,to_date,checkpoint_status,
                              overview_expected,detail_succeeded,detail_failed
                       FROM invoice_detail_checkpoints
                       WHERE company_tax_code=? AND direction=?""",
                    (tax_code, direction),
                ).fetchall()]
                from app.utils.invoice_identity import (canonical_invoice_identity,
                                                        invoice_status_is_excluded)

                has_attributes = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_overview_attributes'"
                ).fetchone() is not None

                def eligible_overview(items):
                    for item in items:
                        if not has_attributes:
                            yield item
                            continue
                        status_row = connection.execute(
                            "SELECT value_json FROM invoice_overview_attributes WHERE invoice_item_id=? AND field_name='tthai'",
                            (item['id'],),
                        ).fetchone()
                        raw_status = status_row[0] if status_row else None
                        if raw_status is not None:
                            try:
                                raw_status = json.loads(raw_status)
                            except (TypeError, json.JSONDecodeError):
                                pass
                        if not invoice_status_is_excluded(raw_status):
                            yield item

                def invoice_complete(row):
                    overview = connection.execute(
                        """SELECT * FROM invoice_overview_items
                           WHERE company_tax_code=? AND direction=? AND query_type=?
                             AND nlap_date BETWEEN ? AND ?""",
                        (tax_code, direction, row['query_type'], row['from_date'], row['to_date']),
                    ).fetchall()
                    expected = {
                        canonical_invoice_identity(tax_code, direction, dict(item))
                        for item in eligible_overview(overview)
                    }
                    details = connection.execute(
                        """SELECT * FROM invoice_detail_items
                           WHERE company_tax_code=? AND direction=?
                             AND nlap_date BETWEEN ? AND ? AND normalized_ready=1
                             AND detail_outcome IN ('with_lines','valid_empty')
                             AND (error_message IS NULL OR TRIM(error_message)='')""",
                        (tax_code, direction, row['from_date'], row['to_date']),
                    ).fetchall()
                    completed = {
                        canonical_invoice_identity(tax_code, direction, dict(item))
                        for item in details
                    }
                    return expected <= completed
                for row in rows:
                    row['_invoice_complete'] = invoice_complete(row)
        except sqlite3.Error:
            return []
        per_query = []
        for query_type in QUERY_TYPES:
            per_query.append(_merge_intervals([
                (date.fromisoformat(row["from_date"]), date.fromisoformat(row["to_date"]))
                for row in rows
                if row["query_type"] == query_type
                and row["checkpoint_status"] == "finalized"
                and int(row.get("overview_expected") or 0) == int(row.get("detail_succeeded") or 0)
                and int(row.get("detail_failed") or 0) == 0
                and row.get('_invoice_complete', False)
            ]))
        if not all(per_query):
            return []
        return _intersect_interval_sets(per_query[0], per_query[1])

    def _account_snapshot(self, connection_id: str, value: dict[str, Any]) -> dict[str, Any]:
        tax_code = self.backend.connection_tax_code(connection_id)
        database = self.data_root / tax_code / "db" / "invoices.sqlite3"
        missing = self._coverage_missing(database, tax_code, value)
        total = xml = html = pdf = 0
        if database.is_file():
            try:
                with closing(sqlite3.connect(database, timeout=5)) as connection:
                    connection.row_factory = sqlite3.Row
                    placeholders = ",".join("?" for _ in value["directions"])
                    targets = connection.execute(
                        f"""SELECT direction,query_type,nbmst,khhdon,shdon,khmshdon
                            FROM invoice_overview_items
                            WHERE company_tax_code=? AND direction IN ({placeholders})
                              AND query_type IN ('query','sco-query')
                              AND nlap_date BETWEEN ? AND ?""",
                        [tax_code, *value["directions"], value["date_from"], value["date_to"]],
                    ).fetchall()
                    package_rows = connection.execute(
                        f"""SELECT direction,query_type,nbmst,khhdon,shdon,khmshdon,
                                   xml_path,html_path,xml_fetched,html_fetched,unavailable
                            FROM invoice_package_items
                            WHERE company_tax_code=? AND direction IN ({placeholders})
                              AND query_type IN ('query','sco-query')
                              AND nlap_date BETWEEN ? AND ?""",
                        [tax_code, *value["directions"], value["date_from"], value["date_to"]],
                    ).fetchall() if connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_package_items'"
                    ).fetchone() else []
                    packages = {
                        "|".join(str(row[name]) for name in (
                            "direction", "query_type", "nbmst", "khhdon", "shdon", "khmshdon"
                        )): dict(row)
                        for row in package_rows
                    }
                    total = len(targets)
                    for target_row in targets:
                        target = dict(target_row)
                        target["artifact_key"] = "|".join(str(target[name]) for name in (
                            "direction", "query_type", "nbmst", "khhdon", "shdon", "khmshdon"
                        ))
                        package = packages.get(target["artifact_key"])
                        if not package or int(package.get("unavailable") or 0):
                            continue
                        xml_path = Path(str(package.get("xml_path") or ""))
                        html_path = Path(str(package.get("html_path") or ""))
                        if int(package.get("xml_fetched") or 0) == 1 and xml_path.is_file():
                            xml += 1
                        html_ready = int(package.get("html_fetched") or 0) == 1 and html_dependency_files(html_path) is not None
                        if html_ready:
                            html += 1
                            if pdf_cache_valid(self.data_root, tax_code, target["artifact_key"], html_path):
                                pdf += 1
            except sqlite3.Error:
                total = xml = html = pdf = 0
        return {
            "connection_id": connection_id,
            "ready": not missing,
            "missing_ranges": [
                {"date_from": begin.isoformat(), "date_to": end.isoformat()}
                for begin, end in missing
            ],
            "total": total,
            "cached": {"xml": xml, "html": html, "pdf": pdf},
        }

    def _coverage_missing(
        self, database: Path, tax_code: str, value: dict[str, Any],
    ) -> list[tuple[date, date]]:
        requested_from = date.fromisoformat(value["date_from"])
        requested_to = date.fromisoformat(value["date_to"])
        if not database.is_file():
            return [(requested_from, requested_to)]
        try:
            with closing(sqlite3.connect(database, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='invoice_overview_checkpoints'"
                ).fetchone()
                if exists is None:
                    return [(requested_from, requested_to)]
                rows = [dict(row) for row in connection.execute(
                    """SELECT direction,query_type,status_filter,from_date,to_date,
                              checkpoint_status,fetched_count,expected_total,page_number
                       FROM invoice_overview_checkpoints
                       WHERE company_tax_code=? AND to_date>=? AND from_date<=?""",
                    (tax_code, value["date_from"], value["date_to"]),
                ).fetchall()]
        except (sqlite3.Error, ImportError):
            return [(requested_from, requested_to)]
        missing: list[tuple[date, date]] = []
        for direction in value["directions"]:
            # A persisted finalized Overview interval is the same coverage
            # source-of-truth used by invoice management. Missing scopes must
            # not subtract dates already finalized by another source scope;
            # doing so caused one absent status/query row to poison the entire
            # requested range after the valid intervals were merged.
            intervals = []
            for row in rows:
                fetched = int(row.get("fetched_count") or 0)
                expected = row.get("expected_total")
                valid = (
                    row["direction"] == direction
                    and row["query_type"] in QUERY_TYPES
                    and row["checkpoint_status"] == "finalized"
                    and (expected is None or fetched == int(expected))
                    and (fetched == 0 or int(row.get("page_number") or 0) >= 1)
                )
                if valid:
                    intervals.append((
                        date.fromisoformat(row["from_date"]),
                        date.fromisoformat(row["to_date"]),
                    ))
            missing.extend(_missing_intervals(requested_from, requested_to, intervals))
        return _merge_intervals(missing)


def _create_pdf_renderer(assets_dir: Path):
    """Desktop adapter for package assets and Windows-safe atomic PDF writes."""
    from app.exporters.invoice_pdf_renderer import InvoicePdfRenderer

    class DesktopInvoicePdfRenderer(InvoicePdfRenderer):
        def _render_pdf_atomically(self, pdf_path: Path, **options: Any) -> None:
            pdf_path = Path(pdf_path)
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{pdf_path.name}.", suffix=".tmp", dir=pdf_path.parent
            )
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            try:
                self._page.pdf(path=str(temporary_path), **options)
                # Windows rejects fsync on a read-only CRT descriptor. r+b
                # preserves validation while making the durability flush valid.
                with temporary_path.open("r+b") as stream:
                    header = stream.read(5)
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    stream.seek(max(0, size - 1024))
                    trailer = stream.read()
                    if header != b"%PDF-" or b"%%EOF" not in trailer:
                        raise RuntimeError("Rendered PDF failed structural validation")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_path, pdf_path)
                # Directory fsync is a POSIX durability primitive and is not
                # supported by Windows directory handles.
                if os.name != "nt":
                    directory_fd = os.open(pdf_path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            finally:
                temporary_path.unlink(missing_ok=True)

    return DesktopInvoicePdfRenderer(assets_dir=assets_dir)


class _PdfWorkerPool:
    def __init__(
        self, concurrency: int,
        render: Callable[[dict[str, Any], Callable[[], Any]], None],
    ) -> None:
        # Backpressure keeps a 100k-invoice export from materializing 100k PDF
        # work items while Chromium is still rendering the first pages.
        self.queue: queue.Queue[dict[str, Any] | None] = queue.Queue(
            maxsize=max(2, concurrency * 2)
        )
        self.render = render
        self.threads = [
            threading.Thread(target=self._worker, name=f"mia-pdf-worker-{index + 1}", daemon=True)
            for index in range(concurrency)
        ]
        for thread in self.threads:
            thread.start()

    def _worker(self) -> None:
        renderer = None
        renderer_assets = None

        def get_renderer(assets_dir: Path):
            nonlocal renderer, renderer_assets
            assets_dir = Path(assets_dir).resolve()
            if renderer is None:
                # The two page assets belong to the real package. The vendored
                # global directory is intentionally not assumed to be populated.
                renderer = _create_pdf_renderer(assets_dir)
                renderer.__enter__()
                renderer_assets = assets_dir
            elif renderer_assets != assets_dir:
                # One Chromium instance is retained per worker while the
                # controlled page CSS follows each invoice package directory.
                renderer.assets_dir = assets_dir
                renderer.page_asset_css = renderer._build_page_asset_css()
                renderer_assets = assets_dir
            return renderer

        try:
            while True:
                item = self.queue.get()
                try:
                    if item is None:
                        return
                    self.render(item, get_renderer)
                finally:
                    self.queue.task_done()
        finally:
            if renderer is not None:
                renderer.__exit__(None, None, None)

    def submit(self, item: dict[str, Any]) -> None:
        self.queue.put(item)

    def finish(self) -> None:
        self.queue.join()
        for _thread in self.threads:
            self.queue.put(None)
        for thread in self.threads:
            thread.join()


class ArtifactBatchCoordinator:
    """Shared package producer with independent XML/HTML/PDF consumers."""

    def __init__(
        self, backend: Any, raw: dict[str, Any], *,
        state_callback: Callable[[dict[str, Any]], None] | None = None,
        logger: Any | None = None,
    ) -> None:
        self.backend = backend
        self.data_root = Path(backend.data_root)
        self.value = _validate_request(dict(raw), destination=True)
        self.callback = state_callback
        self.logger = logger
        self.lock = threading.RLock()
        self.global_cancel = threading.Event()
        self.format_cancel = {kind: threading.Event() for kind in KINDS}
        self.failures: dict[str, dict[str, dict[str, Any]]] = {
            connection_id: {} for connection_id in self.value["connection_ids"]
        }
        self.state: dict[str, Any] = {
            "status": "running", "current_account_id": None,
            "accounts": {}, "formats": {}, "warning_count": 0,
        }

    def cancel(self, kind: str | None = None) -> None:
        if kind is None:
            self.global_cancel.set()
            for event in self.format_cancel.values():
                event.set()
        elif kind in self.format_cancel:
            self.format_cancel[kind].set()
        else:
            raise ValueError("invalid_artifact_kind")
        if self.logger is not None:
            if kind is None:
                self.logger.info("batch_cancelled")
            else:
                self.logger.info("format_cancelled kind=%s", kind)
        self._emit()

    def view(self) -> dict[str, Any]:
        with self.lock:
            # Status polling must remain a RAM-only bounded snapshot. Structured
            # invoice failures are exposed by a separate on-demand RPC.
            return {
                "status": self.state["status"],
                "current_account_id": self.state["current_account_id"],
                "warning_count": self.state["warning_count"],
                "accounts": {
                    key: {
                        **value,
                        "cached": dict(value.get("cached") or {}),
                        "missing_ranges": [dict(item) for item in value.get("missing_ranges") or ()],
                    }
                    for key, value in self.state["accounts"].items()
                },
                "formats": {
                    key: dict(value) for key, value in self.state["formats"].items()
                },
            }

    def failure_view(
        self, connection_id: str, offset: int = 0, limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        if connection_id not in self.failures:
            raise ValueError("invalid_artifact_account")
        with self.lock:
            values = list(self.failures[connection_id].values())
            return [dict(item) for item in values[offset:offset + limit]], len(values)

    def _emit(self) -> None:
        if self.callback:
            self.callback(self.view())

    def _selected_active(self) -> bool:
        return any(
            kind in self.value["kinds"] and not self.format_cancel[kind].is_set()
            for kind in KINDS
        )

    def run(self) -> dict[str, Any]:
        cancelled = False
        inspector = ArtifactInspector(self.backend)
        snapshots = {
            item["connection_id"]: item
            for item in inspector.snapshot(self.value)["accounts"]
        }
        with self.lock:
            self.state["accounts"] = {
                connection_id: {
                    "status": "ready" if snapshots[connection_id]["ready"] else "not_ready",
                    "total": snapshots[connection_id]["total"],
                    "cached": snapshots[connection_id]["cached"],
                    "missing_ranges": snapshots[connection_id]["missing_ranges"],
                    "failure_count": 0,
                }
                for connection_id in self.value["connection_ids"]
            }
        self._emit()
        for connection_id in self.value["connection_ids"]:
            if self.global_cancel.is_set() or not self._selected_active():
                break
            if not snapshots[connection_id]["ready"]:
                continue
            try:
                self._run_account(connection_id)
            except ValueError as error:
                if str(error) == "artifact_cancelled":
                    cancelled = True
                    with self.lock:
                        self.state["accounts"][connection_id]["status"] = "stopped"
                    if self.logger is not None:
                        self.logger.info("batch_cancelled connection_ref=%s", connection_id[-8:])
                    self._emit()
                    break
                with self.lock:
                    self.state["accounts"][connection_id]["status"] = "error"
                    self.state["accounts"][connection_id]["error"] = type(error).__name__
                    self.state["warning_count"] += 1
                if self.logger is not None:
                    self.logger.exception("account_failed connection_ref=%s", connection_id[-8:])
                self._emit()
            except Exception as error:
                with self.lock:
                    self.state["accounts"][connection_id]["status"] = "error"
                    self.state["accounts"][connection_id]["error"] = type(error).__name__
                    self.state["warning_count"] += 1
                if self.logger is not None:
                    self.logger.exception("account_failed connection_ref=%s", connection_id[-8:])
                self._emit()
        with self.lock:
            stopped = cancelled or self.global_cancel.is_set() or not self._selected_active()
            self.state["status"] = "stopped" if stopped else "completed"
            self.state["current_account_id"] = None
        self._emit()
        return self.view()

    def _targets(self, connection_id: str) -> list[dict[str, Any]]:
        targets: dict[str, dict[str, Any]] = {}
        for direction in self.value["directions"]:
            for query_type in QUERY_TYPES:
                request = {
                    "connection_ids": [connection_id],
                    "direction": direction, "query_type": query_type,
                    "date_from": self.value["date_from"], "date_to": self.value["date_to"],
                    "search": "",
                }
                for target in self.backend.artifact_targets_for_export(request):
                    targets[target["artifact_key"]] = target
        return list(targets.values())

    def _run_account(self, connection_id: str) -> None:
        tax_code = self.backend.connection_tax_code(connection_id)
        database = self.data_root / tax_code / "db" / "invoices.sqlite3"
        targets = self._targets(connection_id)
        total = len(targets)
        with self.lock:
            self.state["current_account_id"] = connection_id
            self.state["accounts"][connection_id].update(status="downloading", total=total)
            self.state["formats"] = {
                kind: {
                    "status": "preparing" if kind == "pdf" else "running",
                    "processed": 0, "total": total, "percent": 0,
                    "current_invoice": None, "failed": 0, "skipped": 0,
                }
                for kind in self.value["kinds"]
            }
        self._emit()
        target_map = {target["artifact_key"]: target for target in targets}
        package_before = _package_map(database, tax_code)
        cache_keys: dict[str, set[str]] = {kind: set() for kind in KINDS}
        for key in target_map:
            package = package_before.get(key)
            if not package or int(package.get("unavailable") or 0):
                continue
            xml_path = Path(str(package.get("xml_path") or ""))
            html_path = Path(str(package.get("html_path") or ""))
            if int(package.get("xml_fetched") or 0) == 1 and xml_path.is_file():
                cache_keys["xml"].add(key)
            if int(package.get("html_fetched") or 0) == 1 and html_dependency_files(html_path) is not None:
                cache_keys["html"].add(key)
                if pdf_cache_valid(self.data_root, tax_code, key, html_path):
                    cache_keys["pdf"].add(key)
        output_roots = {
            kind: Path(self.value["destination"]) / _safe_filename(tax_code)
            / f"{kind.upper()} {self.value['date_from']}_{self.value['date_to']}"
            for kind in self.value["kinds"]
        }
        pdf_pool = None
        if "pdf" in self.value["kinds"]:
            pdf_pool = _PdfWorkerPool(
                min(int(self.value["pdf_concurrency"]), max(1, total)),
                lambda item, get_renderer: self._render_pdf_item(
                    item, tax_code, output_roots["pdf"], get_renderer
                ),
            )
        exported_asset_roots: set[Path] = set()

        def ready(target: dict[str, Any], state: str, outcome: str) -> None:
            key = target["artifact_key"]
            export_basename = build_invoice_export_basename(target)
            display = " - ".join(str(target.get(name) or "") for name in ("khhdon", "shdon", "nbmst"))
            if state == "missing_original" or outcome == "missing_original":
                self._record_failure(
                    connection_id, target, self.value["kinds"],
                    "missing_original", "Không tồn tại hồ sơ gốc",
                )
                for kind in self.value["kinds"]:
                    self._skip(kind, display)
                return
            if state == "unavailable" or outcome in {
                "unavailable", "source_confirmed_unavailable", "source_retry_exhausted",
            }:
                with self.lock:
                    self.state["warning_count"] += 1
                message = (
                    "Không lấy được gói dữ liệu sau 7 lần thử"
                    if outcome == "source_retry_exhausted"
                    else "Không thể lấy gói dữ liệu từ nguồn"
                )
                self._record_failure(
                    connection_id, target, self.value["kinds"], outcome, message,
                )
                for kind in self.value["kinds"]:
                    self._advance(kind, display, failed=True)
                return
            if state != "completed":
                with self.lock:
                    self.state["warning_count"] += 1
                if self.logger is not None:
                    self.logger.error(
                        "artifact_package_failed format=XML/HTML account_ref=%s invoice_ref=%s outcome=%s",
                        connection_id[-8:], _safe_filename(display)[:80], outcome,
                    )
                self._record_failure(
                    connection_id, target, self.value["kinds"],
                    "package_failed", "Không thể tải gói dữ liệu hóa đơn",
                )
                for kind in self.value["kinds"]:
                    self._advance(kind, display, failed=True)
                return
            package = _package_row(database, tax_code, target)
            if package is None:
                self._record_failure(
                    connection_id, target, self.value["kinds"],
                    "package_cache_missing", "Không tìm thấy gói dữ liệu đã tải",
                )
                for kind in self.value["kinds"]:
                    self._advance(kind, display, failed=True)
                return
            xml_path = Path(str(package.get("xml_path") or ""))
            html_path = Path(str(package.get("html_path") or ""))
            html_ready = html_dependency_files(html_path) is not None
            self._mark_cached(connection_id, "xml", key, xml_path.is_file(), cache_keys)
            self._mark_cached(connection_id, "html", key, html_ready, cache_keys)
            if "xml" in self.value["kinds"] and not self.format_cancel["xml"].is_set():
                try:
                    if not xml_path.is_file():
                        raise FileNotFoundError("xml_cache_missing")
                    _copy_atomically(xml_path, output_roots["xml"], export_basename)
                    self._advance("xml", display)
                except OSError as error:
                    if self.logger is not None:
                        self.logger.warning(
                            "artifact_copy_failed format=XML account_ref=%s invoice_ref=%s error_type=%s message=%s",
                            connection_id[-8:], _safe_filename(display)[:80],
                            type(error).__name__, str(error)[:200],
                        )
                    self._record_failure(
                        connection_id, target, ["xml"], "xml_export_failed",
                        str(error)[:240] or type(error).__name__,
                    )
                    self._advance("xml", display, failed=True)
            if "html" in self.value["kinds"] and not self.format_cancel["html"].is_set():
                try:
                    if not html_ready:
                        raise FileNotFoundError("html_bundle_incomplete")
                    _copy_atomically(html_path, output_roots["html"], export_basename)
                    source_root = html_path.parent.resolve()
                    if source_root not in exported_asset_roots:
                        self._copy_html_assets(source_root, output_roots["html"])
                        exported_asset_roots.add(source_root)
                    self._advance("html", display)
                except OSError as error:
                    if self.logger is not None:
                        self.logger.warning(
                            "artifact_copy_failed format=HTML account_ref=%s invoice_ref=%s error_type=%s message=%s",
                            connection_id[-8:], _safe_filename(display)[:80],
                            type(error).__name__, str(error)[:200],
                        )
                    self._record_failure(
                        connection_id, target, ["html"], "html_export_failed",
                        str(error)[:240] or type(error).__name__,
                    )
                    self._advance("html", display, failed=True)
            if "pdf" in self.value["kinds"] and not self.format_cancel["pdf"].is_set():
                if html_ready and pdf_pool is not None:
                    with self.lock:
                        self.state["formats"]["pdf"]["status"] = "running"
                    pdf_pool.submit({"target": target, "html_path": html_path, "display": display, "connection_id": connection_id, "cache_keys": cache_keys})
                    self._emit()
                else:
                    if self.logger is not None:
                        self.logger.warning(
                            "artifact_dependency_missing format=PDF account_ref=%s invoice_ref=%s error_type=FileNotFoundError message=html_bundle_incomplete",
                            connection_id[-8:], _safe_filename(display)[:80],
                        )
                    self._record_failure(
                        connection_id, target, ["pdf"], "pdf_dependency_missing",
                        "HTML thiếu tài nguyên cần thiết",
                    )
                    self._advance("pdf", display, failed=True)

        target_keys = set(target_map)
        for direction in self.value["directions"]:
            for query_type in QUERY_TYPES:
                if self.global_cancel.is_set() or not self._selected_active():
                    break
                batch_keys = {
                    key for key, target in target_map.items()
                    if target["direction"] == direction and target["query_type"] == query_type
                }
                if not batch_keys:
                    continue
                request = {
                    "connection_ids": [connection_id], "kinds": ["xml", "html"],
                    "direction": direction, "query_type": query_type,
                    "date_from": self.value["date_from"], "date_to": self.value["date_to"],
                    "search": "", "_artifact_keys": batch_keys,
                }
                self.backend.ensure_invoice_packages(
                    request,
                    cancel_callback=lambda: self.global_cancel.is_set() or not self._selected_active(),
                    ready_callback=ready,
                )
        if pdf_pool is not None:
            pdf_pool.finish()
        with self.lock:
            for kind, item in self.state["formats"].items():
                if self.format_cancel[kind].is_set():
                    item["status"] = "stopped"
                elif item["status"] not in TERMINAL_FORMAT_STATES:
                    item["status"] = "completed"
                    item["percent"] = 100 if item["total"] == 0 or item["processed"] >= item["total"] else item["percent"]
            self.state["accounts"][connection_id]["status"] = (
                "stopped"
                if self.global_cancel.is_set() or not self._selected_active()
                else "completed"
            )
        if self.logger is not None:
            event = "account_completed" if self.state["accounts"][connection_id]["status"] == "completed" else "batch_cancelled"
            self.logger.info("%s connection_ref=%s", event, connection_id[-8:])
        self._emit()

    def _advance(self, kind: str, display: str, *, failed: bool = False) -> None:
        if self.format_cancel[kind].is_set():
            return
        with self.lock:
            item = self.state["formats"][kind]
            item["processed"] += int(not failed)
            item["failed"] += int(failed)
            item["current_invoice"] = display[:300]
            completed = item["processed"] + item["failed"] + item.get("skipped", 0)
            item["percent"] = completed / item["total"] * 100 if item["total"] else 100
        self._emit()

    def _skip(self, kind: str, display: str) -> None:
        if self.format_cancel[kind].is_set():
            return
        with self.lock:
            item = self.state["formats"][kind]
            item["skipped"] = int(item.get("skipped") or 0) + 1
            item["current_invoice"] = display[:300]
            completed = item["processed"] + item["failed"] + item["skipped"]
            item["percent"] = completed / item["total"] * 100 if item["total"] else 100
        self._emit()

    def _record_failure(
        self, connection_id: str, target: dict[str, Any],
        formats: list[str] | tuple[str, ...], category: str, message: str,
    ) -> None:
        key = str(target.get("artifact_key") or "")
        if not key:
            return
        with self.lock:
            account_failures = self.failures[connection_id]
            existing = account_failures.get(key)
            affected = sorted(set(formats) | set(existing.get("affected_formats") or () if existing else ()))
            messages = list(existing.get("messages") or () if existing else ())
            clean_message = _safe_failure_message(message, category)
            if clean_message and clean_message not in messages:
                messages.append(clean_message)
            account_failures[key] = {
                "account_id": connection_id,
                "invoice_key": key,
                "date": str(target.get("nlap_date") or target.get("nlap") or ""),
                "direction": str(target.get("direction") or ""),
                "khmshdon": str(target.get("khmshdon") or ""),
                "khhdon": str(target.get("khhdon") or ""),
                "shdon": str(target.get("shdon") or ""),
                "nbmst": str(target.get("nbmst") or ""),
                "partner_name": str(target.get("partner_name") or ""),
                "affected_formats": affected,
                "category": category if existing is None else str(existing.get("category") or category),
                "message": "; ".join(messages),
                "messages": messages,
            }
            self.state["accounts"][connection_id]["failure_count"] = len(account_failures)
        self._emit()

    def _mark_cached(
        self, connection_id: str, kind: str, key: str, ready: bool,
        cache_keys: dict[str, set[str]],
    ) -> None:
        if not ready or key in cache_keys[kind]:
            return
        cache_keys[kind].add(key)
        with self.lock:
            account = self.state["accounts"][connection_id]
            account["cached"][kind] = len(cache_keys[kind])
        self._emit()

    def _render_pdf_item(
        self, item: dict[str, Any], tax_code: str, output_root: Path,
        get_renderer: Callable[[Path], Any],
    ) -> None:
        display = item["display"]
        if self.global_cancel.is_set() or self.format_cancel["pdf"].is_set():
            return
        target = item["target"]
        html_path = Path(item["html_path"])
        pdf_path, metadata_path = _pdf_cache_paths(self.data_root, tax_code, target["artifact_key"])
        try:
            fingerprint = html_fingerprint(html_path)
            if fingerprint is None:
                raise FileNotFoundError("html_bundle_incomplete")
            if not pdf_cache_valid(self.data_root, tax_code, target["artifact_key"], html_path):
                if self.logger is not None:
                    self.logger.info("pdf_started invoice_ref=%s", _safe_filename(display)[:80])
                get_renderer(html_path.parent).render_pdf(html_path, pdf_path)
                _atomic_json(metadata_path, {
                    "artifact_key": target["artifact_key"],
                    "html_fingerprint": fingerprint,
                })
            if self.global_cancel.is_set() or self.format_cancel["pdf"].is_set():
                return
            _copy_atomically(
                pdf_path, output_root, build_invoice_export_basename(target)
            )
            self._mark_cached(
                item["connection_id"], "pdf", target["artifact_key"], True,
                item["cache_keys"],
            )
            self._advance("pdf", display)
            if self.logger is not None:
                self.logger.info("pdf_completed invoice_ref=%s", _safe_filename(display)[:80])
        except Exception as error:
            if self.logger is not None:
                self.logger.warning(
                    "pdf_failed format=PDF account_ref=%s invoice_ref=%s error_type=%s message=%s",
                    str(item.get("connection_id") or "")[-8:], _safe_filename(display)[:80],
                    type(error).__name__, str(error)[:200],
                )
            self._record_failure(
                str(item.get("connection_id") or ""), target, ["pdf"],
                "pdf_failed", str(error)[:240] or type(error).__name__,
            )
            self._advance("pdf", display, failed=True)

    @staticmethod
    def _copy_html_assets(source_root: Path, output_root: Path) -> None:
        for source in sorted(source_root.rglob("*")):
            if not source.is_file() or source.suffix.casefold() in {
                ".html", ".htm", ".xml", ".zip", ".pdf"
            }:
                continue
            relative = source.resolve().relative_to(source_root)
            target = (output_root / relative).resolve()
            target.relative_to(output_root.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{threading.get_ident()}.tmp")
            try:
                with source.open("rb") as reader, temporary.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)


__all__ = [
    "ArtifactBatchCoordinator", "ArtifactInspector", "html_dependency_files",
    "html_fingerprint", "pdf_cache_valid", "_missing_intervals",
    "invalidate_incomplete_html_bundle",
]
