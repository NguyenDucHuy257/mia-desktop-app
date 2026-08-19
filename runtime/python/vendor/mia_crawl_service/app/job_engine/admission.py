from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence

from app.config.crawl_config import parse_proxy_list
from app.job_engine.models import (
    AccountBusyError,
    CapacityExhaustedError,
    CreateJobRequest,
    IdempotencyConflictError,
    InvalidJobTransitionError,
    JobStageSpec,
    LeaseLostError,
)
from app.job_engine.progress import initial_progress_state
from app.postgres_migration_lock import acquire_control_schema_migration_lock
from app.job_engine.repository import _json_dump


class ImmediateAdmissionJobRepository:
    """Admission-control facade over the existing relational job repository.

    New pipeline-v2 jobs reserve an account and a logical worker slot inside the
    same control-database transaction that inserts the job. Legacy queued rows
    remain readable/claimable for rollback compatibility, but this facade never
    creates a new queued or waiting_account job.
    """

    def __init__(self, delegate) -> None:
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def migrate(self) -> None:
        self.delegate.migrate()
        with self.delegate._transaction() as connection:
            if getattr(connection, '_connection', None) is not None:
                acquire_control_schema_migration_lock(connection)
            _execute_script(connection, _ADMISSION_SCHEMA)
            self._ensure_worker_claimed_column(connection)
            self._sync_slots(connection, _timestamp(self.delegate._resolve_now(None)))
            connection.execute(
                _migration_sql(connection),
                _migration_parameters(connection),
            )

    def create_job(self, request, tasks=(), *, stages=None, now=None):
        # Compatibility path for tests/tools that explicitly require the old
        # repository behavior. Public API calls create_admitted_job.
        return self.delegate.create_job(request, tasks, stages=stages, now=now)

    def create_admitted_job(
        self,
        request: CreateJobRequest,
        tasks: Sequence = (),
        *,
        stages: Sequence[JobStageSpec] | None = None,
        lease_seconds: int | None = None,
        now: datetime | None = None,
    ):
        """Durably enqueue a pipeline-v2 job without reserving a worker early.

        Worker capacity is assigned only when a healthy logical slot claims the
        queued row. This keeps requests safe while the worker host is stopped and
        removes the slot double-allocation race from API admission.
        """
        if request.pipeline_version != 2:
            raise InvalidJobTransitionError('durable admission requires pipeline v2')
        if tasks:
            raise InvalidJobTransitionError('pipeline v2 does not accept durable task rows')
        if lease_seconds is not None and lease_seconds < 10:
            raise ValueError('lease_seconds must be at least 10')

        current = self.delegate._resolve_now(now)
        timestamp = _timestamp(current)
        job_id = str(uuid.uuid4())
        stage_specs = tuple(stages or ())

        with self.delegate._transaction() as connection:
            if request.owner_id is not None:
                self.delegate._lock_idempotency(
                    connection, request.owner_id, request.idempotency_key_hash or ''
                )
                existing = connection.execute(
                    """
                    SELECT * FROM crawl_jobs
                    WHERE owner_id = ? AND idempotency_key_hash = ?
                    """,
                    (request.owner_id, request.idempotency_key_hash),
                ).fetchone()
                if existing is not None:
                    if existing['request_fingerprint'] != request.request_fingerprint:
                        raise IdempotencyConflictError(
                            'idempotency key was already used for another request'
                        )
                    return self.delegate._get_job(connection, existing['job_id'])

            self.delegate._lock_account(connection, request.account_key)
            active = connection.execute(
                """
                SELECT job_id FROM crawl_jobs
                WHERE account_key = ?
                  AND status IN ('queued', 'waiting_account', 'running', 'cancelling')
                ORDER BY created_at
                LIMIT 1
                """,
                (request.account_key,),
            ).fetchone()
            if active is not None:
                raise AccountBusyError(str(active['job_id']))

            self._sync_slots(connection, timestamp)
            # Serialize queue-capacity accounting and queue_order allocation
            # across concurrent API requests for different accounts.
            if getattr(connection, '_connection', None) is not None:
                connection.execute(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext('mia:job-admission-capacity'))"
                )
            total_slots = int(connection.execute(
                """SELECT COUNT(*) AS value FROM worker_slots
                   WHERE enabled = 1 AND status <> 'disabled'"""
            ).fetchone()['value'])
            available_slots = int(connection.execute(
                """SELECT COUNT(*) AS value FROM worker_slots
                   WHERE enabled = 1 AND status = 'available'
                     AND current_job_id IS NULL"""
            ).fetchone()['value'])
            queued_jobs = int(connection.execute(
                """SELECT COUNT(*) AS value FROM crawl_jobs
                   WHERE pipeline_version = 2
                     AND status IN ('queued', 'waiting_account')"""
            ).fetchone()['value'])
            max_queued_jobs = _env_int(
                'MIA_MAX_QUEUED_JOBS', 100, minimum=1
            )
            if queued_jobs >= max_queued_jobs:
                raise CapacityExhaustedError(
                    available_slots=available_slots,
                    total_slots=total_slots,
                    retry_after_seconds=_env_int(
                        'MIA_CAPACITY_RETRY_AFTER_SECONDS', 15, minimum=1
                    ),
                )

            queue_row = connection.execute(
                'SELECT COALESCE(MAX(queue_order), 0) + 1 AS value FROM crawl_jobs'
            ).fetchone()
            queue_order = int(queue_row['value'])
            parameters = dict(request.parameters)
            for transient_key in (
                'worker_slot_id', 'route_type', 'proxy_id', 'fence_token'
            ):
                parameters.pop(transient_key, None)

            connection.execute(
                """
                INSERT INTO crawl_jobs (
                    job_id, account_key, company_tax_code, job_type, queue_order,
                    parameters_json, owner_id, idempotency_key_hash,
                    request_fingerprint, status, current_stage, worker_id,
                    lease_token, lease_generation, lease_started_at,
                    lease_expires_at, available_at, created_at, updated_at,
                    started_at, pipeline_version, stage_progress_percent,
                    progress_state, progress_updated_at, worker_claimed_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, NULL, 0,
                    NULL, NULL, ?, ?, ?, NULL, 2, 0, ?, ?, NULL
                )
                """,
                (
                    job_id, request.account_key, request.company_tax_code,
                    request.job_type, queue_order, _json_dump(parameters),
                    request.owner_id, request.idempotency_key_hash,
                    request.request_fingerprint, timestamp, timestamp, timestamp,
                    self.delegate._progress_value(initial_progress_state(
                        parameters.get('pipeline_plan')
                    )),
                    timestamp,
                ),
            )
            for sequence_number, stage in enumerate(stage_specs, start=1):
                connection.execute(
                    """
                    INSERT INTO crawl_job_stages (
                        stage_id, job_id, stage_name, sequence_number, status,
                        task_generation_completed, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), job_id, stage.stage_name,
                        sequence_number, int(stage.task_generation_completed),
                        timestamp, timestamp,
                    ),
                )
            return self.delegate._get_job(connection, job_id)

    def claim_next_job(self, worker_id: str, *, lease_seconds: int, now=None):
        """Claim the oldest queued job and bind it to this healthy worker slot."""
        if not worker_id.strip():
            raise ValueError('worker_id is required')
        if lease_seconds < 10:
            raise ValueError('lease_seconds must be at least 10')

        current = self.delegate._resolve_now(now)
        timestamp = _timestamp(current)
        expires_at = _timestamp(current + timedelta(seconds=lease_seconds))

        with self.delegate._transaction() as connection:
            # Backward-compatible recovery for jobs admitted by the previous
            # immediate-reservation implementation.
            row = connection.execute(
                """
                SELECT job_id FROM crawl_jobs
                WHERE status = 'running' AND worker_id = ?
                  AND worker_claimed_at IS NULL
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at > ?
                ORDER BY created_at, job_id
                LIMIT 1
                """,
                (worker_id, timestamp),
            ).fetchone()
            if row is not None:
                updated = connection.execute(
                    """
                    UPDATE crawl_jobs
                    SET worker_claimed_at = ?, lease_expires_at = ?, updated_at = ?
                    WHERE job_id = ? AND status = 'running'
                      AND worker_id = ? AND worker_claimed_at IS NULL
                    """,
                    (timestamp, expires_at, timestamp, row['job_id'], worker_id),
                ).rowcount
                if updated == 1:
                    connection.execute(
                        """UPDATE account_execution_leases
                           SET heartbeat_at = ?, lease_expires_at = ?
                           WHERE job_id = ? AND worker_slot_id = ?""",
                        (timestamp, expires_at, row['job_id'], worker_id),
                    )
                    connection.execute(
                        """UPDATE worker_slots
                           SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                           WHERE slot_id = ? AND current_job_id = ?""",
                        (timestamp, expires_at, timestamp, worker_id, row['job_id']),
                    )
                    return self.delegate._get_job(connection, row['job_id'])

            slot = self._select_worker_slot(connection, worker_id)
            if slot is None:
                return None
            queued = self.delegate._select_job_for_claim(connection, timestamp)
            if queued is None:
                return None

            job = self.delegate._get_job(connection, queued['job_id'])
            self.delegate._lock_account(connection, job.account_key)
            competing = connection.execute(
                """
                SELECT job_id FROM crawl_jobs
                WHERE account_key = ? AND job_id <> ?
                  AND status IN ('running', 'cancelling')
                LIMIT 1
                """,
                (job.account_key, job.job_id),
            ).fetchone()
            if competing is not None:
                connection.execute(
                    """UPDATE crawl_jobs SET status = 'waiting_account', updated_at = ?
                       WHERE job_id = ? AND status = 'queued'""",
                    (timestamp, job.job_id),
                )
                return None

            fence_row = connection.execute(
                'SELECT fence_token FROM account_execution_fences WHERE account_key = ?',
                (job.account_key,),
            ).fetchone()
            fence_token = int(fence_row['fence_token']) + 1 if fence_row else 1
            connection.execute(
                """
                INSERT INTO account_execution_fences (account_key, fence_token, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (account_key) DO UPDATE SET
                    fence_token = excluded.fence_token,
                    updated_at = excluded.updated_at
                """,
                (job.account_key, fence_token, timestamp),
            )

            lease_token = str(uuid.uuid4())
            parameters = dict(job.parameters)
            parameters.update({
                'worker_slot_id': str(slot['slot_id']),
                'route_type': str(slot['route_type']),
                'proxy_id': slot['proxy_id'],
                'fence_token': fence_token,
            })

            reserved = connection.execute(
                """
                UPDATE worker_slots
                SET status = 'busy', current_job_id = ?, heartbeat_at = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE slot_id = ? AND enabled = 1 AND status = 'available'
                  AND current_job_id IS NULL
                """,
                (
                    job.job_id, timestamp, expires_at, timestamp,
                    slot['slot_id'],
                ),
            ).rowcount
            if reserved != 1:
                return None

            claimed = connection.execute(
                """
                UPDATE crawl_jobs
                SET parameters_json = ?, status = 'running', current_stage = 'auth',
                    worker_id = ?, lease_token = ?,
                    lease_generation = lease_generation + 1,
                    lease_started_at = ?, lease_expires_at = ?,
                    started_at = COALESCE(started_at, ?), updated_at = ?,
                    worker_claimed_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (
                    _json_dump(parameters), slot['slot_id'], lease_token,
                    timestamp, expires_at, timestamp, timestamp, timestamp,
                    job.job_id,
                ),
            ).rowcount
            if claimed != 1:
                raise LeaseLostError(f'queued job claim lost: {job.job_id}')

            connection.execute(
                """DELETE FROM account_execution_leases
                   WHERE account_key = ? AND lease_expires_at <= ?""",
                (job.account_key, timestamp),
            )
            connection.execute(
                """
                INSERT INTO account_execution_leases (
                    account_key, job_id, worker_slot_id, fence_token,
                    lease_token, acquired_at, heartbeat_at, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.account_key, job.job_id, slot['slot_id'], fence_token,
                    lease_token, timestamp, timestamp, expires_at,
                ),
            )
            return self.delegate._get_job(connection, job.job_id)

    def renew_job_lease(
        self, job_id, worker_id, lease_token, *, lease_seconds, now=None
    ):
        record = self.delegate.renew_job_lease(
            job_id, worker_id, lease_token,
            lease_seconds=lease_seconds, now=now,
        )
        timestamp = _timestamp(self.delegate._resolve_now(now))
        expires_at = _timestamp(
            self.delegate._resolve_now(now) + timedelta(seconds=lease_seconds)
        )
        with self.delegate._transaction() as connection:
            connection.execute(
                """UPDATE account_execution_leases
                   SET heartbeat_at = ?, lease_expires_at = ?
                   WHERE job_id = ? AND worker_slot_id = ? AND lease_token = ?""",
                (timestamp, expires_at, job_id, worker_id, lease_token),
            )
            connection.execute(
                """UPDATE worker_slots SET heartbeat_at = ?, lease_expires_at = ?,
                       updated_at = ? WHERE slot_id = ? AND current_job_id = ?""",
                (timestamp, expires_at, timestamp, worker_id, job_id),
            )
        return record

    def finish_pipeline_job(self, *args, **kwargs):
        result = self.delegate.finish_pipeline_job(*args, **kwargs)
        self._release_result(result)
        return result

    def terminalize_pipeline_job(self, *args, **kwargs):
        result = self.delegate.terminalize_pipeline_job(*args, **kwargs)
        self._release_result(result)
        return result

    def requeue_pipeline_job(
        self, job_id, worker_id, lease_token, *, now=None
    ):
        timestamp = _timestamp(self.delegate._resolve_now(now))
        with self.delegate._transaction() as connection:
            job = self.delegate._get_job(connection, job_id)
            self.delegate._assert_job_lease(job, worker_id, lease_token)
            connection.execute(
                """
                UPDATE crawl_jobs
                SET status = 'abandoned', current_stage = NULL,
                    worker_id = NULL, lease_token = NULL,
                    lease_started_at = NULL, lease_expires_at = NULL,
                    finished_at = ?, updated_at = ?,
                    last_error_code = 'worker_restarted',
                    last_error_message = 'Worker stopped; create a new job'
                WHERE job_id = ? AND worker_id = ? AND lease_token = ?
                  AND status IN ('running', 'cancelling')
                """,
                (timestamp, timestamp, job_id, worker_id, lease_token),
            )
            self._release_resources(connection, job, timestamp)
            return self.delegate._get_job(connection, job_id)

    def recover_expired_leases(self, *, now=None):
        current = self.delegate._resolve_now(now)
        timestamp = _timestamp(current)
        with self.delegate._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM crawl_jobs
                WHERE status IN ('running', 'cancelling')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                  AND parameters_json LIKE ?
                """,
                (timestamp, '%worker_slot_id%'),
            ).fetchall()
            for row in rows:
                job = self.delegate._get_job(connection, row['job_id'])
                connection.execute(
                    """
                    UPDATE crawl_jobs
                    SET status = 'abandoned', current_stage = NULL,
                        worker_id = NULL, lease_token = NULL,
                        lease_started_at = NULL, lease_expires_at = NULL,
                        finished_at = ?, updated_at = ?,
                        last_error_code = 'worker_lease_expired',
                        last_error_message = 'Worker lease expired; create a new job'
                    WHERE job_id = ? AND status IN ('running', 'cancelling')
                    """,
                    (timestamp, timestamp, job.job_id),
                )
                self._release_resources(connection, job, timestamp)
        return self.delegate.recover_expired_leases(now=now)

    def _release_result(self, result) -> None:
        timestamp = _timestamp(self.delegate._resolve_now(None))
        with self.delegate._transaction() as connection:
            self._release_resources(connection, result, timestamp)

    @staticmethod
    def _release_resources(connection, job, timestamp: str) -> None:
        slot_id = job.parameters.get('worker_slot_id')
        fence_token = job.parameters.get('fence_token')
        if slot_id:
            connection.execute(
                """
                UPDATE worker_slots
                SET status = CASE WHEN enabled = 1 THEN 'available' ELSE 'disabled' END,
                    current_job_id = NULL, heartbeat_at = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE slot_id = ? AND current_job_id = ?
                """,
                (timestamp, slot_id, job.job_id),
            )
        connection.execute(
            """
            DELETE FROM account_execution_leases
            WHERE account_key = ? AND job_id = ? AND fence_token = ?
            """,
            (job.account_key, job.job_id, int(fence_token or 0)),
        )

    def _sync_slots(self, connection, timestamp: str) -> None:
        max_slots = _env_int('MIA_MAX_WORKER_SLOTS', 4, minimum=1)
        base_direct = _env_int('MIA_BASE_DIRECT_WORKER_SLOTS', 1, minimum=1)
        per_proxy = _env_int('MIA_WORKER_PER_HEALTHY_PROXY', 1, minimum=1)
        desired: list[tuple[str, str, str | None]] = []
        for index in range(min(base_direct, max_slots)):
            suffix = '' if index == 0 else f'-{index + 1}'
            desired.append((f'slot-direct{suffix}', 'direct', None))
        for proxy in parse_proxy_list(os.getenv('MIA_WORKER_PROXIES')):
            proxy_id = 'proxy-' + hashlib.sha256(proxy.encode()).hexdigest()[:12]
            for index in range(per_proxy):
                suffix = '' if index == 0 else f'-{index + 1}'
                desired.append((f'slot-{proxy_id}{suffix}', 'proxy', proxy_id))
                if len(desired) >= max_slots:
                    break
            if len(desired) >= max_slots:
                break
        desired_ids = {item[0] for item in desired}
        for slot_id, route_type, proxy_id in desired:
            connection.execute(
                """
                INSERT INTO worker_slots (
                    slot_id, route_type, proxy_id, status, enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'available', 1, ?, ?)
                ON CONFLICT (slot_id) DO UPDATE SET
                    route_type = excluded.route_type,
                    proxy_id = excluded.proxy_id,
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (slot_id, route_type, proxy_id, timestamp, timestamp),
            )
        rows = connection.execute(
            'SELECT slot_id FROM worker_slots WHERE enabled = 1'
        ).fetchall()
        for row in rows:
            if row['slot_id'] not in desired_ids:
                connection.execute(
                    """UPDATE worker_slots SET enabled = 0,
                       status = CASE WHEN current_job_id IS NULL
                                     THEN 'disabled' ELSE status END,
                       updated_at = ? WHERE slot_id = ?""",
                    (timestamp, row['slot_id']),
                )

    @staticmethod
    def _select_worker_slot(connection, worker_id: str):
        sql = """
            SELECT slot_id, route_type, proxy_id FROM worker_slots
            WHERE slot_id = ? AND enabled = 1 AND status = 'available'
              AND current_job_id IS NULL
        """
        if getattr(connection, '_connection', None) is not None:
            sql += ' FOR UPDATE SKIP LOCKED'
        return connection.execute(sql, (worker_id,)).fetchone()

    @staticmethod
    def _ensure_worker_claimed_column(connection) -> None:
        if getattr(connection, '_connection', None) is not None:
            connection.execute(
                'ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS '
                'worker_claimed_at TIMESTAMPTZ'
            )
            return
        columns = connection.execute('PRAGMA table_info(crawl_jobs)').fetchall()
        if any(str(row['name']) == 'worker_claimed_at' for row in columns):
            return
        connection.execute(
            'ALTER TABLE crawl_jobs ADD COLUMN worker_claimed_at TEXT'
        )


def _env_int(name: str, default: int, *, minimum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f'{name} must be at least {minimum}')
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError('timestamps must be timezone-aware')
    return value.astimezone(timezone.utc).isoformat()


def _execute_script(connection, script: str) -> None:
    raw = getattr(connection, '_connection', None)
    if raw is None:
        connection.executescript(script)
        return
    for statement in script.split(';'):
        if statement.strip():
            connection.execute(statement)


def _migration_sql(connection) -> str:
    return (
        """INSERT INTO control_schema_migrations (version, name, applied_at)
           VALUES (8, 'immediate_admission_worker_slots', CURRENT_TIMESTAMP)
           ON CONFLICT (version) DO NOTHING"""
        if getattr(connection, '_connection', None) is not None
        else """INSERT OR IGNORE INTO control_schema_migrations
                (version, name, applied_at) VALUES (8, ?, ?)"""
    )


def _migration_parameters(connection):
    if getattr(connection, '_connection', None) is not None:
        return ()
    return ('immediate_admission_worker_slots', _timestamp(datetime.now(timezone.utc)))


_ADMISSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_slots (
    slot_id TEXT PRIMARY KEY,
    route_type TEXT NOT NULL,
    proxy_id TEXT,
    status TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    current_job_id TEXT,
    heartbeat_at TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_execution_fences (
    account_key TEXT PRIMARY KEY,
    fence_token BIGINT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_execution_leases (
    account_key TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    worker_slot_id TEXT NOT NULL,
    fence_token BIGINT NOT NULL,
    lease_token TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worker_slots_available
ON worker_slots (enabled, status, slot_id);
CREATE INDEX IF NOT EXISTS idx_execution_leases_expiry
ON account_execution_leases (lease_expires_at);
"""