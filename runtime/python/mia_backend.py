"""Thin desktop transport over the byte-for-byte vendored MIA source service."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import sys
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from app.account_connections.repository import SQLiteAccountConnectionRepository
from app.account_connections.service import AccountConnectionManager
from app.config.crawl_config import CrawlConfig
from app.external_api.app import _job_status
from app.external_api.models import (
    CreateAccountConnectionBody,
    CreateJobBody,
    ReconnectAccountConnectionBody,
)
from app.external_api.service import ExternalApiService
from app.job_engine.factory import create_job_engine_repository
from app.job_engine.models import JOB_TERMINAL_STATES
from app.job_engine.service import SequentialWorkerSupervisor
from app.job_engine.worker import WorkerLoop
from app.session_manager.crypto import SessionCipher
from app.session_manager.portal_authenticator import PortalAuthenticator
from app.session_manager.repository import SQLiteSessionRepository
from app.session_manager.service import SessionTokenManager
from app.utils.date_utils import BUSINESS_TIMEZONE
from app.worker_runtime.coverage_planner import CoveragePlanner
from app.worker_runtime.handler import InvoiceCrawlTaskHandler
from app.worker_runtime.pipeline import InvoiceCrawlPipeline
from app.worker_runtime.proxy import ProxyRegistry
from app.external_api.results import JobResultReader
from app.repositories.invoice_package_repository import InvoicePackageRepository
from app.services.invoice_pdf_export_service import InvoicePdfExportService


OWNER_ID = "mia-desktop-local"
WORKER_ID = "slot-direct"


class ProductionBackend:
    """Desktop host for the original source API/service/worker stack.

    No crawl scheduling, coverage policy, progress calculation, retry policy or
    public error mapping is reimplemented here. Those responsibilities remain
    in the vendored source modules pinned by VENDOR-MANIFEST.json.
    """

    def __init__(self, data_dir: Path, logger=None, *, start_worker: bool = True) -> None:
        self.data_dir = data_dir.resolve()
        self.data_root = self.data_dir / "source-data"
        self.control_db = self.data_dir / "source-control.sqlite3"
        self.logger = logger

        # All source factories and repositories point at the same local control
        # database. This is transport configuration, not a second data model.
        os.environ["MIA_DATA_ROOT"] = str(self.data_root)
        os.environ["MIA_CONTROL_DATABASE_URL"] = self._sqlite_url(self.control_db)

        self.repository = create_job_engine_repository(sqlite_path=self.control_db)
        self.session_repository = SQLiteSessionRepository(self.control_db)
        self.account_repository = SQLiteAccountConnectionRepository(self.control_db)
        self.cipher = SessionCipher.from_environment()
        self.sessions = SessionTokenManager(
            self.session_repository,
            self.cipher,
            PortalAuthenticator(),
        )
        self.accounts = AccountConnectionManager(
            self.account_repository,
            self.sessions,
            self.cipher,
        )
        self.coverage_planner = CoveragePlanner(self.data_root)
        self.service = ExternalApiService(
            self.repository,
            self.sessions,
            self.accounts,
            coverage_planner=self.coverage_planner,
            business_clock=lambda: datetime.now(BUSINESS_TIMEZONE),
        )
        self.service.initialize()

        config = CrawlConfig.from_env()
        config.validate()
        self.handler = InvoiceCrawlTaskHandler(
            self.repository,
            self.sessions,
            worker_id=WORKER_ID,
            data_root=self.data_root,
            crawl_config=config,
            proxy_registry=ProxyRegistry(direct_capacity=1),
        )
        self.pipeline = InvoiceCrawlPipeline(
            self.repository,
            self.handler,
            self.coverage_planner,
            clock=lambda: datetime.now(BUSINESS_TIMEZONE),
        )
        self.supervisor = SequentialWorkerSupervisor(
            self.repository,
            worker_id=WORKER_ID,
            pipeline=self.pipeline,
            job_lease_seconds=120,
            heartbeat_interval_seconds=10,
        )
        self.stop_event = threading.Event()
        self.worker_loop = WorkerLoop(self.supervisor)
        self.worker = threading.Thread(
            target=self.worker_loop.run,
            args=(self.stop_event,),
            daemon=True,
            name="mia-source-worker",
        )
        if start_worker:
            self.worker.start()

    @staticmethod
    def _sqlite_url(path: Path) -> str:
        value = path.resolve().as_posix()
        if len(value) >= 2 and value[1] == ":":
            value = "/" + value
        return "sqlite://" + value

    def close(self) -> None:
        self.stop_event.set()
        self.supervisor.request_shutdown()
        if self.worker.is_alive():
            self.worker.join(timeout=20)

    # ------------------------------------------------------------------
    # Source account-connection contract
    # ------------------------------------------------------------------
    @staticmethod
    def _connection_public(connection, *, reused: bool = False) -> dict[str, Any]:
        return {
            "connection_id": connection.connection_id,
            "username": connection.username,
            "company_name": None,
            "status": connection.status,
            "token_generation": connection.token_generation,
            "created_at": connection.created_at.isoformat(),
            "updated_at": connection.updated_at.isoformat(),
            "reused": reused,
        }

    def create_connection(self, username: str, password: str) -> dict[str, Any]:
        body = CreateAccountConnectionBody(username=username, password=password)
        connection, reused = self.service.create_account_connection(body, owner_id=OWNER_ID)
        return self._connection_public(connection, reused=reused)

    def get_connection(self, connection_id: str) -> dict[str, Any]:
        connection = self.service.get_account_connection(connection_id, owner_id=OWNER_ID)
        return self._connection_public(connection)

    def reconnect_connection(self, connection_id: str, username: str, password: str) -> dict[str, Any]:
        body = ReconnectAccountConnectionBody(username=username, password=password)
        connection = self.service.reconnect_account_connection(
            connection_id, body, owner_id=OWNER_ID
        )
        return self._connection_public(connection)

    def revoke_connection(self, connection_id: str) -> dict[str, Any]:
        connection = self.service.revoke_account_connection(connection_id, owner_id=OWNER_ID)
        return self._connection_public(connection)

    def list_connections(self) -> list[dict[str, Any]]:
        # The upstream API intentionally exposes create/get/reconnect/revoke but
        # no list route. Desktop needs inventory for its table, so enumerate only
        # IDs from the same upstream control DB and resolve each record through
        # the source AccountConnectionManager. No account state is synthesized.
        with closing(sqlite3.connect(self.control_db)) as connection:
            rows = connection.execute(
                """SELECT connection_id FROM account_connections
                   WHERE owner_id=? AND status<>'revoked'
                   ORDER BY created_at, connection_id""",
                (OWNER_ID,),
            ).fetchall()
        return [self.get_connection(str(row[0])) for row in rows]

    def connection_username(self, connection_id: str) -> str:
        return self.service.get_account_connection(
            connection_id, owner_id=OWNER_ID
        ).username

    # ------------------------------------------------------------------
    # Source job contract
    # ------------------------------------------------------------------
    def start(self, value: dict[str, Any]) -> dict[str, Any]:
        intent = dict(value["intent"])
        connection_id = str(intent["connection_id"])

        # Compatibility migration for accounts created by the former desktop
        # account store. New UI accounts use conn_* IDs directly.
        if not connection_id.startswith("conn_"):
            username = str(value["username"])
            password = str(value["password"])
            connection = self.create_connection(username, password)
            connection_id = str(connection["connection_id"])

        scopes = set(intent.get("scopes") or ("overview", "detail"))
        data_types = set(intent.get("data_types") or ("invoice",))
        body = CreateJobBody(
            connection_id=connection_id,
            date_from=date.fromisoformat(str(intent["date_from"])),
            date_to=date.fromisoformat(str(intent["date_to"])),
            directions=list(intent["directions"]),
            query_types=list(intent["query_types"]),
            force_refresh=bool(intent.get("force_refresh")),
            refresh_latest_month=bool(intent.get("refresh_latest_month", True)),
            result_scope="detail" if "detail" in scopes else "overview",
            include_xml=bool(data_types.intersection({"xml", "html", "pdf"})),
            include_mvt=False,
        )
        job = self.service.create_job(
            body,
            owner_id=OWNER_ID,
            idempotency_key=str(value["idempotency_key"]),
        )
        return self.public_job(job)

    def get(self, job_id: str) -> dict[str, Any]:
        return self.public_job(self.service.get_job(job_id, owner_id=OWNER_ID))

    def resume_all(self) -> list[dict[str, Any]]:
        return [
            self.public_job(job)
            for job in self.repository.list_jobs_for_reconciliation()
            if job.owner_id == OWNER_ID and job.status not in JOB_TERMINAL_STATES
        ]

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.public_job(self.service.cancel_job(job_id, owner_id=OWNER_ID))

    def summary(self, job_id: str) -> dict[str, Any]:
        return self.service.get_summary(job_id, owner_id=OWNER_ID)

    def _result_job(self, connection_id: str):
        candidates = [
            job for job in self.repository.list_jobs_for_reconciliation()
            if job.owner_id == OWNER_ID
            and str(job.parameters.get("connection_id")) == connection_id
        ]
        return max(candidates, key=lambda item: (item.created_at, item.job_id)) if candidates else None

    @staticmethod
    def _empty_result_page(limit: int) -> dict[str, Any]:
        return {
            "items": [],
            "pagination": {"limit": limit, "has_more": False, "next_cursor": None},
        }

    def results(self, kind: str, query: dict[str, Any]) -> dict[str, Any]:
        if kind not in {"overview", "details"}:
            raise ValueError("invalid_result_kind")
        limit = int(query.get("limit", 50))
        if not 1 <= limit <= 200:
            raise ValueError("invalid_result_limit")
        base_job = self._result_job(str(query["connection_id"]))
        if base_job is None:
            return self._empty_result_page(limit)

        date_from = str(query.get("date_from") or base_job.parameters["date_from"])
        date_to = str(query.get("date_to") or base_job.parameters["date_to"])
        if date.fromisoformat(date_from) > date.fromisoformat(date_to):
            raise ValueError("invalid_result_range")
        direction = query.get("direction")
        if direction not in (None, "purchase", "sold"):
            raise ValueError("invalid_result_direction")

        # Read-only desktop range/search view over the source DB. The data model
        # and cursor/page implementation remain JobResultReader from source.
        job = replace(base_job, parameters={
            **base_job.parameters,
            "date_from": date_from,
            "date_to": date_to,
            "directions": [direction] if direction else ["purchase", "sold"],
            "query_types": ["query", "sco-query"],
        })
        reader = JobResultReader(
            self.data_root / job.company_tax_code / "db" / "invoices.sqlite3"
        )
        cursor = query.get("cursor")
        search = str(query.get("search") or "").strip().casefold()
        output: list[dict[str, Any]] = []
        has_more = False
        while len(output) < limit:
            page = (
                reader.overview_page(job, limit=max(1, limit - len(output)), cursor=cursor)
                if kind == "overview"
                else reader.detail_page(job, limit=max(1, limit - len(output)), cursor=cursor)
            )
            for item in page["items"]:
                serialized = json.dumps(item, ensure_ascii=False, default=str)
                if search and search not in serialized.casefold():
                    continue
                parts = [
                    str(item.get(name, ""))
                    for name in ("nbmst", "khhdon", "shdon", "khmshdon")
                ]
                business_key = "|".join(parts)
                raw_id = item.get("id")
                identifier = (
                    int(raw_id)
                    if raw_id is not None
                    else int(hashlib.sha256(serialized.encode()).hexdigest()[:12], 16)
                )
                row = {
                    "direction": item.get("direction", "purchase"),
                    "business_key": business_key,
                    "payload": item,
                }
                if kind == "overview":
                    row["overview_id"] = identifier
                else:
                    row["detail_id"] = identifier
                    row["line_key"] = str(item.get("stt", identifier))
                output.append(row)
                if len(output) >= limit:
                    break
            cursor = page["pagination"]["next_cursor"]
            has_more = bool(page["pagination"]["has_more"])
            if not has_more or not cursor:
                break
        return {
            "items": output[:limit],
            "pagination": {
                "limit": limit,
                "has_more": has_more,
                "next_cursor": cursor if has_more else None,
            },
        }

    def export_results(self, value: dict[str, Any]) -> dict[str, Any]:
        destination = Path(value["destination"])
        if not destination.is_absolute():
            raise ValueError("invalid_artifact_directory")
        scopes = value.get("result_scopes") or []
        if not isinstance(scopes, list) or not scopes or set(scopes) - {"overview", "details"}:
            raise ValueError("invalid_result_export_scope")
        connection_ids = value.get("connection_ids") or []
        if len(connection_ids) != 1:
            raise ValueError("invalid_result_export_account")
        connection_id = str(connection_ids[0])
        base_job = self._result_job(connection_id)
        if base_job is None:
            raise ValueError("result_job_not_found")

        date_from = str(value["date_from"])
        date_to = str(value["date_to"])
        if date.fromisoformat(date_from) > date.fromisoformat(date_to):
            raise ValueError("invalid_result_range")
        query = {
            "connection_id": connection_id,
            "date_from": date_from,
            "date_to": date_to,
            "direction": value.get("direction"),
            "search": str(value.get("search") or ""),
        }
        workbook = Workbook()
        workbook.remove(workbook.active)
        for scope in scopes:
            rows = self._all_result_rows(scope, query)
            sheet = workbook.create_sheet("Tong quan" if scope == "overview" else "Chi tiet")
            self._write_result_sheet(sheet, rows, include_line=scope == "details")

        destination.mkdir(parents=True, exist_ok=True)
        filename = (
            f"ket-qua-{self._safe_filename(base_job.company_tax_code)}-"
            f"{date_from}_{date_to}.xlsx"
        )
        target = self._available_result_path(destination, filename)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}-", suffix=".tmp", dir=destination
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            workbook.save(temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"count": 1, "files": [str(target)]}

    def _all_result_rows(self, scope: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = None
        while True:
            page = self.results(scope, {**query, "cursor": cursor, "limit": 200})
            rows.extend(page["items"])
            if not page["pagination"]["has_more"] or not page["pagination"]["next_cursor"]:
                return rows
            cursor = page["pagination"]["next_cursor"]

    @staticmethod
    def _excel_value(value: Any):
        if value is None or isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, (dict, list, tuple)):
            text = json.dumps(value, ensure_ascii=False, default=str)
        else:
            text = str(value)
        return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text

    def _write_result_sheet(self, sheet, rows: list[dict[str, Any]], *, include_line: bool) -> None:
        payload_keys: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row.get("payload", {}):
                if key not in seen:
                    seen.add(key)
                    payload_keys.append(str(key))
        headers = ["Hướng", "Mã hóa đơn"]
        if include_line:
            headers.append("Dòng")
        headers.extend(payload_keys)
        sheet.append(headers)
        for row in rows:
            output = [
                "Mua vào" if row.get("direction") == "purchase" else "Bán ra",
                self._excel_value(row.get("business_key", "")),
            ]
            if include_line:
                output.append(self._excel_value(row.get("line_key", "")))
            payload = row.get("payload") or {}
            output.extend(self._excel_value(payload.get(key)) for key in payload_keys)
            sheet.append(output)

    @staticmethod
    def _safe_filename(value: str) -> str:
        cleaned = "".join(
            character if character.isalnum() or character in "._-" else "_"
            for character in str(value)
        ).strip("._")
        return cleaned[:80] or "hoa-don"

    @staticmethod
    def _available_result_path(destination: Path, filename: str) -> Path:
        candidate = destination / filename
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        for copy in range(1, 1000):
            if not candidate.exists():
                return candidate
            candidate = destination / f"{stem} ({copy}){suffix}"
        raise OSError("artifact_name_exhausted")

    def prepare_artifacts(self, value: dict[str, Any]) -> None:
        if "pdf" not in set(value.get("kinds") or ()):
            return
        for connection_id in value.get("connection_ids") or ():
            candidates = [
                job for job in self.repository.list_jobs_for_reconciliation()
                if str(job.parameters.get("connection_id")) == connection_id
                and job.status in {"completed", "completed_with_warning"}
            ]
            if not candidates:
                continue
            job = max(candidates, key=lambda item: (item.updated_at, item.job_id))
            database_path = self.data_root / job.company_tax_code / "db" / "invoices.sqlite3"
            service = InvoicePdfExportService(
                self.data_root, InvoicePackageRepository(database_path),
            )
            for direction in job.parameters["directions"]:
                for query_type in job.parameters["query_types"]:
                    service.export_invoice_pdfs(
                        job.company_tax_code, direction, query_type,
                        job.parameters["date_from"], job.parameters["date_to"],
                        overwrite=False,
                    )

    @staticmethod
    def public_job(job) -> dict[str, Any]:
        status = _job_status(job).model_dump()
        return {
            **status,
            "connection_id": str(job.parameters.get("connection_id") or job.account_key),
            "intent": {
                "connection_id": str(job.parameters.get("connection_id") or job.account_key),
                "date_from": job.parameters["date_from"],
                "date_to": job.parameters["date_to"],
                "directions": list(job.parameters["directions"]),
                "query_types": list(job.parameters["query_types"]),
                "scopes": ["overview", "detail"] if job.parameters.get("result_scope") == "detail" else ["overview"],
                "data_types": ["invoice", "xml"] if job.parameters.get("include_xml") else ["invoice"],
                "force_refresh": bool(job.parameters.get("force_refresh")),
                "refresh_latest_month": bool(job.parameters.get("refresh_latest_month")),
            },
            "idempotency_key": "managed-by-source-api",
            "created_at": job.created_at,
            "event_sequence": int(job.lease_generation),
        }
