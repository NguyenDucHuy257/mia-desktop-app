"""Local desktop host for the source-of-truth crawl runtime.

MIA Desktop does not expose the source HTTP control API and does not run a
worker pool. Electron talks to one Python process over local JSON-RPC; that
process hosts one sequential source worker backed by the source SQLite job
repository. Crawl/cache/session/result behavior stays in the vendored source.
"""

from __future__ import annotations

import sys
import sqlite3
import threading
import time
import types
from calendar import monthrange
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from mia_local_job_repository import create_local_job_repository
from mia_local_source_models import install_source_model_shim
from mia_local_worker import LocalWorkerLoop, SOURCE_EXECUTION_LOCK


# The upstream service imports Pydantic DTOs because its normal transport is
# HTTP/FastAPI. Desktop has no HTTP control API; install equivalent local value
# objects before importing the exact source service so Pydantic is not part of
# the local execution graph.
install_source_model_shim()

# mia_source_backend predates the local-only refactor and still contains a dead
# import of ``mia_crawler.verify_account``. ProductionBackend overrides both
# account methods that used it. In a clean desktop process, satisfy that import
# with a disabled shim so the legacy threaded crawler is not imported at all.
if "mia_crawler" not in sys.modules:
    legacy_crawler_shim = types.ModuleType("mia_crawler")

    def _legacy_verify_account_disabled(*_args, **_kwargs):
        raise RuntimeError("legacy_crawler_disabled")

    legacy_crawler_shim.verify_account = _legacy_verify_account_disabled
    sys.modules["mia_crawler"] = legacy_crawler_shim

# mia_source_backend was originally written against two source server-host
# modules. Pre-seed those import names with local-only adapters so importing the
# backend never imports worker-slot admission or the multi-slot worker CLI.
factory_shim = types.ModuleType("app.job_engine.factory")
factory_shim.create_job_engine_repository = create_local_job_repository
sys.modules["app.job_engine.factory"] = factory_shim

worker_shim = types.ModuleType("app.job_engine.worker")
worker_shim.WorkerLoop = LocalWorkerLoop
sys.modules["app.job_engine.worker"] = worker_shim

from mia_optimized_source_pipeline import OptimizedInvoiceCrawlPipeline
import mia_source_backend as source_backend_module

source_backend_module.WORKER_ID = "desktop-local-worker"
# Keep the vendored source pipeline authoritative while replacing only its
# redundant desktop-host orchestration: no post-commit reread verification and
# one materialized detail plan per sequential job.
source_backend_module.InvoiceCrawlPipeline = OptimizedInvoiceCrawlPipeline
SourceBackend = source_backend_module.SourceBackend


def _transport_datetime(value):
    """Normalize source ISO timestamp strings for the existing JSON adapter.

    ``JobRecord`` in mia-crawl-service deliberately stores timestamps as ISO
    strings. The older desktop adapter called ``.isoformat()`` unconditionally.
    Convert only at this presentation boundary; durable source records remain
    untouched and string timestamps pass through source repositories unchanged.
    """
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


_RESULT_EXPORT_VALUE_ERRORS = {
    "result_export_empty",
    "result_export_no_overview_data",
    "result_export_no_detail_data",
    "result_job_not_found",
    "invalid_artifact_directory",
    "invalid_result_export_range",
}


def _export_error(code: str) -> dict[str, object]:
    """Return only a stable public code; stack/path details remain in local logs."""
    return {"count": 0, "files": [], "error_code": code}


def _progress_totals_from_state(state):
    """Cumulative discovered denominators; moving month never resets counters."""
    progress_totals = {}
    for name, module in (state.get("modules") or {}).items():
        if not isinstance(module, dict):
            continue
        discovered = [
            month for month in module.get("months") or ()
            if isinstance(month, dict) and month.get("planned") is not None
        ]
        total = sum(max(0, int(month.get("planned") or 0)) for month in discovered)
        processed = sum(
            min(max(0, int(month.get("processed") or 0)), max(0, int(month.get("planned") or 0)))
            for month in discovered
        )
        if module.get("status") == "completed":
            processed = total
        progress_totals[str(name)] = {"processed": processed, "total": total}
    return progress_totals


