"""Local-only job repository adapter for MIA Desktop.

The upstream source service calls ``create_admitted_job`` because its web/server
host adds worker-slot capacity admission around the relational repository.
Desktop has exactly one local runtime and one sequential worker, so the source
SQLite repository itself is the correct durable queue.  This adapter changes
only that host-facing method name; queueing, leases, recovery, progress and job
state transitions remain implemented by the upstream repository.
"""

from __future__ import annotations

from pathlib import Path

from app.job_engine.repository import SQLiteJobEngineRepository


class LocalSequentialJobRepository:
    """Expose the source SQLite repository without web worker-slot admission."""

    def __init__(self, database_path: Path | str) -> None:
        self.delegate = SQLiteJobEngineRepository(database_path)

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def create_admitted_job(self, request, tasks=(), *, stages=None, now=None):
        # ExternalApiService is reused only as an in-process source service. Its
        # server host normally calls a worker-slot admission facade here. Local
        # desktop should simply enqueue into the source relational repository.
        return self.delegate.create_job(
            request,
            tasks,
            stages=stages,
            now=now,
        )


def create_local_job_repository(*, sqlite_path: Path | str, **_ignored):
    return LocalSequentialJobRepository(sqlite_path)
