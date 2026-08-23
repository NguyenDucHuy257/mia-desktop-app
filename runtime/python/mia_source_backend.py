"""Desktop transport adapter over the vendored mia-crawl-service runtime.

Business behavior lives in the vendored source repository.  This module only
hosts those source classes inside the offline Electron process and adapts their
objects to JSON-RPC/file-system contracts used by the desktop shell.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from app.account_connections.repository import (
    SQLiteAccountConnectionRepository,
    _SELECT_CONNECTION,
    _connection_from_row,
)
from app.account_connections.service import AccountConnectionManager
from app.external_api.models import (
    CreateAccountConnectionBody,
    CreateJobBody,
    ReconnectAccountConnectionBody,
)
from app.external_api.results import JobResultReader
from app.external_api.service import ExternalApiService
from app.job_engine.factory import create_job_engine_repository
from app.job_engine.models import JOB_TERMINAL_STATES
from app.job_engine.progress import current_month_public
from app.job_engine.service import SequentialWorkerSupervisor
from app.job_engine.worker import WorkerLoop
from app.repositories.invoice_package_repository import InvoicePackageRepository
from app.services.invoice_pdf_export_service import InvoicePdfExportService
from app.session_manager.crypto import SessionCipher
from app.session_manager.portal_authenticator import PortalAuthenticator
from app.session_manager.repository import SQLiteSessionRepository
from app.session_manager.service import SessionTokenManager
from app.utils.date_utils import BUSINESS_TIMEZONE
from app.worker_runtime.coverage_planner import CoveragePlanner
from app.worker_runtime.handler import InvoiceCrawlTaskHandler
from app.worker_runtime.pipeline import InvoiceCrawlPipeline
from app.worker_runtime.proxy import ProxyRegistry

from mia_crawler import verify_account


OWNER_ID = "mia-desktop-local"
WORKER_ID = "slot-direct"


class SourceBackend:
    """Host exact source service/job/pipeline classes for the desktop client."""

    def __init__(self, data_dir: Path, logger=None, *, start_worker: bool = True) -> None:
        self.data_dir = Path(data_dir)
        self.data_root = self.data_dir / "source-data"
        self.logger = logger
        self.control_db = self.data_dir / "source-control.sqlite3"
        self.metadata_path = self.data_dir / "account-display.json"
        self._metadata_lock = threading.Lock()

        # Source-owned durable repositories and authentication/session model.
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
        self.planner = CoveragePlanner(self.data_root)
        self.service = ExternalApiService(
            self.repository,
            self.sessions,
            self.accounts,
            coverage_planner=self.planner,
            business_clock=lambda: datetime.now(BUSINESS_TIMEZONE),
        )
        self.service.initialize()

        # Exact source pipeline.  No desktop subclass calculates progress,
        # coverage decisions, retries, cache policy, lease behavior or stages.
        self.handler = InvoiceCrawlTaskHandler(
            self.repository,
            self.sessions,
            worker_id=WORKER_ID,
            data_root=self.data_root,
            proxy_registry=ProxyRegistry(direct_capacity=1),
        )
        self.pipeline = InvoiceCrawlPipeline(
            self.repository,
            self.handler,
            self.planner,
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
        self.supervisor.set_stop_event(self.stop_event)
        self.loop = WorkerLoop(
            self.supervisor,
            idle_backoff_seconds=0.25,
            error_backoff_seconds=1.0,
            orphan_scan_seconds=30.0,
        )
        self.worker = threading.Thread(
            target=self.loop.run,
            args=(self.stop_event,),
            daemon=True,
            name="mia-source-worker",
        )
        if start_worker:
            self.worker.start()

    def close(self) -> None:
        self.stop_event.set()
        self.supervisor.request_shutdown()
        if self.worker.is_alive():
            self.worker.join(timeout=15)

    # ------------------------------------------------------------------
    # Account connections: source repo is authoritative.  The JSON file only
    # stores a non-sensitive display name because the source API intentionally
    # does not persist company display names in account_connections.
    # ------------------------------------------------------------------

    def create_connection(self, username: str, password: str) -> dict[str, Any]:
        profile = verify_account(username, password)
        connection, reused = self.service.create_account_connection(
            CreateAccountConnectionBody(username=username, password=password),
            owner_id=OWNER_ID,
        )
        self._save_company_name(connection.connection_id, profile["company_name"])
        return self.public_connection(connection, reused=reused)

    def reconnect_connection(
        self, connection_id: str, username: str, password: str
    ) -> dict[str, Any]:
        profile = verify_account(username, password)
        connection = self.service.reconnect_account_connection(
            connection_id,
            ReconnectAccountConnectionBody(username=username, password=password),
            owner_id=OWNER_ID,
        )
        self._save_company_name(connection.connection_id, profile["company_name"])
        return self.public_connection(connection)

    def get_connection(self, connection_id: str) -> dict[str, Any]:
        return self.public_connection(
            self.service.get_account_connection(connection_id, owner_id=OWNER_ID)
        )

    def list_connections(self) -> list[dict[str, Any]]:
        # The upstream control API currently exposes create/get/reconnect/revoke
        # but no list endpoint.  Read its own schema/row converter rather than
        # maintaining a second desktop account database.
        with closing(self.account_repository._connect()) as connection:
            rows = connection.execute(
                _SELECT_CONNECTION
                + " WHERE c.owner_id = ? AND c.status <> 'revoked'"
                + " ORDER BY c.created_at, c.connection_id",
                (OWNER_ID,),
            ).fetchall()
        return [self.public_connection(_connection_from_row(row)) for row in rows]

    def revoke_connection(self, connection_id: str) -> dict[str, Any]:
        connection = self.service.revoke_account_connection(
            connection_id, owner_id=OWNER_ID
        )
        self._remove_company_name(connection_id)
        return self.public_connection(connection)

    def connection_tax_code(self, connection_id: str) -> str:
        return self.service.get_account_connection(
            connection_id, owner_id=OWNER_ID
        ).username

    def public_connection(self, connection, *, reused: bool = False) -> dict[str, Any]:
        names = self._load_company_names()
        company_name = names.get(connection.connection_id)
        if not company_name:
            company_name = self._legacy_company_name(connection.username)
        return {
            "connection_id": connection.connection_id,
            "username": connection.username,
            "company_name": company_name,
            "status": connection.status,
            "token_generation": connection.token_generation,
            "created_at": connection.created_at.isoformat(),
            "updated_at": connection.updated_at.isoformat(),
            "reused": bool(reused),
        }

    def _load_company_names(self) -> dict[str, str]:
        with self._metadata_lock:
            try:
                value = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return {}
            if not isinstance(value, dict):
                return {}
            return {
                str(key): str(name)
                for key, name in value.items()
                if isinstance(name, str) and name.strip()
            }

    def _write_company_names(self, value: dict[str, str]) -> None:
        with self._metadata_lock:
            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.metadata_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, self.metadata_path)

    def _save_company_name(self, connection_id: str, company_name: str) -> None:
        current = self._load_company_names()
        current[connection_id] = company_name.strip()[:300]
        self._write_company_names(current)

    def _remove_company_name(self, connection_id: str) -> None:
        current = self._load_company_names()
        if current.pop(connection_id, None) is not None:
            self._write_company_names(current)

    def _legacy_company_name(self, username: str) -> str | None:
        # One-way compatibility read for accounts created by pre-refactor builds.
        # It never reads or returns the legacy encrypted password.
        database = self.data_dir / "mia.sqlite3"
        if not database.is_file():
            return None
        try:
            with closing(sqlite3.connect(database, timeout=2)) as connection:
                row = connection.execute(
                    "SELECT company_name FROM accounts WHERE tax_code=? "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (username,),
                ).fetchone()
        except sqlite3.Error:
            return None
        return str(row[0]).strip() if row and row[0] else None

    # ------------------------------------------------------------------
    # Jobs: exact ExternalApiService admission + exact source WorkerLoop.
    # ------------------------------------------------------------------

    def start(self, value: dict[str, Any]) -> dict[str, Any]:
        intent = dict(value["intent"])
        sync_mode = intent.get("sync_mode")
        if sync_mode not in {None, "new", "supplement"}:
            raise ValueError("invalid_sync_mode")
        if sync_mode is not None and len(list(intent.get("directions") or ())) != 1:
            raise ValueError("sync_mode_requires_one_direction")
        if sync_mode == "new":
            intent["force_refresh"] = True
            intent["refresh_latest_month"] = False
        elif sync_mode == "supplement":
            intent["force_refresh"] = False
            intent["refresh_latest_month"] = True
        scopes = set(intent.get("scopes") or ())
        data_types = set(intent.get("data_types") or ("invoice",))
        include_xml = bool(data_types.intersection({"xml", "html", "pdf"}))
        result_scope = "detail" if "detail" in scopes or include_xml else "overview"
        body = CreateJobBody(
            connection_id=str(intent["connection_id"]),
            date_from=date.fromisoformat(str(intent["date_from"])),
            date_to=date.fromisoformat(str(intent["date_to"])),
            directions=list(intent["directions"]),
            query_types=list(intent["query_types"]),
            force_refresh=bool(intent.get("force_refresh")),
            # This is the source API option.  Desktop no longer broadens it to
            # a custom two-month policy; CoveragePlanner remains authoritative.
            refresh_latest_month=bool(intent.get("refresh_latest_month", True)),
            result_scope=result_scope,
            include_xml=include_xml,
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

    def latest_all(self) -> list[dict[str, Any]]:
        latest: dict[str, Any] = {}
        for job in self.repository.list_jobs_for_reconciliation():
            if job.owner_id != OWNER_ID:
                continue
            connection_id = str(job.parameters.get("connection_id") or job.account_key)
            previous = latest.get(connection_id)
            if previous is None or (job.created_at, job.job_id) > (
                previous.created_at, previous.job_id
            ):
                latest[connection_id] = job
        return [self.public_job(job) for job in latest.values()]

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.public_job(self.service.cancel_job(job_id, owner_id=OWNER_ID))

    def summary(self, job_id: str) -> dict[str, Any]:
        return dict(self.service.get_summary(job_id, owner_id=OWNER_ID))

    @staticmethod
    def public_job(job) -> dict[str, Any]:
        state = dict(job.progress_state or {})
        stage = job.current_stage or state.get("current_stage")
        error = None
        if job.last_error_code:
            error = {
                "code": str(job.last_error_code),
                # Do not invent a desktop message.  The source worker persists a
                # safe public error message alongside its error code.
                "message": str(job.last_error_message or job.last_error_code),
                "retryable": False,
            }
        return {
            "job_id": job.job_id,
            "connection_id": str(job.parameters.get("connection_id") or job.account_key),
            "intent": {
                "connection_id": str(job.parameters.get("connection_id") or job.account_key),
                "date_from": job.parameters["date_from"],
                "date_to": job.parameters["date_to"],
                "directions": list(job.parameters["directions"]),
                "query_types": list(job.parameters["query_types"]),
                "scopes": (
                    ["overview", "detail"]
                    if job.parameters.get("result_scope") == "detail"
                    else ["overview"]
                ),
                "data_types": ["invoice", "xml"] if job.parameters.get("include_xml") else ["invoice"],
                "force_refresh": bool(job.parameters.get("force_refresh")),
            },
            "idempotency_key": "managed-by-source-service",
            "status": job.status,
            "stage": stage,
            "overall_percent": float(job.progress_percent),
            "current_month": current_month_public(state),
            # The source progress_state already owns the human-readable/current
            # operation message.  Renderer displays it verbatim.
            "message": state.get("message"),
            "created_at": job.created_at.isoformat(),
            "updated_at": (
                job.progress_updated_at or job.updated_at
            ).isoformat(),
            "error": error,
            "event_sequence": int(job.lease_generation),
        }

    # ------------------------------------------------------------------
    # Desktop read/export adapters.  They call the source JobResultReader and
    # source artifact services; no crawl/progress/cache decision is made here.
    # ------------------------------------------------------------------

    def _result_job(self, connection_id: str):
        candidates = [
            job
            for job in self.repository.list_jobs_for_reconciliation()
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

        job = replace(
            base_job,
            parameters={
                **base_job.parameters,
                "date_from": date_from,
                "date_to": date_to,
                "directions": [direction] if direction else ["purchase", "sold"],
                "query_types": ["query", "sco-query"],
            },
        )
        reader = JobResultReader(
            self.data_root / job.company_tax_code / "db" / "invoices.sqlite3"
        )
        cursor = query.get("cursor")
        search = str(query.get("search") or "").strip().casefold()
        output: list[dict[str, Any]] = []
        has_more = False
        while len(output) < limit:
            page = (
                reader.overview_page(
                    job, limit=max(1, limit - len(output)), cursor=cursor
                )
                if kind == "overview"
                else reader.detail_page(
                    job, limit=max(1, limit - len(output)), cursor=cursor
                )
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
                if kind == "overview":
                    output.append(
                        {
                            "overview_id": identifier,
                            "direction": item.get("direction", "purchase"),
                            "business_key": business_key,
                            "payload": item,
                        }
                    )
                else:
                    output.append(
                        {
                            "detail_id": identifier,
                            "direction": item.get("direction", "purchase"),
                            "business_key": business_key,
                            "line_key": str(item.get("stt", identifier)),
                            "payload": item,
                        }
                    )
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
            sheet = workbook.create_sheet(
                "Tong quan" if scope == "overview" else "Chi tiet"
            )
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

    def _write_result_sheet(
        self, sheet, rows: list[dict[str, Any]], *, include_line: bool
    ) -> None:
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
                job
                for job in self.repository.list_jobs_for_reconciliation()
                if str(job.parameters.get("connection_id")) == connection_id
                and job.status in {"completed", "completed_with_warning"}
            ]
            if not candidates:
                continue
            job = max(candidates, key=lambda item: (item.updated_at, item.job_id))
            database_path = (
                self.data_root / job.company_tax_code / "db" / "invoices.sqlite3"
            )
            service = InvoicePdfExportService(
                self.data_root, InvoicePackageRepository(database_path)
            )
            for direction in job.parameters["directions"]:
                for query_type in job.parameters["query_types"]:
                    service.export_invoice_pdfs(
                        job.company_tax_code,
                        direction,
                        query_type,
                        job.parameters["date_from"],
                        job.parameters["date_to"],
                        overwrite=False,
                    )
