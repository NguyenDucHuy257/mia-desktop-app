"""Thin desktop boundary over the production MIA job engine (63acf11)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

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
from app.utils.date_utils import BUSINESS_TIMEZONE
from app.worker_runtime.coverage_planner import CoveragePlanner
from app.worker_runtime.handler import InvoiceCrawlTaskHandler
from app.worker_runtime.pipeline import InvoiceCrawlPipeline
from app.worker_runtime.proxy import ProxyRegistry
from app.external_api.results import JobResultReader
from app.repositories.invoice_package_repository import InvoicePackageRepository
from app.services.invoice_pdf_export_service import InvoicePdfExportService


OWNER_ID = "mia-desktop-local"
WORKER_ID = "desktop-direct"


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
        self.pipeline = InvoiceCrawlPipeline(
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
            "force_refresh": False, "refresh_latest_month": False,
            "result_scope": result_scope, "include_xml": include_xml,
            "include_mvt": False, "data_types": sorted(data_types),
            "pipeline_plan": pipeline_plan,
        }
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

    def results(self, kind: str, query: dict[str, Any]) -> dict[str, Any]:
        candidates = [job for job in self.repository.list_jobs_for_reconciliation()
                      if str(job.parameters.get("connection_id")) == query["connection_id"]]
        if not candidates:
            return {"items": [], "pagination": {"limit": int(query.get("limit", 50)), "has_more": False, "next_cursor": None}}
        job = max(candidates, key=lambda item: (item.updated_at, item.job_id))
        direction = query.get("direction")
        if direction:
            job = replace(job, parameters={**job.parameters, "directions": [direction]})
        reader = JobResultReader(self.data_root / job.company_tax_code / "db" / "invoices.sqlite3")
        limit = int(query.get("limit", 50))
        cursor = query.get("cursor")
        search = str(query.get("search") or "").strip().casefold()
        output: list[dict[str, Any]] = []
        has_more = False
        while len(output) < limit:
            page = (reader.overview_page(job, limit=max(1, limit - len(output)), cursor=cursor)
                    if kind == "overview" else reader.detail_page(job, limit=max(1, limit - len(output)), cursor=cursor))
            for item in page["items"]:
                serialized = json.dumps(item, ensure_ascii=False, default=str)
                if search and search not in serialized.casefold():
                    continue
                parts = [str(item.get(name, "")) for name in ("nbmst", "khhdon", "shdon", "khmshdon")]
                business_key = "|".join(parts)
                raw_id = item.get("id")
                identifier = int(raw_id) if raw_id is not None else int(hashlib.sha256(serialized.encode()).hexdigest()[:12], 16)
                if kind == "overview":
                    output.append({"overview_id": identifier, "direction": item.get("direction", "purchase"),
                                   "business_key": business_key, "payload": item})
                else:
                    output.append({"detail_id": identifier, "direction": item.get("direction", "purchase"),
                                   "business_key": business_key, "line_key": str(item.get("stt", identifier)), "payload": item})
            cursor = page["pagination"]["next_cursor"]
            has_more = bool(page["pagination"]["has_more"])
            if not has_more or not cursor:
                break
        return {"items": output[:limit], "pagination": {"limit": limit, "has_more": has_more,
                                                          "next_cursor": cursor if has_more else None}}

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
            },
            "idempotency_key": "managed-by-production-engine",
            "status": job.status, "stage": job.current_stage,
            "overall_percent": max(0, min(100, int(float(job.progress_percent)))),
            "current_month": current_month_public(state),
            "created_at": job.created_at, "updated_at": job.updated_at,
            "error": {"code": job.last_error_code} if job.last_error_code else None,
            "event_sequence": int(job.lease_generation),
        }
