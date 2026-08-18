from __future__ import annotations

import json
import os
import shutil
import tempfile
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
                outputs.append(str(self._export_excel(destination, account_id, account["username"])))
            for kind in set(kinds) - {"excel"}:
                outputs.extend(str(item) for item in self._copy_job_artifacts(destination, account_id, kind))
        return {"count": len(outputs), "files": outputs}

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
        with closing(self.storage._connect()) as connection:
            job_ids = [row[0] for row in connection.execute("SELECT job_id FROM jobs WHERE account_id=?", (account_id,)).fetchall()]
        copied = []
        for job_id in job_ids:
            root = (self.data_directory / "exports" / job_id).resolve()
            if root != self.data_directory and self.data_directory not in root.parents:
                continue
            if not root.exists():
                continue
            for source in root.rglob(f"*.{kind}"):
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