def cancel_stale_jobs_for_desktop_session(data_dir, logger=None) -> int:
    """Cancel pre-launch work without constructing or starting a crawler."""
    repository = create_local_job_repository(
        sqlite_path=Path(data_dir) / "source-control.sqlite3"
    )
    repository.migrate()
    cancelled = 0
    for job in repository.list_jobs_for_reconciliation():
        if (
            job.owner_id != source_backend_module.OWNER_ID
            or job.status in source_backend_module.JOB_TERMINAL_STATES
        ):
            continue
        repository.request_cancellation(job.job_id)
        cancelled += 1
    if logger is not None:
        logger.info(
            "job_engine event=desktop_session_reset_cancel_requested count=%s",
            cancelled,
        )
    return cancelled


class ProductionBackend(SourceBackend):
    """One local source worker; no HTTP listener and no worker-slot admission."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Display-name metadata is desktop-only and its helpers can be nested
        # (_save -> _load/_write). RLock prevents a self-deadlock without
        # changing source crawler/session/job behavior.
        self._metadata_lock = threading.RLock()

    def _invoice_direction_metrics(self, tax_code: str, direction: str, job=None):
        database = self.data_root / tax_code / "db" / "invoices.sqlite3"
        if not database.is_file():
            return {
                "invoice_count": 0, "added_count": 0,
                "replaced_old_count": None, "downloaded_new_count": None,
                "sync_from": None, "sync_until": None,
            }
        with closing(sqlite3.connect(database, timeout=5)) as connection:
            connection.execute("PRAGMA busy_timeout = 5000")
            count = int(connection.execute(
                "SELECT COUNT(*) FROM invoice_overview_items WHERE company_tax_code=? AND direction=?",
                (tax_code, direction),
            ).fetchone()[0])
            coverage = connection.execute(
                """SELECT MIN(from_date), MAX(to_date)
                   FROM invoice_overview_checkpoints
                   WHERE company_tax_code=? AND direction=? AND checkpoint_status='finalized'""",
                (tax_code, direction),
            ).fetchone()
            baseline = (
                job.parameters.get("baseline_invoice_count") if job is not None else None
            )
            # The invoice count is read from committed overview rows on every
            # metadata poll. Do not infer inserts from crawler processed/planned
            # progress: UPSERTed duplicates do not increase this delta.
            added = max(0, count - int(baseline)) if baseline is not None else 0
            replaced = (
                job.parameters.get("replaced_old_count") if job is not None else None
            )
            downloaded = None
            if (
                job is not None
                and job.parameters.get("sync_mode") == "new"
                and job.parameters.get("replacement_prepared")
                and replaced is not None
            ):
                downloaded = int(connection.execute(
                    """SELECT COUNT(*) FROM invoice_overview_items
                       WHERE company_tax_code=? AND direction=?
                         AND nlap_date BETWEEN ? AND ?""",
                    (
                        tax_code, direction, str(job.parameters.get("date_from")),
                        str(job.parameters.get("date_to")),
                    ),
                ).fetchone()[0])
        return {
            "invoice_count": count,
            "added_count": added,
            "replaced_old_count": int(replaced) if replaced is not None else None,
            "downloaded_new_count": downloaded,
            "sync_from": coverage[0] if coverage else None,
            "sync_until": coverage[1] if coverage else None,
        }

    @staticmethod
    def _current_processing_until(job, state, month_key):
        if not job or not month_key:
            return None
        stage = str(state.get("current_stage") or getattr(job, "current_stage", "") or "")
        module = (state.get("modules") or {}).get(stage)
        if isinstance(module, dict):
            for month in module.get("months") or ():
                if isinstance(month, dict) and str(month.get("key") or "") == month_key:
                    to_date = str(month.get("to_date") or "")
                    if len(to_date) == 10:
                        return to_date
        try:
            year, month = (int(part) for part in month_key.split("-", 1))
            month_end = f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"
        except (TypeError, ValueError):
            return None
        job_to = str(job.parameters.get("date_to") or "")
        return min(month_end, job_to) if len(job_to) == 10 else month_end

    def start(self, value: dict[str, object]) -> dict[str, object]:
        intent = dict(value.get("intent") or {})
        mode = intent.get("sync_mode")
        directions = list(intent.get("directions") or ())
        if mode in {"new", "supplement"} and len(directions) != 1:
            raise ValueError("sync_mode_requires_one_direction")
        direction = str((directions or [""])[0])
        if mode in {"new", "supplement"}:
            # New is a force refresh of every requested month. Supplement uses
            # finalized Overview coverage and the desktop pipeline's current +
            # previous month realtime window to select only incremental work.
            intent["force_refresh"] = mode == "new"
            intent["refresh_latest_month"] = False
            connection = self.service.get_account_connection(
                str(intent["connection_id"]), owner_id=source_backend_module.OWNER_ID
            )
            baseline = self._invoice_direction_metrics(connection.username, direction)["invoice_count"]
            self.repository.desktop_job_metadata = {
                "sync_mode": mode,
                "baseline_invoice_count": baseline,
            }
        try:
            return super().start({**value, "intent": intent})
        finally:
            if hasattr(self, "repository"):
                self.repository.desktop_job_metadata = None

    def sync_states(self, connection_ids, direction):
        if direction not in {"purchase", "sold"}:
            raise ValueError("invalid_direction")
        output = []
        for connection_id in connection_ids:
            connection = self.service.get_account_connection(
                str(connection_id), owner_id=source_backend_module.OWNER_ID
            )
            job = self.repository.latest_invoice_job_for_direction(
                str(connection_id), direction, owner_id=source_backend_module.OWNER_ID
            )
            metrics = self._invoice_direction_metrics(connection.username, direction, job)
            state = dict(getattr(job, "progress_state", None) or {}) if job else {}
            modules = state.get("modules") or {}
            overview_module = modules.get("overview")
            overview_complete = (
                isinstance(overview_module, dict)
                and overview_module.get("status") == "completed"
            )
            raw_status = str(getattr(job, "status", "")) if job else ""
            active_statuses = {
                "queued", "starting", "waiting_account", "running",
                "processing", "downloading", "syncing", "cancelling",
            }
            overview_requested = "overview" in set(job.parameters.get("scopes") or ()) if job else False
            if raw_status in active_statuses and overview_complete:
                # Overview is the business completion boundary. Detail may
                # continue in the same source job without delaying this state.
                status = "completed"
            elif raw_status in active_statuses:
                # A current job always wins over finalized coverage from older
                # jobs until its Overview module reaches the business boundary.
                status = "running"
            elif raw_status in {"failed", "abandoned"}:
                status = "failed"
            elif raw_status == "cancelled":
                status = "cancelled"
            elif overview_complete or (
                raw_status in {"completed", "completed_with_warning"}
                and overview_requested
                and not isinstance(overview_module, dict)
            ):
                status = "completed"
            elif metrics["invoice_count"] > 0 or metrics["sync_until"]:
                status = "completed"
            else:
                status = "not_synced"
            month = state.get("current_month") if status == "running" else None
            month_key = month.get("key") if isinstance(month, dict) else None
            if status == "running" and not month_key and job:
                date_from = str(job.parameters.get("date_from") or "")
                month_key = date_from[:7] if len(date_from) >= 7 else None
            baseline = job.parameters.get("baseline_invoice_count") if job else None
            sync_from = metrics["sync_from"]
            sync_until = metrics["sync_until"]
            if status == "completed" and job:
                job_from = str(job.parameters.get("date_from") or "")
                job_until = str(job.parameters.get("date_to") or "")
                sync_from = job_from if len(job_from) == 10 else sync_from
                sync_until = job_until if len(job_until) == 10 else sync_until
            output.append({
                "connection_id": str(connection_id),
                "direction": direction,
                "status": status,
                "current_month": month_key,
                "current_until": self._current_processing_until(job, state, month_key)
                if status == "running" else None,
                "sync_from": sync_from,
                "sync_until": sync_until,
                "invoice_count": metrics["invoice_count"],
                "baseline_invoice_count": int(baseline) if baseline is not None else None,
                "added_invoice_count": metrics["added_count"] if baseline is not None else None,
                "replaced_old_count": metrics["replaced_old_count"],
                "downloaded_new_count": metrics["downloaded_new_count"],
                "last_job_id": job.job_id if job else None,
                "sync_mode": job.parameters.get("sync_mode") if job else None,
            })
        return output

    def _source_company_name(self, connection) -> str:
        """Read the company profile through the source-managed durable session.

        Account creation/reconnect and the crawler now share exactly the same
        encrypted session/token lifecycle. Desktop does not perform a second
        standalone portal login merely to discover the company display name.
        """
        _, session_hash = self.accounts.session_hash(
            connection.connection_id,
            owner_id=source_backend_module.OWNER_ID,
        )
        portal = self.sessions.build_worker_portal_session(
            session_hash,
            worker_id=source_backend_module.WORKER_ID,
        )
        company = portal.get_company_info()
        company_name = str(company.get("name") or "").strip()
        if not company_name:
            raise ValueError("missing_company_name")
        return company_name[:300]

    def create_connection(self, username: str, password: str):
        connection, reused = self.service.create_account_connection(
            source_backend_module.CreateAccountConnectionBody(
                username=username,
                password=password,
            ),
            owner_id=source_backend_module.OWNER_ID,
        )
        # Source create intentionally reuses an already-ready connection. An
        # explicit desktop import must still validate the password just typed,
        # so reconnect the same source connection before requesting company
        # info. This resets only source-managed encrypted auth state.
        if reused:
            connection = self.service.reconnect_account_connection(
                connection.connection_id,
                source_backend_module.ReconnectAccountConnectionBody(
                    username=username,
                    password=password,
                ),
                owner_id=source_backend_module.OWNER_ID,
            )
        try:
            self._save_company_name(
                connection.connection_id,
                self._source_company_name(connection),
            )
        except Exception:
            # A brand-new invalid credential must not leave a seemingly ready
            # account row behind. Reused connections remain in source's
            # auth_failed state so the user can reconnect them explicitly.
            if not reused:
                try:
                    self.service.revoke_account_connection(
                        connection.connection_id,
                        owner_id=source_backend_module.OWNER_ID,
                    )
                except Exception as cleanup_error:
                    logger = getattr(self, "logger", None)
                    if logger is not None:
                        logger.warning(
                            "invalid_account_cleanup_failed connection_ref=%s error_type=%s",
                            str(connection.connection_id)[-8:],
                            type(cleanup_error).__name__,
                        )
            raise
        return self.public_connection(connection, reused=reused)

    def reconnect_connection(self, connection_id: str, username: str, password: str):
        connection = self.service.reconnect_account_connection(
            connection_id,
            source_backend_module.ReconnectAccountConnectionBody(
                username=username,
                password=password,
            ),
            owner_id=source_backend_module.OWNER_ID,
        )
        self._save_company_name(
            connection.connection_id,
            self._source_company_name(connection),
        )
        return self.public_connection(connection)

    def list_connections(self):
        """Backfill only missing display names through source-managed auth."""
        connections = super().list_connections()
        known_names = self._load_company_names()
        for item in connections:
            if item.get("company_name"):
                # Migrate a legacy non-sensitive display name into the current
                # metadata file so future startups avoid another lookup.
                if item["connection_id"] not in known_names:
                    self._save_company_name(
                        item["connection_id"], str(item["company_name"])
                    )
                    known_names[item["connection_id"]] = str(item["company_name"])
                continue
            try:
                connection = self.service.get_account_connection(
                    item["connection_id"], owner_id=source_backend_module.OWNER_ID
                )
                company_name = self._source_company_name(connection)
                self._save_company_name(item["connection_id"], company_name)
                known_names[item["connection_id"]] = company_name
                item["company_name"] = company_name
            except Exception as error:
                if self.logger is not None:
                    self.logger.warning(
                        "company_name_backfill_failed connection_ref=%s error_type=%s",
                        str(item["connection_id"])[-8:],
                        type(error).__name__,
                    )
        return connections

    def cancel_active_jobs_for_exit(self) -> int:
        """Cancel local source jobs without deleting any persisted invoice data.

        Normal source host shutdown requeues a running taskless pipeline so a
        server worker can resume it later. Desktop has different UX semantics:
        closing the app means stop this user-initiated batch. Request source
        cancellation *before* worker shutdown so queued/waiting jobs become
        cancelled immediately and a running job moves to ``cancelling``. The
        source supervisor then terminalizes it as cancelled at its next safe
        interruption point instead of requeueing it. Already committed DB rows,
        checkpoints and artifacts remain untouched; the next manual sync still
        goes through CoveragePlanner and the source cache/refresh policy.
        """
        cancelled = 0
        for job in self.repository.list_jobs_for_reconciliation():
            if (
                job.owner_id != source_backend_module.OWNER_ID
                or job.status in source_backend_module.JOB_TERMINAL_STATES
            ):
                continue
            self.service.cancel_job(
                job.job_id,
                owner_id=source_backend_module.OWNER_ID,
            )
            cancelled += 1
        if self.logger is not None:
            self.logger.info(
                "job_engine event=desktop_exit_cancel_requested count=%s",
                cancelled,
            )
        return cancelled

    def close(self, *, cancel_jobs: bool = False) -> None:
        # Account purge/reinitialization may close the backend without meaning
        # "the user exited the app", so cancellation is explicit rather than
        # the default. Electron's runtime manager requests source cancellations
        # before it sends system.shutdown; cancel_jobs remains useful for direct
        # host callers and regression tests.
        if cancel_jobs:
            self.cancel_active_jobs_for_exit()
        SourceBackend.close(self)

    def _result_job(self, connection_id: str):
        """Resolve result metadata without making terminal jobs recoverable.

        Source reconciliation deliberately omits failed/cancelled jobs. Desktop
        Results must still read invoice rows that were committed before a user
        stopped a batch or exited the app, so use the local repository's
        read-only latest-job lookup across all terminal states. The returned job
        is used only to locate the source DB and project date/direction metadata.
        """
        lookup = getattr(self.repository, "latest_invoice_job_for_account", None)
        if callable(lookup):
            job = lookup(connection_id, owner_id=source_backend_module.OWNER_ID)
            if job is not None:
                return job
        return SourceBackend._result_job(self, connection_id)

    @staticmethod
    def public_job(job):
        # Upstream JobRecord timestamps are strings. Keep the source record
        # immutable and normalize only the three fields that the legacy JSON
        # serializer expects to expose via ``.isoformat()``.
        if any(
            isinstance(value, str)
            for value in (job.created_at, job.updated_at, job.progress_updated_at)
            if value is not None
        ):
            normalized = SimpleNamespace(**vars(job))
            normalized.created_at = _transport_datetime(job.created_at)
            normalized.updated_at = _transport_datetime(job.updated_at)
            normalized.progress_updated_at = (
                _transport_datetime(job.progress_updated_at)
                if job.progress_updated_at is not None
                else None
            )
            job = normalized
        payload = SourceBackend.public_job(job)
        if job.parameters.get("sync_mode") in {"new", "supplement"}:
            payload["intent"]["sync_mode"] = job.parameters["sync_mode"]
            payload["intent"]["force_refresh"] = job.parameters["sync_mode"] == "new"
            payload["intent"]["refresh_latest_month"] = False
        state = dict(getattr(job, "progress_state", None) or {})
        progress_totals = _progress_totals_from_state(state)
        payload["progress_totals"] = progress_totals
        active_stage = str(payload.get("stage") or "")
        if active_stage in progress_totals:
            payload["scope_progress"] = {
                "scope": active_stage,
                **progress_totals[active_stage],
            }
        elif payload.get("status") in {"completed", "completed_with_warning"} and "overview" in progress_totals:
            payload["scope_progress"] = {
                "scope": "overview",
                **progress_totals["overview"],
            }
        # The source repository already persists stage_progress_percent from its
        # ProgressSnapshot. Expose that value unchanged so the renderer can give
        # detailed auth/finalize feedback without inventing progress.
        payload["stage_percent"] = float(getattr(job, "stage_progress_percent", 0.0) or 0.0)
        # The optimized host records only the exact source unit currently being
        # executed. Counters remain the source month/overall counters.
        payload["current_direction"] = state.get("current_direction")
        payload["current_query_type"] = state.get("current_query_type")
        current_artifact = state.get("current_artifact")
        if isinstance(current_artifact, dict):
            allowed = {
                "direction", "query_type", "nbmst", "khhdon", "shdon", "khmshdon"
            }
            payload["current_artifact"] = {
                key: str(value)
                for key, value in current_artifact.items()
                if key in allowed and value is not None
            }
        artifact_progress = state.get("artifact_progress")
        if isinstance(artifact_progress, dict):
            safe_items = {}
            for key, item in (artifact_progress.get("items") or {}).items():
                if not isinstance(key, str) or len(key) > 512 or not isinstance(item, dict):
                    continue
                xml_state = item.get("xml")
                html_state = item.get("html")
                if xml_state not in {"running", "completed", "failed"}:
                    continue
                if html_state not in {"running", "completed", "failed"}:
                    continue
                safe_items[key] = {"xml": xml_state, "html": html_state}
            payload["artifact_progress"] = {
                "current_key": str(artifact_progress.get("current_key") or "")[:512],
                "processed": max(0, int(artifact_progress.get("processed") or 0)),
                "completed_xml": max(0, int(artifact_progress.get("completed_xml") or 0)),
                "completed_html": max(0, int(artifact_progress.get("completed_html") or 0)),
                "items": safe_items,
            }
        return payload

    def results(self, kind, query):
        # Import lazily so health/storage startup does not pay the Excel/result
        # dependency cost. All paging is delegated to source JobResultReader.
        from mia_source_results import read_results
        return read_results(self, kind, query)

    def result_facets(self, query):
        from mia_source_results import read_result_facets
        return read_result_facets(self, query)

    def artifact_keys_for_export(self, value):
        """Resolve the exact filtered Overview set through the source reader.

        This is presentation/filesystem orchestration only: invoice selection,
        search and cursor semantics stay owned by ``read_results``. Paths and
        database internals never cross JSON-RPC into the renderer.
        """
        connection_ids = value.get("connection_ids") or ()
        if len(connection_ids) != 1:
            return set()
        query = {
            "connection_id": str(connection_ids[0]),
            "date_from": value.get("date_from"),
            "date_to": value.get("date_to"),
            "direction": value.get("direction"),
            "query_type": value.get("query_type"),
            "search": str(value.get("search") or ""),
            "limit": 50,
        }
        return {item["artifact_key"] for item in self.artifact_targets_for_export(value)}

    def artifact_targets_for_export(self, value):
        """Materialize filtered Overview identities without querying the portal."""
        connection_ids = value.get("connection_ids") or ()
        if len(connection_ids) != 1:
            return []
        query_type = str(value.get("query_type") or "")
        if query_type not in {"query", "sco-query"}:
            raise ValueError("invalid_artifact_query_type")
        query = {
            "connection_id": str(connection_ids[0]),
            "date_from": value.get("date_from"),
            "date_to": value.get("date_to"),
            "direction": value.get("direction"),
            "query_type": query_type,
            "search": str(value.get("search") or ""),
        }
        from mia_source_results import read_artifact_targets
        return read_artifact_targets(self, query)

    def ensure_invoice_packages(
        self, value, *, progress_callback=None, cancel_callback=None
    ):
        """Fetch source XML packages from persisted Overview identities only.

        This thin desktop orchestration calls the vendored source handler for
        authentication, cache verification, retry, ZIP extraction and SQLite
        persistence.  It does not create a crawl job and therefore cannot run
        Overview or Detail.  The shared execution lock keeps it serialized with
        the one source worker.
        """
        kinds = tuple(dict.fromkeys(value.get("kinds") or ()))
        if not kinds or set(kinds) - {"xml", "html"}:
            raise ValueError("invalid_artifact_kind")
        targets = self.artifact_targets_for_export(value)
        if not targets:
            return {"processed": 0, "failed": 0, "targets": 0, "keys": set()}
        connection_id = str((value.get("connection_ids") or ())[0])
        _, session_hash = self.accounts.session_hash(
            connection_id, owner_id=source_backend_module.OWNER_ID
        )
        base_job = self._result_job(connection_id)
        if base_job is None:
            raise ValueError("result_job_not_found")
        job = replace(base_job, parameters={
            **base_job.parameters,
            "connection_id": connection_id,
            "session_hash": session_hash,
            "date_from": str(value.get("date_from")),
            "date_to": str(value.get("date_to")),
        })
        total = len(targets) * len(kinds)
        processed = failed = 0
        outcomes = {"downloaded": 0, "reused_verified": 0, "unavailable": 0, "failed": 0}
        batch_started = time.perf_counter()

        def emit(target, kind, state):
            if progress_callback:
                progress_callback({
                    "status": state,
                    "processed": processed, "total": total,
                    "percent": (processed / total * 100) if total else 100,
                    "artifact_key": target["artifact_key"], "kind": kind,
                })

        with SOURCE_EXECUTION_LOCK:
            if cancel_callback and cancel_callback():
                raise ValueError("artifact_cancelled")
            self.handler.authenticate_job(job)
            for target_index, target in enumerate(targets, start=1):
                if cancel_callback and cancel_callback():
                    raise ValueError("artifact_cancelled")
                for kind in kinds:
                    emit(target, kind, "running")
                payload = {
                    **target,
                    "session_hash": session_hash,
                    "date_from": str(value.get("date_from")),
                    "date_to": str(value.get("date_to")),
                    "export_xml": "xml" in kinds,
                    "export_html": "html" in kinds,
                }
                try:
                    outcome = self.handler.run_xml_unit(job, payload) or {}
                    outcome_name = str(outcome.get("outcome") or "downloaded")
                    state = "failed" if outcome_name == "unavailable" else "completed"
                    outcomes[outcome_name if outcome_name in outcomes else "downloaded"] += 1
                except Exception as error:
                    state = "failed"
                    outcomes["failed"] += 1
                    if self.logger is not None:
                        self.logger.warning(
                            "artifact_source_item_failed item=%s/%s error_type=%s",
                            target_index, len(targets), type(error).__name__,
                        )
                for kind in kinds:
                    processed += 1
                    if state == "failed":
                        failed += 1
                    emit(target, kind, state)
        if self.logger is not None:
            self.logger.info(
                "artifact_source_batch_complete targets=%s kinds=%s downloaded=%s "
                "reused=%s unavailable=%s failed=%s duration_ms=%.1f",
                len(targets), len(kinds), outcomes["downloaded"],
                outcomes["reused_verified"], outcomes["unavailable"],
                outcomes["failed"], (time.perf_counter() - batch_started) * 1000,
            )
        return {
            "processed": processed, "failed": failed, "targets": len(targets),
            "keys": {target["artifact_key"] for target in targets},
        }

    def export_results(self, value, *, progress_callback=None):
        """Build source-native Excel and preserve only safe failure categories."""
        from mia_source_results import export_results

        try:
            if progress_callback is None:
                return export_results(self, value)
            return export_results(
                self,
                value,
                progress_callback=progress_callback,
            )
        except PermissionError:
            return _export_error("artifact_write_denied")
        except FileNotFoundError:
            return _export_error("result_export_template_missing")
        except ValueError as error:
            code = str(error)
            if code == "result_export_empty":
                scopes = set(value.get("result_scopes") or ())
                if scopes == {"overview"}:
                    code = "result_export_no_overview_data"
                elif scopes == {"details"}:
                    code = "result_export_no_detail_data"
            if code in _RESULT_EXPORT_VALUE_ERRORS:
                return _export_error(code)
            raise
        except OSError:
            return _export_error("artifact_write_failed")
        except Exception as error:
            if self.logger is not None:
                self.logger.exception(
                    "result_export_failed error_type=%s",
                    type(error).__name__,
                )
            return _export_error("result_export_failed")


__all__ = ["ProductionBackend", "SourceBackend"]
