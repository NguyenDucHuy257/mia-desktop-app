"""Thin desktop boundary over the production MIA job engine (63acf11)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openpyxl import Workbook

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from app.account_connections.repository import SQLiteAccountConnectionRepository
from app.account_connections.service import AccountConnectionManager
from app.config.crawl_config import CrawlConfig
from app.job_engine.models import CreateJobRequest, JobStageSpec, JOB_TERMINAL_STATES
from app.job_engine.progress import build_pipeline_plan, current_month_public
from app.job_engine.repository import SQLiteJobEngineRepository
from app.job_engine.service import SequentialWorkerSupervisor
from app.session_manager.crypto import SessionCipher
from app.session_manager.portal_authenticator import PortalAuthenticator
from app.session_manager.repository import SQLiteSessionRepository
from app.session_manager.service import SessionTokenManager
from app.utils.date_utils import BUSINESS_TIMEZONE, split_by_calendar_month
from app.worker_runtime.coverage_planner import CoveragePlanner
from app.worker_runtime.handler import InvoiceCrawlTaskHandler
from app.worker_runtime.pipeline import InvoiceCrawlPipeline
from app.worker_runtime.proxy import ProxyRegistry
from app.external_api.results import JobResultReader
from app.repositories.invoice_package_repository import InvoicePackageRepository
from app.services.invoice_pdf_export_service import InvoicePdfExportService


OWNER_ID = "mia-desktop-local"
WORKER_ID = "desktop-direct"


def previous_calendar_month_start(value: date) -> date:
    """Return the first day of the calendar month immediately before value."""
    return (value.replace(day=1) - timedelta(days=1)).replace(day=1)


def current_calendar_month_end(value: date) -> date:
    """Return the last day of value's calendar month."""
    current = value.replace(day=1)
    next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month - timedelta(days=1)


class DesktopInvoiceCrawlTaskHandler(InvoiceCrawlTaskHandler):
    """Translate desktop artifact intent into the production package service."""

    def run_xml_unit(self, job, payload: dict, progress_callback=None):
        requested = set(job.parameters.get("data_types") or ())
        translated = {
            **payload,
            # The production coverage planner uses the XML file as the durable
            # package checkpoint. Keep that checkpoint for HTML/PDF jobs while
            # Electron exports only the user-requested artifact kind.
            "export_xml": True,
            "export_html": bool(requested.intersection({"html", "pdf"})),
        }
        return super().run_xml_unit(
            job, translated, progress_callback=progress_callback,
        )


class DesktopInvoiceCrawlPipeline(InvoiceCrawlPipeline):
    """Keep production behavior while forcing the two newest calendar months."""

    def _recent_refresh_range(self, parameters):
        # force_refresh is handled by the production planner and refreshes every
        # selected slice. Do not add a narrower detail force range in that case.
        if parameters.get("force_refresh") or not parameters.get("refresh_recent_months"):
            return None
        now = self.clock().astimezone(BUSINESS_TIMEZONE).date()
        request_from = date.fromisoformat(parameters["date_from"])
        request_to = date.fromisoformat(parameters["date_to"])
        begin = max(request_from, previous_calendar_month_start(now))
        end = min(request_to, current_calendar_month_end(now))
        return (begin, end) if begin <= end else None

    def _latest_month_range(self, parameters):
        # Vendor pipeline uses this hook to force detail decisions. Desktop
        # broadens it from one latest month to previous+current calendar month.
        return self._recent_refresh_range(parameters)

    def _latest_month_force_slices(self, parameters):
        forced = self._recent_refresh_range(parameters)
        if forced is None:
            return frozenset()
        begin, end = forced
        return frozenset(
            (direction, query_type, month_from, month_to)
            for month_from, month_to in split_by_calendar_month(begin, end)
            for direction in parameters["directions"]
            for query_type in parameters["query_types"]
        )


