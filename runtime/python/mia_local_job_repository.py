"""Local-only job repository adapter for MIA Desktop.

The upstream source service calls ``create_admitted_job`` because its web/server
host adds worker-slot capacity admission around the relational repository.
Desktop has exactly one local runtime and one sequential worker, so the source
SQLite repository itself is the correct durable queue. This adapter changes
only host-facing behavior: web admission is bypassed and pre-refactor desktop
jobs that cannot be executed by source ``conn_*`` accounts are retired before
the single local worker starts. Queueing, leases, recovery, progress and job
state transitions for source-compatible jobs remain implemented upstream.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "mia_crawl_service"
if str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))

from app.job_engine.repository import SQLiteJobEngineRepository


logger = logging.getLogger("mia.job_engine")
_SOURCE_CONNECTION_PREFIX = "conn_"
_LEGACY_JOB_ERROR_CODE = "desktop_legacy_job_retired"
_LEGACY_JOB_ERROR_MESSAGE = (
    "Pre-refactor desktop job was retired because its account is not a source connection"
)


class LocalSequentialJobRepository:
    """Expose the source SQLite repository without web worker-slot admission."""

    def __init__(self, database_path: Path | str) -> None:
        self.delegate = SQLiteJobEngineRepository(database_path)

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def migrate(self) -> None:
        """Migrate source schema and retire incompatible pre-refactor work.

        Older desktop builds wrote pipeline-v2 jobs whose ``account_key`` was a
        desktop UUID. The source worker can still claim those rows because the
        upstream claim query is intentionally global. With one local worker,
        such a recovered legacy job can therefore starve every new ``conn_*``
        job for hours. Retire only non-terminal jobs whose account key is not a
        source connection; persisted invoice/source data is left untouched.
        """
        self.delegate.migrate()
        retired = self._retire_incompatible_legacy_jobs()
        if retired:
            logger.warning(
                "job_engine event=legacy_desktop_jobs_retired count=%s error_code=%s",
                retired,
                _LEGACY_JOB_ERROR_CODE,
            )

    def _retire_incompatible_legacy_jobs(self) -> int:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        database_path = self.delegate.database_path
        with closing(sqlite3.connect(database_path, timeout=30)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            cursor = connection.execute(
                """
                UPDATE crawl_jobs
                SET status = 'cancelled',
                    current_stage = NULL,
                    worker_id = NULL,
                    lease_token = NULL,
                    lease_started_at = NULL,
                    lease_expires_at = NULL,
                    cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    finished_at = COALESCE(finished_at, ?),
                    updated_at = ?,
                    last_error_code = ?,
                    last_error_message = ?
                WHERE pipeline_version = 2
                  AND status IN ('waiting_account', 'queued', 'running', 'cancelling')
                  AND account_key NOT GLOB 'conn_*'
                """,
                (
                    timestamp,
                    timestamp,
                    timestamp,
                    _LEGACY_JOB_ERROR_CODE,
                    _LEGACY_JOB_ERROR_MESSAGE,
                ),
            )
            connection.commit()
            return int(cursor.rowcount)

    def create_admitted_job(self, request, tasks=(), *, stages=None, now=None):
        # ExternalApiService is reused only as an in-process source service. Its
        # server host normally calls a worker-slot admission facade here. Local
        # desktop should simply enqueue into the source relational repository.
        # Reject the old UUID account namespace so it can never re-enter the
        # source worker queue after migration.
        if not str(request.account_key).startswith(_SOURCE_CONNECTION_PREFIX):
            raise ValueError("source_connection_required")
        return self.delegate.create_job(
            request,
            tasks,
            stages=stages,
            now=now,
        )


def create_local_job_repository(*, sqlite_path: Path | str, **_ignored):
    return LocalSequentialJobRepository(sqlite_path)
