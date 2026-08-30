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
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
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
        self.desktop_job_metadata: dict[str, object] | None = None

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

    def latest_invoice_job_for_account(self, account_key: str, *, owner_id: str | None = None):
        """Return the newest invoice job even when it is terminal/cancelled.

        ``list_jobs_for_reconciliation`` intentionally excludes failed/cancelled
        jobs so recovery can never resurrect them. Results presentation has a
        different requirement: a cancelled desktop batch may still have many
        invoice rows already committed to the source SQLite database. Reading
        those rows needs the latest job's company/range metadata, but must not
        make that job eligible for reconciliation again. This read-only lookup
        therefore queries the same source control table and returns the source
        ``JobRecord`` through the delegate.
        """
        database_path = self.delegate.database_path
        where_owner = " AND owner_id = ?" if owner_id is not None else ""
        params: tuple[object, ...] = (
            (account_key, owner_id) if owner_id is not None else (account_key,)
        )
        with closing(sqlite3.connect(database_path, timeout=30)) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            row = connection.execute(
                f"""
                SELECT job_id
                FROM crawl_jobs
                WHERE account_key = ?
                  AND job_type = 'invoice_crawl'
                  {where_owner}
                ORDER BY created_at DESC, job_id DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        return self.delegate.get_job(str(row[0])) if row else None

    def create_admitted_job(self, request, tasks=(), *, stages=None, now=None):
        # ExternalApiService is reused only as an in-process source service. Its
        # server host normally calls a worker-slot admission facade here. Local
        # desktop should simply enqueue into the source relational repository.
        # Reject the old UUID account namespace so it can never re-enter the
        # source worker queue after migration.
        if not str(request.account_key).startswith(_SOURCE_CONNECTION_PREFIX):
            raise ValueError("source_connection_required")
        metadata = dict(self.desktop_job_metadata or {})
        enriched_request = replace(
            request, parameters={**request.parameters, **metadata}
        ) if metadata else request
        return self.delegate.create_job(
            enriched_request,
            tasks,
            stages=stages,
            now=now,
        )

    def latest_invoice_job_for_direction(
        self, account_key: str, direction: str, *, owner_id: str | None = None
    ):
        """Latest durable job containing one direction, including terminal jobs."""
        where_owner = " AND owner_id = ?" if owner_id is not None else ""
        params: tuple[object, ...] = (
            (account_key, owner_id) if owner_id is not None else (account_key,)
        )
        with closing(sqlite3.connect(self.delegate.database_path, timeout=30)) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            rows = connection.execute(
                f"""SELECT job_id, parameters_json FROM crawl_jobs
                    WHERE account_key=? AND job_type='invoice_crawl' {where_owner}
                    ORDER BY created_at DESC, job_id DESC""",
                params,
            ).fetchall()
        for job_id, raw in rows:
            try:
                directions = json.loads(raw).get("directions") or ()
            except (TypeError, json.JSONDecodeError):
                continue
            if direction in directions:
                return self.delegate.get_job(str(job_id))
        return None

    def invoice_jobs_for_account(
        self, account_key: str, *, owner_id: str | None = None
    ):
        """Return all durable invoice jobs for read-only coverage analysis."""
        where_owner = " AND owner_id = ?" if owner_id is not None else ""
        params: tuple[object, ...] = (
            (account_key, owner_id) if owner_id is not None else (account_key,)
        )
        with closing(sqlite3.connect(self.delegate.database_path, timeout=30)) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            rows = connection.execute(
                f"""SELECT job_id FROM crawl_jobs
                    WHERE account_key=? AND job_type='invoice_crawl' {where_owner}
                    ORDER BY created_at, job_id""",
                params,
            ).fetchall()
        return [self.delegate.get_job(str(row[0])) for row in rows]

    def merge_job_parameters(self, job_id: str, values: dict[str, object]):
        """Durably add desktop worker metadata without changing source schema."""
        if not values:
            return self.delegate.get_job(job_id)
        with closing(sqlite3.connect(self.delegate.database_path, timeout=30)) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            with connection:
                row = connection.execute(
                    "SELECT parameters_json FROM crawl_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(job_id)
                parameters = json.loads(row[0])
                parameters.update(values)
                connection.execute(
                    "UPDATE crawl_jobs SET parameters_json=? WHERE job_id=?",
                    (json.dumps(parameters, ensure_ascii=False, sort_keys=True), job_id),
                )
        return self.delegate.get_job(job_id)


def create_local_job_repository(*, sqlite_path: Path | str, **_ignored):
    return LocalSequentialJobRepository(sqlite_path)
