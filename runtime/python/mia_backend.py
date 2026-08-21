"""Local desktop host for the source-of-truth crawl runtime.

MIA Desktop does not expose the source HTTP control API and does not run a
worker pool.  Electron talks to one Python process over local JSON-RPC; that
process hosts one sequential source worker backed by the source SQLite job
repository.  Crawl/cache/session/result behavior stays in the vendored source.
"""

from __future__ import annotations

import threading

import mia_source_backend as source_backend_module
from mia_local_job_repository import create_local_job_repository


# Inject the local host dependency before SourceBackend is instantiated.  The
# upstream web host uses SafeImmediateAdmissionJobRepository, which adds worker
# slots/capacity/proxy failover.  Desktop needs only the source SQLite queue.
source_backend_module.create_job_engine_repository = create_local_job_repository
source_backend_module.WORKER_ID = "desktop-local-worker"
SourceBackend = source_backend_module.SourceBackend


class ProductionBackend(SourceBackend):
    """One local source worker; no HTTP listener and no worker-slot admission."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Display-name metadata is desktop-only and its helpers can be nested
        # (_save -> _load/_write). RLock prevents a self-deadlock without
        # changing source crawler/session/job behavior.
        self._metadata_lock = threading.RLock()


__all__ = ["ProductionBackend", "SourceBackend"]
