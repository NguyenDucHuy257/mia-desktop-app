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

# mia_source_backend was originally written against two source server-host
# modules. Pre-seed those import names with local-only adapters so importing the
# backend never imports worker-slot admission or the multi-slot worker CLI.
factory_shim = types.ModuleType("app.job_engine.factory")
factory_shim.create_job_engine_repository = create_local_job_repository
sys.modules["app.job_engine.factory"] = factory_shim

worker_shim = types.ModuleType("app.job_engine.worker")
worker_shim.WorkerLoop = LocalWorkerLoop
sys.modules["app.job_engine.worker"] = worker_shim

import mia_source_backend as source_backend_module

source_backend_module.WORKER_ID = "desktop-local-worker"
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
        return SourceBackend.public_job(job)


__all__ = ["ProductionBackend", "SourceBackend"]
