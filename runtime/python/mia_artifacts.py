from __future__ import annotations

import json
import os
import shutil
import tempfile
import base64
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from typing import Any

from openpyxl import Workbook

ALLOWED_KINDS = {"xml", "html", "pdf", "excel"}


def safe_excel_value(value: Any) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def safe_filename(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in " ._-" else "_" for character in value).strip(" .")
    return (cleaned or "artifact")[:120]


class ArtifactExporter:
    def __init__(self, storage: Any, data_directory: Path) -> None:
        self.storage = storage
        self.data_directory = data_directory.resolve()

    def _export_roots(self, username: str) -> tuple[Path, ...]:
        # Keep artifacts produced by pre-production desktop builds visible while
        # all new jobs write through the production source pipeline.
        return tuple(
            (self.data_directory / directory / username / "exports").resolve()
            for directory in ("source-data", "crawler-data")
        )

    def export(self, value: dict[str, Any]) -> dict[str, Any]:
        destination = Path(value["destination"])
        if not destination.is_absolute():
            raise ValueError("invalid_artifact_directory")
        kinds = value.get("kinds")
        account_ids = value.get("connection_ids")
        if not isinstance(kinds, list) or not kinds or set(kinds) - ALLOWED_KINDS:
            raise ValueError("invalid_artifact_kind")
        if not isinstance(account_ids, list) or not 1 <= len(account_ids) <= 50 or len(set(account_ids)) != len(account_ids):
            raise ValueError("invalid_artifact_accounts")
        destination.mkdir(parents=True, exist_ok=True)
        outputs: list[str] = []
        for account_id in account_ids:
            account = self.storage.get_account(account_id)
            if "excel" in kinds:
                production_excel = self._copy_job_artifacts(
                    destination, account_id, "excel",
                )
                if production_excel:
                    outputs.extend(str(item) for item in production_excel)
                else:
                    # Compatibility for jobs created before the production
                    # pipeline became the local source of truth.
                    outputs.append(str(self._export_excel(destination, account_id, account["username"])))
            for kind in set(kinds) - {"excel"}:
                outputs.extend(str(item) for item in self._copy_job_artifacts(destination, account_id, kind))
        return {"count": len(outputs), "files": outputs}

    def list(self, value: dict[str, Any]) -> dict[str, Any]:
        account_ids = value.get("connection_ids")
        kind = value.get("kind")
        direction = value.get("direction")
        search = str(value.get("search", "")).strip().casefold()
        limit = int(value.get("limit", 50))
        date_from = value.get("date_from")
        date_to = value.get("date_to")
        if not isinstance(account_ids, list) or not 1 <= len(account_ids) <= 50 or len(set(account_ids)) != len(account_ids):
            raise ValueError("invalid_artifact_accounts")
        if kind not in ALLOWED_KINDS - {"excel"} or direction not in (None, "purchase", "sold") or not 1 <= limit <= 200:
            raise ValueError("invalid_artifact_query")
        offset = self._decode_cursor(value.get("cursor"))
        items: list[dict[str, Any]] = []
        for account_id in account_ids:
            account = self.storage.get_account(account_id)
            with closing(self.storage._connect()) as connection:
                latest = connection.execute("SELECT job_id FROM jobs WHERE account_id=? ORDER BY created_at DESC,job_id DESC LIMIT 1", (account_id,)).fetchone()
            job_id = latest[0] if latest else "local"
            for root in self._export_roots(account["username"]):
                if not root.exists() or self.data_directory not in root.parents:
                    continue
                for source in root.rglob(f"*.{kind}"):
                    relative = source.relative_to(root).as_posix()
                    lowered = relative.casefold()
                    inferred_direction = "purchase" if "purchase" in lowered or "mua" in lowered else "sold" if "sold" in lowered or "ban" in lowered else None
                    if direction and inferred_direction != direction:
                        continue
                    if search and search not in lowered:
                        continue
                    info = source.stat()
                    modified_date = datetime.fromtimestamp(info.st_mtime, timezone.utc).date().isoformat()
                    if date_from and modified_date < date_from or date_to and modified_date > date_to:
                        continue
                    items.append({
                        "artifact_id": f"{job_id}:{relative}", "connection_id": account_id, "job_id": job_id,
                        "filename": source.name, "kind": kind, "direction": inferred_direction,
                        "size": info.st_size, "updated_at": info.st_mtime_ns,
                    })
        items.sort(key=lambda item: (item["updated_at"], item["artifact_id"]), reverse=True)
        page = items[offset:offset + limit]
        next_offset = offset + len(page)
        has_more = next_offset < len(items)
        return {"items": page, "pagination": {"limit": limit, "has_more": has_more, "next_cursor": self._encode_cursor(next_offset) if has_more else None}}

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(f"a1:{offset}".encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(value: Any) -> int:
        if value in (None, ""):
            return 0
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError("invalid_artifact_cursor")
        try:
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
            prefix, offset = decoded.split(":", 1)
            if prefix != "a1" or not offset.isdigit():
                raise ValueError
            return int(offset)
        except (ValueError, UnicodeError):
            raise ValueError("invalid_artifact_cursor") from None

    def _export_excel(self, destination: Path, account_id: str, tax_code: str) -> Path:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Tong quan"
        sheet.append(["MST", "Hướng", "Mã hóa đơn", "Dữ liệu"])
        with closing(self.storage._connect()) as connection:
            rows = connection.execute(
                "SELECT direction,business_key,payload_json FROM invoice_overviews WHERE account_id=? ORDER BY overview_id", (account_id,),
            ).fetchall()
        for direction, business_key, payload in rows:
            sheet.append([safe_excel_value(tax_code), direction, safe_excel_value(business_key), safe_excel_value(json.dumps(json.loads(payload), ensure_ascii=False))])
        return self._atomic_workbook(destination, f"hoa-don-{safe_filename(tax_code)}.xlsx", workbook)

    def _copy_job_artifacts(self, destination: Path, account_id: str, kind: str) -> list[Path]:
        account = self.storage.get_account(account_id)
        copied = []
        extension = "xlsx" if kind == "excel" else kind
        for root in self._export_roots(account["username"]):
            if not root.exists() or self.data_directory not in root.parents:
                continue
            for source in root.rglob(f"*.{extension}"):
                target = self._available_path(destination, safe_filename(source.stem) + source.suffix.lower())
                temporary = target.with_name(f".{target.name}.tmp")
                try:
                    with source.open("rb") as reader, temporary.open("xb") as writer:
                        shutil.copyfileobj(reader, writer)
                        writer.flush()
                        os.fsync(writer.fileno())
                    temporary.replace(target)
                    copied.append(target)
                finally:
                    temporary.unlink(missing_ok=True)
        return copied

    def _atomic_workbook(self, destination: Path, filename: str, workbook: Workbook) -> Path:
        target = self._available_path(destination, filename)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".tmp", dir=destination)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            workbook.save(temporary)
            temporary.replace(target)
            return target
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _available_path(destination: Path, filename: str) -> Path:
        target = destination / filename
        for copy in range(1, 1000):
            if not target.exists():
                return target
            target = destination / f"{Path(filename).stem} ({copy}){Path(filename).suffix}"
        raise OSError("artifact_name_exhausted")