class ProductionBackend:
    def __init__(self, data_dir: Path, logger=None, *, start_worker: bool = True) -> None:
        self.data_dir = data_dir
        self.data_root = data_dir / "source-data"
        self.logger = logger
        control_db = data_dir / "source-control.sqlite3"
        self.repository = SQLiteJobEngineRepository(control_db)
        self.session_repository = SQLiteSessionRepository(control_db)
        self.account_repository = SQLiteAccountConnectionRepository(control_db)
        self.cipher = SessionCipher.from_environment()
        self.sessions = SessionTokenManager(
            self.session_repository, self.cipher, PortalAuthenticator(),
        )
        self.accounts = AccountConnectionManager(
            self.account_repository, self.sessions, self.cipher,
        )
        self.accounts.initialize()
        self.repository.migrate()
        config = CrawlConfig.from_env()
        config.validate()
        self.handler = DesktopInvoiceCrawlTaskHandler(
            self.repository, self.sessions, worker_id=WORKER_ID,
            data_root=self.data_root, crawl_config=config,
            proxy_registry=ProxyRegistry(direct_capacity=1),
        )
        self.pipeline = DesktopInvoiceCrawlPipeline(
            self.repository, self.handler, CoveragePlanner(self.data_root),
            clock=lambda: datetime.now(BUSINESS_TIMEZONE),
        )
        self.supervisor = SequentialWorkerSupervisor(
            self.repository, worker_id=WORKER_ID, pipeline=self.pipeline,
            job_lease_seconds=120, heartbeat_interval_seconds=10,
        )
        self.stop_event = threading.Event()
        self.supervisor.set_stop_event(self.stop_event)
        self.worker = threading.Thread(
            target=self._worker_loop, daemon=True, name="mia-production-worker",
        )
        if start_worker:
            self.worker.start()

    def close(self) -> None:
        self.stop_event.set()
        self.supervisor.request_shutdown()
        if self.worker.is_alive():
            self.worker.join(timeout=15)

    def start(self, value: dict[str, Any]) -> dict[str, Any]:
        intent = dict(value["intent"])
        connection, _ = self.accounts.create(
            username=value["username"], password=value["password"], owner_id=OWNER_ID,
        )
        scopes = set(intent["scopes"])
        data_types = set(intent.get("data_types") or ())
        include_xml = bool(data_types.intersection({"xml", "html", "pdf"}))
        # The production pipeline executes ensure_xml only after the detail
        # module. Artifact tabs may present an overview-only UI scope, but the
        # durable backend contract must include detail for package generation.
        result_scope = "detail" if "detail" in scopes or include_xml else "overview"
        pipeline_plan = build_pipeline_plan(
            result_scope, False, include_xml,
            date_from=date.fromisoformat(intent["date_from"]),
            date_to=date.fromisoformat(intent["date_to"]),
        )
        parameters = {
            "connection_id": intent["connection_id"],
            "session_hash": connection.session_hash,
            "company_tax_code": value["username"],
            "date_from": intent["date_from"], "date_to": intent["date_to"],
            "directions": sorted(intent["directions"]),
            "query_types": sorted(intent["query_types"]),
            # User checkbox refreshes all selected historical slices. Even when
            # unchecked, the desktop pipeline always refreshes previous+current
            # calendar month through refresh_recent_months.
            "force_refresh": bool(intent.get("force_refresh")),
            "refresh_recent_months": True,
            "refresh_latest_month": False,
            "result_scope": result_scope, "include_xml": include_xml,
            "include_mvt": False, "data_types": sorted(data_types),
            "pipeline_plan": pipeline_plan,
        }
        if self.logger is not None:
            self.logger.info(
                "source_job_refresh_policy force_refresh=%s recent_months=2 range=%s..%s",
                parameters["force_refresh"], parameters["date_from"], parameters["date_to"],
            )
        fingerprint = hashlib.sha256(json.dumps(
            parameters, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        key_hash = hashlib.sha256(value["idempotency_key"].encode()).hexdigest()
        job = self.repository.create_job(
            CreateJobRequest(
                account_key=intent["connection_id"], company_tax_code=value["username"],
                job_type="invoice_crawl", parameters=parameters, owner_id=OWNER_ID,
                idempotency_key_hash=key_hash, request_fingerprint=fingerprint,
                pipeline_version=2,
            ), (), stages=tuple(JobStageSpec(item["name"]) for item in pipeline_plan["stages"]),
        )
        return self.public_job(job)

    def get(self, job_id: str) -> dict[str, Any]:
        return self.public_job(self.repository.get_job(job_id))

    def resume_all(self) -> list[dict[str, Any]]:
        return [self.public_job(job) for job in self.repository.list_jobs_for_reconciliation()
                if job.status not in JOB_TERMINAL_STATES]

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self.public_job(self.repository.request_cancellation(job_id))

    def summary(self, job_id: str) -> dict[str, Any]:
        job = self.repository.get_job(job_id)
        return {"job_id": job.job_id, "status": job.status, "warning_count": job.warning_count,
                "work": job.progress_state or {}}

    def _result_job(self, connection_id: str):
        candidates = [
            job for job in self.repository.list_jobs_for_reconciliation()
            if str(job.parameters.get("connection_id")) == connection_id
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

        # Results are a view over every durable invoice already synchronized for
        # the account. The selected display range must not be constrained by
        # whichever historical job happens to be the newest record.
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
                if kind == "overview":
                    output.append({
                        "overview_id": identifier,
                        "direction": item.get("direction", "purchase"),
                        "business_key": business_key,
                        "payload": item,
                    })
                else:
                    output.append({
                        "detail_id": identifier,
                        "direction": item.get("direction", "purchase"),
                        "business_key": business_key,
                        "line_key": str(item.get("stt", identifier)),
                        "payload": item,
                    })
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
        """Run production post-processing required before local file copying."""
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

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                result = self.supervisor.run_once()
                if result.job is None:
                    self.stop_event.wait(0.25)
            except Exception as error:
                if self.logger is not None:
                    self.logger.error("production_worker_iteration_failed error_type=%s", type(error).__name__)
                self.stop_event.wait(1)

    @staticmethod
    def public_job(job) -> dict[str, Any]:
        state = dict(job.progress_state or {})
        return {
            "job_id": job.job_id,
            "connection_id": str(job.parameters.get("connection_id") or job.account_key),
            "intent": {
                "connection_id": str(job.parameters.get("connection_id") or job.account_key),
                "date_from": job.parameters["date_from"], "date_to": job.parameters["date_to"],
                "directions": list(job.parameters["directions"]),
                "query_types": list(job.parameters["query_types"]),
                "scopes": ["overview", "detail"] if job.parameters.get("result_scope") == "detail" else ["overview"],
                "data_types": list(job.parameters.get("data_types") or (
                    ["invoice", "xml"] if job.parameters.get("include_xml") else ["invoice"]
                )),
                "force_refresh": bool(job.parameters.get("force_refresh")),
            },
            "idempotency_key": "managed-by-production-engine",
            "status": job.status, "stage": job.current_stage,
            "overall_percent": max(0, min(100, int(float(job.progress_percent)))),
            "current_month": current_month_public(state),
            "created_at": job.created_at, "updated_at": job.updated_at,
            "error": {"code": job.last_error_code} if job.last_error_code else None,
            "event_sequence": int(job.lease_generation),
        }
