from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Sequence

from app.job_engine.models import (
    CreateJobRequest,
    IdempotencyConflictError,
    InvalidJobTransitionError,
    JobNotFoundError,
    JobRecord,
    JobStageSpec,
    JobTaskSpec,
    LeaseLostError,
    RecoveryReport,
    StageRecord,
    TaskInsertResult,
    TaskRecord,
)
from app.job_engine.progress import (
    ProgressSnapshot, initial_progress_state, validate_progress_transition,
)


class _RelationalJobEngineRepository:
    """Shared relational state policy; concrete adapters own locking/connections."""

    def __init__(self, database_path: Path | str, *, busy_timeout_seconds: int = 30) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.busy_timeout_seconds = busy_timeout_seconds

    @staticmethod
    def _progress_value(value):
        return _json_dump(value)

    def migrate(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute('PRAGMA journal_mode = WAL')
            connection.executescript(_SQLITE_SCHEMA)
            self._ensure_column(connection, 'crawl_jobs', 'lease_token', 'TEXT')
            self._ensure_column(
                connection, 'crawl_jobs', 'lease_generation',
                'INTEGER NOT NULL DEFAULT 0',
            )
            self._ensure_column(
                connection, 'crawl_jobs', 'warning_count',
                'INTEGER NOT NULL DEFAULT 0',
            )
            self._ensure_column(
                connection, 'crawl_jobs', 'queue_order',
                'INTEGER NOT NULL DEFAULT 0',
            )
            self._ensure_column(connection, 'crawl_jobs', 'owner_id', 'TEXT')
            self._ensure_column(connection, 'crawl_jobs', 'idempotency_key_hash', 'TEXT')
            self._ensure_column(connection, 'crawl_jobs', 'request_fingerprint', 'TEXT')
            self._ensure_progress_columns(connection)
            self._ensure_pipeline_columns(connection)
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_crawl_jobs_owner_idempotency
                ON crawl_jobs (owner_id, idempotency_key_hash)
                WHERE owner_id IS NOT NULL AND idempotency_key_hash IS NOT NULL
                """
            )
            self._ensure_column(
                connection, 'crawl_job_stages', 'task_generation_completed',
                'INTEGER NOT NULL DEFAULT 1',
            )
            self._ensure_column(
                connection, 'crawl_job_tasks', 'failure_policy',
                "TEXT NOT NULL DEFAULT 'fail_job'",
            )
            self._ensure_column(connection, 'crawl_job_tasks', 'lease_token', 'TEXT')
            self._ensure_column(
                connection, 'crawl_job_tasks', 'lease_generation',
                'INTEGER NOT NULL DEFAULT 0',
            )
            timestamp = _timestamp(_utc_now())
            connection.execute(
                """
                INSERT OR IGNORE INTO control_schema_migrations (version, name, applied_at)
                VALUES (1, 'phase_02_job_engine', ?)
                """,
                (timestamp,),
            )
            connection.execute(
                """INSERT OR IGNORE INTO control_schema_migrations
                   (version, name, applied_at)
                   VALUES (6, 'phase_08_taskless_job_pipeline', ?)""",
                (timestamp,),
            )
            connection.execute('DROP TABLE IF EXISTS webhook_outbox')
            connection.execute('DROP TABLE IF EXISTS job_progress_snapshots')
            connection.execute(
                """INSERT OR IGNORE INTO control_schema_migrations
                   (version, name, applied_at)
                   VALUES (5, 'phase_06_job_polling_progress', ?)""",
                (timestamp,),
            )
            connection.execute(
                """UPDATE control_schema_migrations
                   SET name = 'phase_06_job_polling_progress' WHERE version = 5"""
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO control_schema_migrations (version, name, applied_at)
                VALUES (2, 'phase_02_job_engine_hardening', ?)
                """,
                (timestamp,),
            )

    @staticmethod
    def _ensure_progress_columns(connection: sqlite3.Connection) -> None:
        columns = (
            ('progress_percent', 'REAL NOT NULL DEFAULT 0'),
            ('total_tasks', 'INTEGER NOT NULL DEFAULT 0'),
            ('pending_tasks', 'INTEGER NOT NULL DEFAULT 0'),
            ('running_tasks', 'INTEGER NOT NULL DEFAULT 0'),
            ('succeeded_tasks', 'INTEGER NOT NULL DEFAULT 0'),
            ('failed_tasks', 'INTEGER NOT NULL DEFAULT 0'),
            ('skipped_tasks', 'INTEGER NOT NULL DEFAULT 0'),
            ('progress_message', 'TEXT'),
        )
        for name, declaration in columns:
            _RelationalJobEngineRepository._ensure_column(
                connection, 'crawl_jobs', name, declaration
            )

    @staticmethod
    def _ensure_pipeline_columns(connection: sqlite3.Connection) -> None:
        for name, declaration in (
            ('pipeline_version', 'INTEGER NOT NULL DEFAULT 1'),
            ('stage_progress_percent', 'REAL NOT NULL DEFAULT 0'),
            ('progress_state', 'TEXT'),
            ('progress_updated_at', 'TEXT'),
        ):
            _RelationalJobEngineRepository._ensure_column(
                connection, 'crawl_jobs', name, declaration
            )

    def create_job(
        self,
        request: CreateJobRequest,
        tasks: Sequence[JobTaskSpec],
        *,
        stages: Sequence[JobStageSpec] | None = None,
        now: datetime | None = None,
    ) -> JobRecord:
        if request.pipeline_version != 2:
            raise InvalidJobTransitionError(
                'pipeline v1 is maintenance-only and cannot create new jobs'
            )
        if tasks:
            raise InvalidJobTransitionError(
                'pipeline v2 does not accept durable task rows'
            )
        task_keys = [task.task_key for task in tasks]
        if len(task_keys) != len(set(task_keys)):
            raise ValueError('task_key must be unique within a job')
        stage_specs = _resolve_stage_specs(tasks, stages)
        stage_names = {stage.stage_name for stage in stage_specs}
        missing = {task.stage_name for task in tasks} - stage_names
        if missing:
            raise ValueError(f'task references undeclared stage: {sorted(missing)[0]}')

        timestamp = _timestamp(self._resolve_now(now))
        job_id = str(uuid.uuid4())
        with self._transaction() as connection:
            if request.owner_id is not None:
                self._lock_idempotency(
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
                    return _job_from_row(existing)
            self._lock_account(connection, request.account_key)
            queue_row = connection.execute(
                """
                SELECT COALESCE(MAX(queue_order), 0) + 1 AS value
                FROM crawl_jobs WHERE account_key = ?
                """,
                (request.account_key,),
            ).fetchone()
            queue_order = int(queue_row['value'])
            active = connection.execute(
                """
                SELECT 1 FROM crawl_jobs
                WHERE account_key = ?
                  AND status IN ('queued', 'running', 'cancelling')
                LIMIT 1
                """,
                (request.account_key,),
            ).fetchone()
            status = 'waiting_account' if active else 'queued'
            connection.execute(
                """
                INSERT INTO crawl_jobs (
                    job_id, account_key, company_tax_code, job_type, queue_order,
                    parameters_json, owner_id, idempotency_key_hash, request_fingerprint,
                    status, current_stage, available_at, created_at, updated_at,
                    pipeline_version, stage_progress_percent, progress_state,
                    progress_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    job_id, request.account_key, request.company_tax_code,
                    request.job_type, queue_order, _json_dump(request.parameters),
                    request.owner_id, request.idempotency_key_hash,
                    request.request_fingerprint, status,
                    None if request.pipeline_version == 2 else stage_specs[0].stage_name,
                    timestamp, timestamp, timestamp, request.pipeline_version,
                    self._progress_value(initial_progress_state(
                        request.parameters.get('pipeline_plan')
                    ))
                    if request.pipeline_version == 2 else None,
                    timestamp if request.pipeline_version == 2 else None,
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
                        str(uuid.uuid4()), job_id, stage.stage_name, sequence_number,
                        int(stage.task_generation_completed), timestamp, timestamp,
                    ),
                )
            return self._get_job(connection, job_id)

    def ping(self) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute('SELECT 1 AS value').fetchone()
            return bool(row and row['value'] == 1)

    def terminalize_legacy_jobs(self, *, now=None) -> int:
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE crawl_jobs
                   SET status = 'cancelled', current_stage = NULL,
                       worker_id = NULL, lease_token = NULL,
                       lease_started_at = NULL, lease_expires_at = NULL,
                       finished_at = ?, updated_at = ?,
                       last_error_code = 'pipeline_v2_maintenance',
                       last_error_message = 'Job stopped during pipeline v2 activation'
                   WHERE pipeline_version = 1
                     AND status IN (
                         'waiting_account', 'queued', 'running', 'cancelling'
                     )""",
                (timestamp, timestamp),
            )
            return cursor.rowcount

    def count_nonterminal_pipeline_jobs(self, pipeline_version: int) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS amount FROM crawl_jobs
                   WHERE pipeline_version = ?
                     AND status IN (
                         'waiting_account', 'queued', 'running', 'cancelling'
                     )""",
                (pipeline_version,),
            ).fetchone()
            return int(row['amount'])

    def terminalize_pipeline_jobs_for_rollback(self, *, now=None) -> int:
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE crawl_jobs
                   SET status = 'cancelled', current_stage = NULL,
                       worker_id = NULL, lease_token = NULL,
                       lease_started_at = NULL, lease_expires_at = NULL,
                       finished_at = ?, updated_at = ?,
                       last_error_code = 'pipeline_v1_rollback',
                       last_error_message = 'Job stopped by confirmed rollback'
                   WHERE pipeline_version = 2
                     AND status IN (
                         'waiting_account', 'queued', 'running', 'cancelling'
                     )""",
                (timestamp, timestamp),
            )
            return cursor.rowcount

    def add_tasks(
        self,
        job_id: str,
        stage_name: str,
        tasks: Sequence[JobTaskSpec],
        *,
        generation_completed: bool = False,
        now: datetime | None = None,
    ) -> TaskInsertResult:
        if any(task.stage_name != stage_name for task in tasks):
            raise ValueError('all dynamic tasks must target the requested stage')
        task_keys = [task.task_key for task in tasks]
        if len(task_keys) != len(set(task_keys)):
            raise ValueError('dynamic task_key values must be unique in one request')
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            self._lock_job(connection, job_id)
            job = self._get_job(connection, job_id)
            if job.status in ('cancelled', 'completed', 'completed_with_warning', 'failed'):
                raise InvalidJobTransitionError(
                    f'cannot add tasks to terminal job: {job_id}'
                )
            stage = connection.execute(
                """
                SELECT task_generation_completed FROM crawl_job_stages
                WHERE job_id = ? AND stage_name = ?
                """,
                (job_id, stage_name),
            ).fetchone()
            if stage is None:
                raise InvalidJobTransitionError(f'job stage not found: {stage_name}')
            if stage['task_generation_completed']:
                existing = self._existing_task_keys(connection, job_id, task_keys)
                if existing != set(task_keys):
                    raise InvalidJobTransitionError(
                        f'task generation is already completed for stage: {stage_name}'
                    )
                return TaskInsertResult(
                    job_id, stage_name, len(tasks), 0, True
                )

            inserted = self._insert_tasks(connection, job_id, tasks, timestamp)
            if generation_completed:
                connection.execute(
                    """
                    UPDATE crawl_job_stages
                    SET task_generation_completed = 1, updated_at = ?
                    WHERE job_id = ? AND stage_name = ?
                    """,
                    (timestamp, job_id, stage_name),
                )
            connection.execute(
                """
                UPDATE crawl_jobs SET available_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (timestamp, timestamp, job_id),
            )
            self._recompute_state(connection, job_id, timestamp, finalize_job=False)
            return TaskInsertResult(
                job_id, stage_name, len(tasks), inserted, generation_completed
            )

    def get_job(self, job_id: str) -> JobRecord:
        with closing(self._connect()) as connection:
            return self._get_job(connection, job_id)

    def get_tasks(self, job_id: str) -> list[TaskRecord]:
        with closing(self._connect()) as connection:
            self._get_job(connection, job_id)
            return [_task_from_row(row) for row in connection.execute(
                """
                SELECT * FROM crawl_job_tasks
                WHERE job_id = ? ORDER BY sequence_number
                """,
                (job_id,),
            ).fetchall()]

    def get_stages(self, job_id: str) -> list[StageRecord]:
        with closing(self._connect()) as connection:
            self._get_job(connection, job_id)
            return [_stage_from_row(row) for row in connection.execute(
                """
                SELECT * FROM crawl_job_stages
                WHERE job_id = ? ORDER BY sequence_number
                """,
                (job_id,),
            ).fetchall()]

    def list_jobs_for_reconciliation(self, *, limit: int = 1000) -> list[JobRecord]:
        """Return invoice jobs whose durable artifacts may need repair.

        Failed/cancelled jobs are deliberately excluded: reconciliation must not
        resurrect an explicitly failed or cancelled business operation.
        """
        if limit < 1:
            raise ValueError('reconciliation limit must be positive')
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM crawl_jobs
                WHERE job_type = 'invoice_crawl'
                  AND status IN (
                      'queued', 'waiting_account', 'running',
                      'completed', 'completed_with_warning'
                  )
                ORDER BY created_at, job_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def complete_task_from_reconciliation(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> TaskRecord:
        """Acknowledge persisted business output without a worker lease.

        Only unleased retryable states are eligible. A currently leased task is
        fenced from startup reconciliation and remains owned by its worker.
        """
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            task = self._get_task(connection, task_id)
            self._lock_job(connection, task.job_id)
            job = self._get_job(connection, task.job_id)
            if task.status == 'completed':
                return task
            if task.status not in ('pending', 'retry_wait'):
                raise InvalidJobTransitionError(
                    f'cannot reconcile task completion from {task.status}: {task_id}'
                )
            if job.status in ('failed', 'cancelled'):
                raise InvalidJobTransitionError(
                    f'cannot reconcile task for terminal job: {job.job_id}'
                )
            connection.execute(
                """
                UPDATE crawl_job_tasks
                SET status = 'completed', worker_id = NULL, lease_token = NULL,
                    lease_started_at = NULL, lease_expires_at = NULL,
                    next_retry_at = NULL, last_error_code = NULL,
                    last_error_message = NULL, completed_at = ?, updated_at = ?
                WHERE task_id = ? AND status IN ('pending', 'retry_wait')
                """,
                (timestamp, timestamp, task_id),
            )
            self._recompute_state(
                connection,
                task.job_id,
                timestamp,
                finalize_job=job.status == 'queued',
            )
            reconciled_job = self._get_job(connection, task.job_id)
            if reconciled_job.status in (
                'cancelled', 'completed', 'completed_with_warning', 'failed'
            ):
                self._promote_waiting_jobs(connection, timestamp)
            return self._get_task(connection, task_id)

    def requeue_completed_task(
        self,
        task_id: str,
        *,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> TaskRecord:
        """Reopen a completed task whose durable artifact is missing/corrupt."""
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            task = self._get_task(connection, task_id)
            self._lock_job(connection, task.job_id)
            job = self._get_job(connection, task.job_id)
            if task.status != 'completed':
                return task
            if job.status in ('failed', 'cancelled', 'cancelling'):
                raise InvalidJobTransitionError(
                    f'cannot reopen task for {job.status} job: {job.job_id}'
                )
            if job.status == 'running' and job.lease_token:
                raise InvalidJobTransitionError(
                    f'cannot reopen task owned by a running worker: {job.job_id}'
                )

            connection.execute(
                """
                UPDATE crawl_job_tasks
                SET status = 'pending', worker_id = NULL, lease_token = NULL,
                    lease_started_at = NULL, lease_expires_at = NULL,
                    attempt_count = 0, next_retry_at = NULL,
                    last_error_code = ?, last_error_message = ?,
                    completed_at = NULL, updated_at = ?
                WHERE task_id = ? AND status = 'completed'
                """,
                (
                    _bounded(error_code, 200),
                    _bounded(error_message, 2000),
                    timestamp,
                    task_id,
                ),
            )

            if job.status in ('completed', 'completed_with_warning'):
                self._lock_account(connection, job.account_key)
                active = connection.execute(
                    """
                    SELECT 1 FROM crawl_jobs
                    WHERE account_key = ? AND job_id <> ?
                      AND status IN ('queued', 'running', 'cancelling')
                    LIMIT 1
                    """,
                    (job.account_key, job.job_id),
                ).fetchone()
                reopened_status = 'waiting_account' if active else 'queued'
                connection.execute(
                    """
                    UPDATE crawl_jobs
                    SET status = ?, current_stage = ?, worker_id = NULL,
                        lease_token = NULL, lease_started_at = NULL,
                        lease_expires_at = NULL, available_at = ?,
                        finished_at = NULL, last_error_code = ?,
                        last_error_message = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        reopened_status,
                        task.stage_name,
                        timestamp,
                        _bounded(error_code, 200),
                        _bounded(error_message, 2000),
                        timestamp,
                        job.job_id,
                    ),
                )
            self._recompute_state(
                connection, task.job_id, timestamp, finalize_job=False
            )
            return self._get_task(connection, task_id)

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> JobRecord | None:
        _validate_worker_and_lease(worker_id, lease_seconds)
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        lease_token = str(uuid.uuid4())
        expires_at = _timestamp(current + timedelta(seconds=lease_seconds))
        with self._transaction() as connection:
            row = self._select_job_for_claim(connection, timestamp)
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE crawl_jobs
                SET status = 'running', worker_id = ?, lease_token = ?,
                    lease_generation = lease_generation + 1,
                    lease_started_at = ?, lease_expires_at = ?,
                    started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (
                    worker_id, lease_token, timestamp, expires_at,
                    timestamp, timestamp, row['job_id'],
                ),
            ).rowcount
            if updated != 1:
                return None
            claimed = self._get_job(connection, row['job_id'])
            if claimed.pipeline_version == 1:
                self._recompute_state(
                    connection, row['job_id'], timestamp, finalize_job=False
                )
            else:
                connection.execute(
                    """UPDATE crawl_jobs
                       SET current_stage = COALESCE(current_stage, 'auth'), updated_at = ?
                       WHERE job_id = ?""",
                    (timestamp, row['job_id']),
                )
            return self._get_job(connection, row['job_id'])

    def renew_job_lease(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> JobRecord:
        _validate_worker_and_lease(worker_id, lease_seconds)
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        expires_at = _timestamp(current + timedelta(seconds=lease_seconds))
        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE crawl_jobs SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND worker_id = ? AND lease_token = ?
                  AND status IN ('running', 'cancelling')
                """,
                (expires_at, timestamp, job_id, worker_id, lease_token),
            ).rowcount
            if updated != 1:
                raise LeaseLostError(f'job lease lost: {job_id}')
            return self._get_job(connection, job_id)

    def persist_pipeline_progress(
        self, job_id: str, worker_id: str, lease_token: str, state: dict,
        *, now: datetime | None = None,
    ) -> JobRecord:
        timestamp = _timestamp(self._resolve_now(now))
        snapshot = ProgressSnapshot.from_state(state)
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            self._assert_job_lease(job, worker_id, lease_token)
            if job.pipeline_version != 2:
                raise InvalidJobTransitionError('job is not a taskless pipeline')
            validate_progress_transition(job.progress_state, state)
            progress = max(job.progress_percent, float(snapshot.overall_percent))
            updated = connection.execute(
                """UPDATE crawl_jobs
                   SET current_stage = ?, progress_percent = ?,
                       stage_progress_percent = ?, progress_state = ?,
                       progress_message = ?, progress_updated_at = ?, updated_at = ?
                   WHERE job_id = ? AND worker_id = ? AND lease_token = ?
                     AND status IN ('running', 'cancelling')""",
                (
                    state.get('current_stage'), progress,
                    float(snapshot.stage_percent), self._progress_value(state),
                    state.get('message'), timestamp, timestamp,
                    job_id, worker_id, lease_token,
                ),
            ).rowcount
            if updated != 1:
                raise LeaseLostError(f'job lease lost: {job_id}')
            return self._get_job(connection, job_id)

    def finish_pipeline_job(
        self, job_id: str, worker_id: str, lease_token: str, state: dict,
        *, warning_count: int = 0, now: datetime | None = None,
    ) -> JobRecord:
        timestamp = _timestamp(self._resolve_now(now))
        final_status = 'completed_with_warning' if warning_count else 'completed'
        state['current_stage'] = None
        state['current_unit'] = None
        state['message'] = 'completed_with_warning' if warning_count else 'completed'
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            self._assert_job_lease(job, worker_id, lease_token)
            updated = connection.execute(
                """UPDATE crawl_jobs
                   SET status = ?, current_stage = NULL, progress_percent = 100,
                       stage_progress_percent = 100, progress_state = ?,
                       progress_message = ?, progress_updated_at = ?,
                       warning_count = ?, worker_id = NULL, lease_token = NULL,
                       lease_started_at = NULL, lease_expires_at = NULL,
                       finished_at = ?, updated_at = ?
                   WHERE job_id = ? AND worker_id = ? AND lease_token = ?
                     AND status = 'running' AND pipeline_version = 2""",
                (
                    final_status, self._progress_value(state), state['message'],
                    timestamp, warning_count, timestamp, timestamp,
                    job_id, worker_id, lease_token,
                ),
            ).rowcount
            if updated != 1:
                raise LeaseLostError(f'job lease lost: {job_id}')
            self._promote_waiting_jobs(connection, timestamp)
            return self._get_job(connection, job_id)

    def terminalize_pipeline_job(
        self, job_id: str, worker_id: str, lease_token: str, *,
        status: str, error_code: str | None = None,
        error_message: str | None = None, now: datetime | None = None,
    ) -> JobRecord:
        if status not in ('failed', 'cancelled'):
            raise ValueError('pipeline terminal status must be failed or cancelled')
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            self._assert_job_lease(job, worker_id, lease_token)
            updated = connection.execute(
                """UPDATE crawl_jobs
                   SET status = ?, current_stage = NULL, worker_id = NULL,
                       lease_token = NULL, lease_started_at = NULL,
                       lease_expires_at = NULL, finished_at = ?, updated_at = ?,
                       last_error_code = ?, last_error_message = ?
                   WHERE job_id = ? AND worker_id = ? AND lease_token = ?
                     AND status IN ('running', 'cancelling')
                     AND pipeline_version = 2""",
                (
                    status, timestamp, timestamp, error_code, error_message,
                    job_id, worker_id, lease_token,
                ),
            ).rowcount
            if updated != 1:
                raise LeaseLostError(f'job lease lost: {job_id}')
            self._promote_waiting_jobs(connection, timestamp)
            return self._get_job(connection, job_id)

    def requeue_pipeline_job(
        self, job_id: str, worker_id: str, lease_token: str, *,
        now: datetime | None = None,
    ) -> JobRecord:
        """Relinquish a pipeline-v2 lease without discarding durable progress."""
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            self._assert_job_lease(job, worker_id, lease_token)
            if job.pipeline_version != 2:
                raise InvalidJobTransitionError('job is not a taskless pipeline')
            if job.status == 'cancelling':
                self._terminalize_pipeline_connection(
                    connection, job_id, timestamp, status='cancelled'
                )
                self._promote_waiting_jobs(connection, timestamp)
                return self._get_job(connection, job_id)
            updated = self._requeue_pipeline_connection(
                connection, job_id, timestamp,
                worker_id=worker_id, lease_token=lease_token,
            )
            if updated != 1:
                raise LeaseLostError(f'job lease lost: {job_id}')
            return self._get_job(connection, job_id)

    def claim_task_batch(
        self,
        job_id: str,
        worker_id: str,
        job_lease_token: str,
        *,
        limit: int,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> list[TaskRecord]:
        _validate_worker_and_lease(worker_id, lease_seconds)
        if limit < 1:
            raise ValueError('task batch limit must be at least 1')
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        expires_at = _timestamp(current + timedelta(seconds=lease_seconds))
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            if (
                job.status != 'running'
                or job.worker_id != worker_id
                or job.lease_token != job_lease_token
            ):
                raise LeaseLostError(f'job lease lost: {job_id}')
            connection.execute(
                """
                UPDATE crawl_job_tasks
                SET status = 'pending', next_retry_at = NULL, updated_at = ?
                WHERE job_id = ? AND status = 'retry_wait' AND next_retry_at <= ?
                """,
                (timestamp, job_id, timestamp),
            )
            self._recompute_state(connection, job_id, timestamp, finalize_job=False)
            job = self._get_job(connection, job_id)
            if job.current_stage is None:
                return []
            rows = self._select_tasks_for_claim(
                connection, job_id, job.current_stage, limit
            )
            claimed: list[TaskRecord] = []
            for row in rows:
                task_token = str(uuid.uuid4())
                updated = connection.execute(
                    """
                    UPDATE crawl_job_tasks
                    SET status = 'leased', worker_id = ?, lease_token = ?,
                        lease_generation = lease_generation + 1,
                        lease_started_at = ?, lease_expires_at = ?,
                        attempt_count = attempt_count + 1, updated_at = ?
                    WHERE task_id = ? AND status = 'pending'
                    """,
                    (
                        worker_id, task_token, timestamp, expires_at,
                        timestamp, row['task_id'],
                    ),
                ).rowcount
                if updated == 1:
                    claimed.append(self._get_task(connection, row['task_id']))
            self._recompute_state(connection, job_id, timestamp, finalize_job=False)
            return claimed

    def renew_task_lease(
        self,
        task_id: str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> TaskRecord:
        _validate_worker_and_lease(worker_id, lease_seconds)
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        expires_at = _timestamp(current + timedelta(seconds=lease_seconds))
        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE crawl_job_tasks SET lease_expires_at = ?, updated_at = ?
                WHERE task_id = ? AND worker_id = ? AND lease_token = ?
                  AND status = 'leased'
                """,
                (expires_at, timestamp, task_id, worker_id, lease_token),
            ).rowcount
            if updated != 1:
                raise LeaseLostError(f'task lease lost: {task_id}')
            return self._get_task(connection, task_id)

    def complete_task(
        self,
        task_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> TaskRecord:
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            updated = connection.execute(
                """
                UPDATE crawl_job_tasks
                SET status = 'completed', worker_id = NULL, lease_token = NULL,
                    lease_started_at = NULL, lease_expires_at = NULL,
                    next_retry_at = NULL, last_error_code = NULL,
                    last_error_message = NULL, completed_at = ?, updated_at = ?
                WHERE task_id = ? AND status = 'leased'
                  AND worker_id = ? AND lease_token = ?
                """,
                (timestamp, timestamp, task_id, worker_id, lease_token),
            ).rowcount
            if updated != 1:
                raise LeaseLostError(f'task lease lost: {task_id}')
            task = self._get_task(connection, task_id)
            self._recompute_state(connection, task.job_id, timestamp, finalize_job=False)
            return self._get_task(connection, task_id)

    def fail_task(
        self,
        task_id: str,
        worker_id: str,
        lease_token: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_delay_seconds: int,
        now: datetime | None = None,
    ) -> TaskRecord:
        if retry_delay_seconds < 0:
            raise ValueError('retry_delay_seconds must not be negative')
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        with self._transaction() as connection:
            task = self._get_task(connection, task_id)
            if (
                task.status != 'leased'
                or task.worker_id != worker_id
                or task.lease_token != lease_token
            ):
                raise LeaseLostError(f'task lease lost: {task_id}')
            should_retry = retryable and task.attempt_count < task.max_attempts
            status = 'retry_wait' if should_retry else 'failed_terminal'
            next_retry_at = (
                _timestamp(current + timedelta(seconds=retry_delay_seconds))
                if should_retry else None
            )
            updated = connection.execute(
                """
                UPDATE crawl_job_tasks
                SET status = ?, worker_id = NULL, lease_token = NULL,
                    lease_started_at = NULL, lease_expires_at = NULL,
                    next_retry_at = ?, last_error_code = ?,
                    last_error_message = ?, updated_at = ?
                WHERE task_id = ? AND status = 'leased'
                  AND worker_id = ? AND lease_token = ?
                """,
                (
                    status, next_retry_at, _bounded(error_code, 200),
                    _bounded(error_message, 2000), timestamp, task_id,
                    worker_id, lease_token,
                ),
            ).rowcount
            if updated != 1:
                raise LeaseLostError(f'task lease lost: {task_id}')
            self._recompute_state(connection, task.job_id, timestamp, finalize_job=False)
            return self._get_task(connection, task_id)

    def settle_job(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            self._assert_job_lease(job, worker_id, lease_token)
            if job.status == 'cancelling':
                self._cancel_job(connection, job_id, timestamp)
            else:
                self._recompute_state(connection, job_id, timestamp, finalize_job=True)
            result = self._get_job(connection, job_id)
            if result.status in ('cancelled', 'completed', 'completed_with_warning', 'failed'):
                self._promote_waiting_jobs(connection, timestamp)
            return self._get_job(connection, job_id)

    def release_job(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            self._assert_job_lease(job, worker_id, lease_token)
            if job.status != 'running':
                raise InvalidJobTransitionError(f'cannot release job in {job.status}')
            self._recompute_state(connection, job_id, timestamp, finalize_job=False)
            job = self._get_job(connection, job_id)
            pending = connection.execute(
                """
                SELECT 1 FROM crawl_job_tasks
                WHERE job_id = ? AND stage_name = ? AND status = 'pending' LIMIT 1
                """,
                (job_id, job.current_stage),
            ).fetchone()
            retry_row = connection.execute(
                """
                SELECT MIN(next_retry_at) AS next_retry_at FROM crawl_job_tasks
                WHERE job_id = ? AND stage_name = ? AND status = 'retry_wait'
                """,
                (job_id, job.current_stage),
            ).fetchone()
            if pending:
                available_at = timestamp
            elif retry_row and retry_row['next_retry_at']:
                available_at = retry_row['next_retry_at']
            else:
                available_at = _timestamp(current + timedelta(seconds=5))
            updated = connection.execute(
                """
                UPDATE crawl_jobs
                SET status = 'queued', worker_id = NULL, lease_token = NULL,
                    lease_started_at = NULL, lease_expires_at = NULL,
                    available_at = ?, updated_at = ?
                WHERE job_id = ? AND worker_id = ? AND lease_token = ?
                """,
                (available_at, timestamp, job_id, worker_id, lease_token),
            ).rowcount
            if updated != 1:
                raise LeaseLostError(f'job lease lost: {job_id}')
            return self._get_job(connection, job_id)

    def request_cancellation(
        self,
        job_id: str,
        *,
        now: datetime | None = None,
    ) -> JobRecord:
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            if job.status in ('cancelled', 'completed', 'completed_with_warning', 'failed'):
                return job
            if job.status in ('queued', 'waiting_account'):
                if job.pipeline_version == 2:
                    self._terminalize_pipeline_connection(
                        connection, job_id, timestamp, status='cancelled'
                    )
                else:
                    self._cancel_job(connection, job_id, timestamp)
                self._promote_waiting_jobs(connection, timestamp)
            elif job.status == 'running':
                connection.execute(
                    """
                    UPDATE crawl_jobs
                    SET status = 'cancelling', cancel_requested_at = ?, updated_at = ?
                    WHERE job_id = ? AND status = 'running'
                    """,
                    (timestamp, timestamp, job_id),
                )
            return self._get_job(connection, job_id)

    def recover_expired_leases(
        self,
        *,
        now: datetime | None = None,
    ) -> RecoveryReport:
        timestamp = _timestamp(self._resolve_now(now))
        recovered_jobs = cancelled_jobs = 0
        with self._transaction() as connection:
            expired_jobs = self._select_expired_jobs(connection, timestamp)
            for row in expired_jobs:
                job = self._get_job(connection, row['job_id'])
                if job.pipeline_version != 2:
                    continue
                if row['status'] == 'cancelling':
                    self._terminalize_pipeline_connection(
                        connection, row['job_id'], timestamp, status='cancelled'
                    )
                    cancelled_jobs += 1
                else:
                    recovered_jobs += self._requeue_pipeline_connection(
                        connection, row['job_id'], timestamp
                    )
            promoted = self._promote_waiting_jobs(connection, timestamp)
        return RecoveryReport(recovered_jobs, 0, 0, cancelled_jobs, promoted)

    def _insert_tasks(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        tasks: Sequence[JobTaskSpec],
        timestamp: str,
    ) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) AS value FROM crawl_job_tasks WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        next_sequence = int(row['value']) + 1
        inserted = 0
        for offset, task in enumerate(tasks):
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO crawl_job_tasks (
                    task_id, job_id, sequence_number, task_key, stage_name,
                    task_type, payload_json, status, failure_policy, max_attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), job_id, next_sequence + offset, task.task_key,
                    task.stage_name, task.task_type, _json_dump(task.payload),
                    task.failure_policy, task.max_attempts, timestamp, timestamp,
                ),
            )
            inserted += cursor.rowcount
        return inserted

    @staticmethod
    def _existing_task_keys(
        connection: sqlite3.Connection,
        job_id: str,
        task_keys: Sequence[str],
    ) -> set[str]:
        if not task_keys:
            return set()
        placeholders = ','.join('?' for _ in task_keys)
        return {row['task_key'] for row in connection.execute(
            f"SELECT task_key FROM crawl_job_tasks WHERE job_id = ? AND task_key IN ({placeholders})",
            (job_id, *task_keys),
        ).fetchall()}

    def terminate_owned_job(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> JobRecord:
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            job = self._get_job(connection, job_id)
            if job.status in ('cancelled', 'completed', 'completed_with_warning', 'failed'):
                return job
            if job.worker_id != worker_id or job.lease_token != lease_token:
                raise LeaseLostError(f'job lease lost: {job_id}')
            self._cancel_job(
                connection,
                job_id,
                timestamp,
                error_code=error_code,
                error_message=error_message,
            )
            self._promote_waiting_jobs(connection, timestamp)
            return self._get_job(connection, job_id)

    @staticmethod
    def _select_expired_jobs(connection, timestamp: str):
        return connection.execute(
            """
            SELECT job_id, status FROM crawl_jobs
            WHERE status IN ('running', 'cancelling')
              AND (
                  worker_id IS NULL OR lease_token IS NULL
                  OR lease_expires_at IS NULL OR lease_expires_at <= ?
              )
            ORDER BY created_at
            """,
            (timestamp,),
        ).fetchall()

    def _recompute_state(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        timestamp: str,
        *,
        finalize_job: bool,
    ) -> None:
        stages = connection.execute(
            """
            SELECT stage_name, task_generation_completed FROM crawl_job_stages
            WHERE job_id = ? ORDER BY sequence_number
            """,
            (job_id,),
        ).fetchall()
        fatal = False
        warning_count = 0
        current_stage: str | None = None
        all_terminal = True
        first_error: sqlite3.Row | None = None
        for stage in stages:
            counts = {row['bucket']: row['amount'] for row in connection.execute(
                """
                SELECT
                    CASE
                        WHEN status = 'failed_terminal' THEN failure_policy
                        ELSE status
                    END AS bucket,
                    COUNT(*) AS amount
                FROM crawl_job_tasks
                WHERE job_id = ? AND stage_name = ?
                GROUP BY bucket
                """,
                (job_id, stage['stage_name']),
            ).fetchall()}
            active = sum(counts.get(name, 0) for name in ('pending', 'leased', 'retry_wait'))
            stage_fatal = counts.get('fail_job', 0) > 0
            stage_warnings = counts.get('continue_with_warning', 0)
            warning_count += stage_warnings
            generation_done = bool(stage['task_generation_completed'])
            if stage_fatal:
                status = 'failed'
                fatal = True
                if first_error is None:
                    first_error = connection.execute(
                        """
                        SELECT last_error_code, last_error_message
                        FROM crawl_job_tasks
                        WHERE job_id = ? AND stage_name = ?
                          AND status = 'failed_terminal' AND failure_policy = 'fail_job'
                        ORDER BY updated_at, sequence_number LIMIT 1
                        """,
                        (job_id, stage['stage_name']),
                    ).fetchone()
            elif not generation_done or active:
                status = 'running' if counts.get('leased', 0) else 'pending'
                all_terminal = False
                if current_stage is None:
                    current_stage = stage['stage_name']
            elif stage_warnings:
                status = 'completed_with_warning'
            elif counts.get('cancelled', 0) and not counts.get('completed', 0):
                status = 'cancelled'
            else:
                status = 'completed'
            connection.execute(
                """
                UPDATE crawl_job_stages
                SET status = ?,
                    started_at = CASE WHEN ? = 'running'
                        THEN COALESCE(started_at, ?) ELSE started_at END,
                    finished_at = CASE
                        WHEN ? IN ('completed', 'completed_with_warning', 'failed', 'cancelled')
                        THEN COALESCE(finished_at, ?) ELSE NULL END,
                    last_error_code = CASE WHEN ? = 'failed' THEN ? ELSE NULL END,
                    last_error_message = CASE WHEN ? = 'failed' THEN ? ELSE NULL END,
                    updated_at = ?
                WHERE job_id = ? AND stage_name = ?
                """,
                (
                    status, status, timestamp, status, timestamp,
                    status, first_error['last_error_code'] if first_error else None,
                    status, first_error['last_error_message'] if first_error else None,
                    timestamp, job_id, stage['stage_name'],
                ),
            )

        connection.execute(
            """
            UPDATE crawl_jobs SET current_stage = ?, warning_count = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (current_stage, warning_count, timestamp, job_id),
        )
        if not finalize_job:
            self._update_progress(connection, job_id, timestamp)
            return
        if fatal:
            connection.execute(
                """
                UPDATE crawl_job_tasks
                SET status = 'cancelled', worker_id = NULL, lease_token = NULL,
                    lease_started_at = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND status IN ('pending', 'leased', 'retry_wait')
                """,
                (timestamp, job_id),
            )
            connection.execute(
                """
                UPDATE crawl_jobs
                SET status = 'failed', worker_id = NULL, lease_token = NULL,
                    lease_started_at = NULL, lease_expires_at = NULL,
                    finished_at = ?, updated_at = ?, current_stage = NULL,
                    last_error_code = ?, last_error_message = ?
                WHERE job_id = ?
                """,
                (
                    timestamp, timestamp,
                    first_error['last_error_code'] if first_error else 'task_failed',
                    first_error['last_error_message'] if first_error else 'Required task failed',
                    job_id,
                ),
            )
            self._update_progress(connection, job_id, timestamp)
            return
        if all_terminal and stages:
            final_status = 'completed_with_warning' if warning_count else 'completed'
            connection.execute(
                """
                UPDATE crawl_jobs
                SET status = ?, worker_id = NULL, lease_token = NULL,
                    lease_started_at = NULL, lease_expires_at = NULL,
                    finished_at = ?, updated_at = ?, current_stage = NULL
                WHERE job_id = ?
                """,
                (final_status, timestamp, timestamp, job_id),
            )
        self._update_progress(connection, job_id, timestamp)

    @staticmethod
    def _update_progress(connection, job_id: str, timestamp: str) -> None:
        counts = {row['status']: int(row['amount']) for row in connection.execute(
            """SELECT status, COUNT(*) AS amount FROM crawl_job_tasks
               WHERE job_id = ? GROUP BY status""",
            (job_id,),
        ).fetchall()}
        stages = connection.execute(
            """SELECT stage_name, status, sequence_number FROM crawl_job_stages
               WHERE job_id = ? ORDER BY sequence_number""",
            (job_id,),
        ).fetchall()
        job = connection.execute(
            'SELECT status, current_stage, progress_percent FROM crawl_jobs WHERE job_id = ?',
            (job_id,),
        ).fetchone()
        stage_count = len(stages)
        completed_stages = sum(
            1 for stage in stages
            if stage['status'] in ('completed', 'completed_with_warning')
        )

        stage_fraction = 0.0
        if job['current_stage'] and stage_count:
            current_counts = {row['status']: int(row['amount']) for row in connection.execute(
                """SELECT status, COUNT(*) AS amount FROM crawl_job_tasks
                   WHERE job_id = ? AND stage_name = ? GROUP BY status""",
                (job_id, job['current_stage']),
            ).fetchall()}
            current_total = sum(current_counts.values())
            current_terminal = sum(
                current_counts.get(name, 0)
                for name in ('completed', 'failed_terminal', 'cancelled')
            )
            if current_total:
                stage_fraction = current_terminal / current_total
        calculated = 100.0 * (completed_stages + stage_fraction) / stage_count if stage_count else 0.0
        if job['status'] in ('completed', 'completed_with_warning'):
            calculated = 100.0
        progress = round(max(float(job['progress_percent'] or 0), calculated), 2)
        pending = counts.get('pending', 0) + counts.get('retry_wait', 0)
        running = counts.get('leased', 0)
        failed = counts.get('failed_terminal', 0)
        skipped = counts.get('cancelled', 0)
        messages = {
            'queued': 'Đang chờ worker xử lý',
            'waiting_account': 'Đang chờ lượt xử lý tài khoản',
            'running': 'Đang xử lý job',
            'cancelling': 'Đang hủy job',
            'cancelled': 'Job đã bị hủy',
            'completed': 'Job đã hoàn thành',
            'completed_with_warning': 'Job hoàn thành với cảnh báo',
            'failed': 'Job xử lý thất bại',
        }
        message = messages.get(job['status'], 'Đang cập nhật trạng thái job')
        if job['status'] == 'running' and job['current_stage']:
            message = f"Đang xử lý giai đoạn {job['current_stage']}"
        connection.execute(
            """UPDATE crawl_jobs SET progress_percent = ?, total_tasks = ?,
                   pending_tasks = ?, running_tasks = ?, succeeded_tasks = ?,
                   failed_tasks = ?, skipped_tasks = ?, progress_message = ?,
                   updated_at = ? WHERE job_id = ?""",
            (
                progress, sum(counts.values()), pending, running,
                counts.get('completed', 0), failed, skipped, message,
                timestamp, job_id,
            ),
        )

    @staticmethod
    def _terminalize_pipeline_connection(
        connection, job_id: str, timestamp: str, *, status: str,
        error_code: str | None = None, error_message: str | None = None,
    ) -> None:
        connection.execute(
            """UPDATE crawl_jobs
               SET status = ?, current_stage = NULL, worker_id = NULL,
                   lease_token = NULL, lease_started_at = NULL,
                   lease_expires_at = NULL, finished_at = ?, updated_at = ?,
                   last_error_code = ?, last_error_message = ?
               WHERE job_id = ? AND pipeline_version = 2""",
            (status, timestamp, timestamp, error_code, error_message, job_id),
        )

    @staticmethod
    def _requeue_pipeline_connection(
        connection, job_id: str, timestamp: str, *,
        worker_id: str | None = None, lease_token: str | None = None,
    ) -> int:
        ownership = ''
        parameters: tuple = (timestamp, timestamp, job_id)
        if worker_id is not None and lease_token is not None:
            ownership = ' AND worker_id = ? AND lease_token = ?'
            parameters += (worker_id, lease_token)
        return connection.execute(
            """UPDATE crawl_jobs
               SET status = 'queued', worker_id = NULL, lease_token = NULL,
                   lease_started_at = NULL, lease_expires_at = NULL,
                   available_at = ?, finished_at = NULL,
                   last_error_code = NULL, last_error_message = NULL,
                   progress_message = 'Đang chờ worker tiếp tục', updated_at = ?
               WHERE job_id = ? AND status = 'running' AND pipeline_version = 2"""
            + ownership,
            parameters,
        ).rowcount

    def _cancel_job(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        timestamp: str,
        *,
        terminal_status: str = 'cancelled',
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        connection.execute(
            """
            UPDATE crawl_job_tasks
            SET status = 'cancelled', worker_id = NULL, lease_token = NULL,
                lease_started_at = NULL, lease_expires_at = NULL,
                next_retry_at = NULL,
                last_error_code = COALESCE(?, last_error_code),
                last_error_message = COALESCE(?, last_error_message),
                updated_at = ?
            WHERE job_id = ? AND status IN ('pending', 'leased', 'retry_wait')
            """,
            (error_code, error_message, timestamp, job_id),
        )
        connection.execute(
            """
            UPDATE crawl_job_stages
            SET status = CASE
                    WHEN status IN ('completed', 'completed_with_warning') THEN status
                    ELSE 'cancelled' END,
                finished_at = COALESCE(finished_at, ?), updated_at = ?
            WHERE job_id = ?
            """,
            (timestamp, timestamp, job_id),
        )
        connection.execute(
            """
            UPDATE crawl_jobs
            SET status = ?, worker_id = NULL, lease_token = NULL,
                lease_started_at = NULL, lease_expires_at = NULL,
                cancel_requested_at = COALESCE(cancel_requested_at, ?),
                finished_at = ?, updated_at = ?, current_stage = NULL,
                last_error_code = COALESCE(?, last_error_code),
                last_error_message = COALESCE(?, last_error_message)
            WHERE job_id = ?
            """,
            (
                terminal_status, timestamp, timestamp, timestamp,
                error_code, error_message, job_id,
            ),
        )
        self._update_progress(connection, job_id, timestamp)

    def _promote_waiting_jobs(
        self,
        connection: sqlite3.Connection,
        timestamp: str,
    ) -> int:
        promoted = 0
        while True:
            row = self._select_waiting_for_promotion(connection)
            if row is None:
                return promoted
            self._lock_account(connection, row['account_key'])
            updated = connection.execute(
                """
                UPDATE crawl_jobs SET status = 'queued', available_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'waiting_account'
                  AND NOT EXISTS (
                      SELECT 1 FROM crawl_jobs AS active
                      WHERE active.account_key = ?
                        AND active.status IN ('queued', 'running', 'cancelling')
                  )
                """,
                (timestamp, timestamp, row['job_id'], row['account_key']),
            ).rowcount
            promoted += updated

    @staticmethod
    def _assert_job_lease(job: JobRecord, worker_id: str, lease_token: str) -> None:
        if (
            job.status not in ('running', 'cancelling')
            or job.worker_id != worker_id
            or job.lease_token != lease_token
        ):
            raise LeaseLostError(f'job lease lost: {job.job_id}')

    def _get_job(self, connection: sqlite3.Connection, job_id: str) -> JobRecord:
        row = connection.execute(
            'SELECT * FROM crawl_jobs WHERE job_id = ?', (job_id,)
        ).fetchone()
        if row is None:
            raise JobNotFoundError(f'job not found: {job_id}')
        return _job_from_row(row)

    def _get_task(self, connection: sqlite3.Connection, task_id: str) -> TaskRecord:
        row = connection.execute(
            'SELECT * FROM crawl_job_tasks WHERE task_id = ?', (task_id,)
        ).fetchone()
        if row is None:
            raise JobNotFoundError(f'task not found: {task_id}')
        return _task_from_row(row)

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
        if column not in columns:
            connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute(f'PRAGMA busy_timeout = {self.busy_timeout_seconds * 1000}')
        return connection

    def _resolve_now(self, value: datetime | None) -> datetime:
        return _as_utc(value)

    @staticmethod
    def _select_job_for_claim(
        connection: sqlite3.Connection,
        timestamp: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT job_id FROM crawl_jobs
            WHERE status = 'queued' AND pipeline_version = 2 AND available_at <= ?
            ORDER BY created_at, job_id LIMIT 1
            """,
            (timestamp,),
        ).fetchone()

    @staticmethod
    def _select_tasks_for_claim(
        connection: sqlite3.Connection,
        job_id: str,
        stage_name: str,
        limit: int,
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT task_id FROM crawl_job_tasks
            WHERE job_id = ? AND stage_name = ? AND status = 'pending'
            ORDER BY sequence_number LIMIT ?
            """,
            (job_id, stage_name, limit),
        ).fetchall()

    @staticmethod
    def _select_waiting_for_promotion(
        connection: sqlite3.Connection,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT candidate.job_id, candidate.account_key FROM crawl_jobs AS candidate
            WHERE candidate.status = 'waiting_account'
              AND NOT EXISTS (
                  SELECT 1 FROM crawl_jobs AS active
                  WHERE active.account_key = candidate.account_key
                    AND active.status IN ('queued', 'running', 'cancelling')
              )
            ORDER BY candidate.queue_order, candidate.created_at, candidate.job_id LIMIT 1
            """
        ).fetchone()

    @staticmethod
    def _lock_account(connection: sqlite3.Connection, account_key: str) -> None:
        # BEGIN IMMEDIATE already serializes SQLite writers.
        return None

    @staticmethod
    def _lock_job(connection: sqlite3.Connection, job_id: str) -> None:
        # BEGIN IMMEDIATE already serializes SQLite writers.
        return None

    @staticmethod
    def _lock_idempotency(
        connection: sqlite3.Connection,
        owner_id: str,
        idempotency_key_hash: str,
    ) -> None:
        # BEGIN IMMEDIATE already serializes SQLite writers.
        return None


class SQLiteJobEngineRepository(_RelationalJobEngineRepository):
    """SQLite control-state adapter for local development and offline tests."""


# Compatibility name retained for Phase 02 callers created before hardening.
SQLiteControlRepository = SQLiteJobEngineRepository


def _resolve_stage_specs(
    tasks: Sequence[JobTaskSpec],
    stages: Sequence[JobStageSpec] | None,
) -> list[JobStageSpec]:
    if stages is None:
        names = list(dict.fromkeys(task.stage_name for task in tasks))
        if not names:
            raise ValueError('a job must declare at least one stage')
        return [JobStageSpec(name) for name in names]
    result = list(stages)
    if not result:
        raise ValueError('a job must declare at least one stage')
    names = [stage.stage_name for stage in result]
    if len(names) != len(set(names)):
        raise ValueError('stage_name must be unique within a job')
    return result


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    progress_state = row['progress_state']
    if isinstance(progress_state, str):
        progress_state = json.loads(progress_state)
    return JobRecord(
        job_id=row['job_id'], account_key=row['account_key'],
        company_tax_code=row['company_tax_code'], job_type=row['job_type'],
        queue_order=row['queue_order'],
        parameters=json.loads(row['parameters_json']), status=row['status'],
        current_stage=row['current_stage'], worker_id=row['worker_id'],
        lease_token=row['lease_token'], lease_generation=row['lease_generation'],
        lease_expires_at=_display_time(row['lease_expires_at']),
        available_at=_display_time(row['available_at']),
        cancel_requested_at=_display_time(row['cancel_requested_at']),
        warning_count=row['warning_count'], created_at=_display_time(row['created_at']),
        updated_at=_display_time(row['updated_at']),
        started_at=_display_time(row['started_at']),
        finished_at=_display_time(row['finished_at']),
        last_error_code=row['last_error_code'],
        last_error_message=row['last_error_message'],
        progress_percent=float(row['progress_percent']),
        total_tasks=int(row['total_tasks']),
        pending_tasks=int(row['pending_tasks']),
        running_tasks=int(row['running_tasks']),
        succeeded_tasks=int(row['succeeded_tasks']),
        failed_tasks=int(row['failed_tasks']),
        skipped_tasks=int(row['skipped_tasks']),
        progress_message=row['progress_message'],
        owner_id=row['owner_id'],
        pipeline_version=int(row['pipeline_version'] or 1),
        stage_progress_percent=float(row['stage_progress_percent'] or 0),
        progress_state=progress_state,
        progress_updated_at=_display_time(row['progress_updated_at']),
    )


def _stage_from_row(row: sqlite3.Row) -> StageRecord:
    return StageRecord(
        stage_name=row['stage_name'], sequence_number=row['sequence_number'],
        status=row['status'],
        task_generation_completed=bool(row['task_generation_completed']),
        started_at=_display_time(row['started_at']),
        finished_at=_display_time(row['finished_at']),
        last_error_code=row['last_error_code'],
        last_error_message=row['last_error_message'],
    )


def _task_from_row(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        task_id=row['task_id'], job_id=row['job_id'], task_key=row['task_key'],
        stage_name=row['stage_name'], task_type=row['task_type'],
        payload=json.loads(row['payload_json']), status=row['status'],
        failure_policy=row['failure_policy'], worker_id=row['worker_id'],
        lease_token=row['lease_token'], lease_generation=row['lease_generation'],
        lease_expires_at=_display_time(row['lease_expires_at']),
        attempt_count=row['attempt_count'], max_attempts=row['max_attempts'],
        next_retry_at=_display_time(row['next_retry_at']),
        last_error_code=row['last_error_code'],
        last_error_message=row['last_error_message'],
        created_at=_display_time(row['created_at']),
        updated_at=_display_time(row['updated_at']),
    )


def _display_time(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds')
    return str(value)


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime:
    value = value or _utc_now()
    if value.tzinfo is None:
        raise ValueError('control database timestamps must be timezone-aware')
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec='microseconds')


def _bounded(value: str, limit: int) -> str:
    return str(value).replace('\r', ' ').replace('\n', ' ')[:limit]


def _validate_worker_and_lease(worker_id: str, lease_seconds: int) -> None:
    if not worker_id or not worker_id.strip():
        raise ValueError('worker_id must not be empty')
    if lease_seconds < 1:
        raise ValueError('lease_seconds must be at least 1')


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crawl_jobs (
    job_id TEXT PRIMARY KEY,
    account_key TEXT NOT NULL,
    company_tax_code TEXT NOT NULL,
    job_type TEXT NOT NULL,
    queue_order INTEGER NOT NULL DEFAULT 0,
    parameters_json TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT,
    worker_id TEXT,
    lease_token TEXT,
    lease_generation INTEGER NOT NULL DEFAULT 0,
    lease_started_at TEXT,
    lease_expires_at TEXT,
    available_at TEXT NOT NULL,
    cancel_requested_at TEXT,
    warning_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    progress_percent REAL NOT NULL DEFAULT 0,
    total_tasks INTEGER NOT NULL DEFAULT 0,
    pending_tasks INTEGER NOT NULL DEFAULT 0,
    running_tasks INTEGER NOT NULL DEFAULT 0,
    succeeded_tasks INTEGER NOT NULL DEFAULT 0,
    failed_tasks INTEGER NOT NULL DEFAULT 0,
    skipped_tasks INTEGER NOT NULL DEFAULT 0,
    progress_message TEXT,
    owner_id TEXT,
    idempotency_key_hash TEXT,
    request_fingerprint TEXT
    ,pipeline_version INTEGER NOT NULL DEFAULT 1
    ,stage_progress_percent REAL NOT NULL DEFAULT 0
    ,progress_state TEXT
    ,progress_updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_crawl_jobs_active_account
ON crawl_jobs (account_key)
WHERE status IN ('queued', 'running', 'cancelling');
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_claim
ON crawl_jobs (status, available_at, created_at);
CREATE TABLE IF NOT EXISTS crawl_job_stages (
    stage_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES crawl_jobs(job_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    sequence_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    task_generation_completed INTEGER NOT NULL DEFAULT 1,
    started_at TEXT,
    finished_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (job_id, stage_name),
    UNIQUE (job_id, sequence_number)
);
CREATE INDEX IF NOT EXISTS idx_crawl_job_stages_state
ON crawl_job_stages (job_id, status, sequence_number);
CREATE TABLE IF NOT EXISTS crawl_job_tasks (
    task_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES crawl_jobs(job_id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL,
    task_key TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    failure_policy TEXT NOT NULL DEFAULT 'fail_job',
    worker_id TEXT,
    lease_token TEXT,
    lease_generation INTEGER NOT NULL DEFAULT 0,
    lease_started_at TEXT,
    lease_expires_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_retry_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (job_id, task_key),
    UNIQUE (job_id, sequence_number),
    FOREIGN KEY (job_id, stage_name)
        REFERENCES crawl_job_stages(job_id, stage_name)
);
CREATE INDEX IF NOT EXISTS idx_crawl_job_tasks_claim
ON crawl_job_tasks (job_id, stage_name, status, next_retry_at, sequence_number);
CREATE INDEX IF NOT EXISTS idx_crawl_job_tasks_lease
ON crawl_job_tasks (status, lease_expires_at);
"""
