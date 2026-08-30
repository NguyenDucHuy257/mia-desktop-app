"""Single-worker process loop for MIA Desktop.

The upstream ``app.job_engine.worker`` module also contains the web/server host
that discovers logical worker slots and starts one thread per slot. Desktop
needs none of that. This loop drives exactly one upstream
SequentialWorkerSupervisor and keeps only durable lease recovery/backoff.
"""

from __future__ import annotations

import logging
import threading
import time


logger = logging.getLogger("mia.job_engine")

# Source portal work has one execution lane in the desktop process.  The local
# worker holds this lock for a complete source job; the XML/HTML artifact
# adapter uses the same lock while it calls the source package handler.  This
# prevents a queued crawl and an artifact batch from sharing a managed portal
# session concurrently without introducing another crawler worker.
SOURCE_EXECUTION_LOCK = threading.Lock()


class LocalWorkerLoop:
    """Run one sequential source supervisor until the desktop runtime stops."""

    def __init__(
        self,
        supervisor,
        *,
        idle_backoff_seconds: float = 2.0,
        error_backoff_seconds: float = 5.0,
        orphan_scan_seconds: float = 30.0,
    ) -> None:
        if min(idle_backoff_seconds, error_backoff_seconds, orphan_scan_seconds) <= 0:
            raise ValueError("worker backoff values must be positive")
        self.supervisor = supervisor
        self.idle_backoff_seconds = idle_backoff_seconds
        self.error_backoff_seconds = error_backoff_seconds
        self.orphan_scan_seconds = orphan_scan_seconds

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
                with SOURCE_EXECUTION_LOCK:
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
