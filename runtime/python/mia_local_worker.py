"""Single-worker process loop for MIA Desktop.

The upstream ``app.job_engine.worker`` module also contains the web/server host.
Desktop creates these lightweight loops itself: one loop per direct/proxy slot,
while each loop still owns exactly one SequentialWorkerSupervisor.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import nullcontext


logger = logging.getLogger("mia.job_engine")

# Legacy/default callers keep the original serialized lane. The 4.2 desktop
# pool explicitly disables this lock because every slot has an isolated route,
# handler and account job; SQLite lease fencing remains authoritative.
SOURCE_EXECUTION_LOCK = threading.Lock()


class LocalWorkerLoop:
    """Run one logical source slot until the desktop runtime stops."""

    def __init__(
        self,
        supervisor,
        *,
        idle_backoff_seconds: float = 2.0,
        error_backoff_seconds: float = 5.0,
        orphan_scan_seconds: float = 30.0,
        serialize_source: bool = True,
    ) -> None:
        if min(idle_backoff_seconds, error_backoff_seconds, orphan_scan_seconds) <= 0:
            raise ValueError("worker backoff values must be positive")
        self.supervisor = supervisor
        self.idle_backoff_seconds = idle_backoff_seconds
        self.error_backoff_seconds = error_backoff_seconds
        self.orphan_scan_seconds = orphan_scan_seconds
        self.serialize_source = serialize_source

    def run(self, stop_event: threading.Event, *, max_iterations=None) -> int:
        self.supervisor.set_stop_event(stop_event)
        logger.info("job_engine event=local_worker_started worker_id=%s", self.supervisor.worker_id)
        iterations = 0
        next_orphan_scan = time.monotonic()
        while not stop_event.is_set():
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1
            try:
                if time.monotonic() >= next_orphan_scan:
                    self.supervisor.repository.recover_expired_leases()
                    next_orphan_scan = time.monotonic() + self.orphan_scan_seconds
                with SOURCE_EXECUTION_LOCK if self.serialize_source else nullcontext():
                    result = self.supervisor.run_once()
            except Exception as exc:
                logger.exception(
                    "job_engine event=local_worker_iteration_failed worker_id=%s error_code=%s",
                    self.supervisor.worker_id,
                    exc.__class__.__name__,
                )
                if stop_event.wait(self.error_backoff_seconds):
                    break
                continue
            if result.job is None and stop_event.wait(self.idle_backoff_seconds):
                break
        logger.info(
            "job_engine event=local_worker_stopped worker_id=%s iterations=%s",
            self.supervisor.worker_id,
            iterations,
        )
        return iterations


# Keep the import name expected by mia_source_backend without importing the
# upstream module whose CLI/main starts a worker pool.
WorkerLoop = LocalWorkerLoop

__all__ = ["LocalWorkerLoop", "WorkerLoop"]
