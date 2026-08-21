"""Local desktop host for the source-of-truth crawl runtime.

MIA Desktop does not expose the source HTTP control API and does not run a
worker pool. Electron talks to one Python process over local JSON-RPC; that
process hosts one sequential source worker backed by the source SQLite job
repository. Crawl/cache/session/result behavior stays in the vendored source.
"""

from __future__ import annotations

import sys
import threading
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from mia_local_job_repository import create_local_job_repository
from mia_local_source_models import install_source_model_shim
from mia_local_worker import LocalWorkerLoop


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


class ProductionBackend(SourceBackend):
    """One local source worker; no HTTP listener and no worker-slot admission."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Display-name metadata is desktop-only and its helpers can be nested
        # (_save -> _load/_write). RLock prevents a self-deadlock without
        # changing source crawler/session/job behavior.
        self._metadata_lock = threading.RLock()

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
        self._save_company_name(
            connection.connection_id,
            self._source_company_name(connection),
        )
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
        state = dict(getattr(job, "progress_state", None) or {})
        # The source repository already persists stage_progress_percent from its
        # ProgressSnapshot. Expose that value unchanged so the renderer can give
        # detailed auth/finalize feedback without inventing progress.
        payload["stage_percent"] = float(getattr(job, "stage_progress_percent", 0.0) or 0.0)
        # The optimized host records only the exact source unit currently being
        # executed. Counters remain the source month/overall counters.
        payload["current_direction"] = state.get("current_direction")
        payload["current_query_type"] = state.get("current_query_type")
        return payload

    def results(self, kind, query):
        # Import lazily so health/storage startup does not pay the Excel/result
        # dependency cost. All paging is delegated to source JobResultReader.
        from mia_source_results import read_results
        return read_results(self, kind, query)

    def export_results(self, value):
        # Excel is built only after the user clicks Download. Detail work uses
        # the exact vendored source exporter/template against persisted data.
        from mia_source_results import export_results
        return export_results(self, value)


__all__ = ["ProductionBackend", "SourceBackend"]
