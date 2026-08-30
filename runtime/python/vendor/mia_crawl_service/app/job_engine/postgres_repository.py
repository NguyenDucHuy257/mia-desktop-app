from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Iterator, Sequence
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.job_engine.models import JobTaskSpec
from app.postgres_migration_lock import acquire_control_schema_migration_lock
from app.job_engine.repository import (
    _RelationalJobEngineRepository,
    _json_dump,
)


class PostgreSQLJobEngineRepository(_RelationalJobEngineRepository):
    """Production control-state adapter using PostgreSQL row locking."""

    def __init__(
        self,
        database_url: str,
        *,
        connect_timeout_seconds: int = 10,
        query_timeout_seconds: int = 5,
    ) -> None:
        scheme = urlsplit(database_url).scheme.casefold()
        if scheme not in {'postgresql', 'postgres'}:
            raise ValueError('PostgreSQL control URL must use postgresql:// or postgres://')
        self._database_url = database_url
        self.connect_timeout_seconds = connect_timeout_seconds
        if query_timeout_seconds < 1:
            raise ValueError('PostgreSQL query timeout must be at least 1 second')
        self.query_timeout_seconds = query_timeout_seconds

    @property
    def _connection_options(self) -> str:
        timeout_ms = self.query_timeout_seconds * 1000
        return f'-c statement_timeout={timeout_ms} -c lock_timeout={timeout_ms}'

    def migrate(self) -> None:
        with self._transaction() as connection:
            acquire_control_schema_migration_lock(connection)
            _execute_script(connection, _POSTGRES_SCHEMA)
            _execute_script(connection, """
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS lease_token TEXT;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS
                    lease_generation BIGINT NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS
                    warning_count INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS
                    queue_order BIGINT NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS owner_id TEXT;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS idempotency_key_hash TEXT;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS progress_percent DOUBLE PRECISION NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS total_tasks INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS pending_tasks INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS running_tasks INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS succeeded_tasks INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS failed_tasks INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS skipped_tasks INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS progress_message TEXT;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS pipeline_version INTEGER NOT NULL DEFAULT 1;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS stage_progress_percent NUMERIC(7,4) NOT NULL DEFAULT 0;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS progress_state JSONB;
                ALTER TABLE crawl_jobs ADD COLUMN IF NOT EXISTS progress_updated_at TIMESTAMPTZ;
                CREATE UNIQUE INDEX IF NOT EXISTS uq_crawl_jobs_owner_idempotency
                    ON crawl_jobs (owner_id, idempotency_key_hash)
                    WHERE owner_id IS NOT NULL AND idempotency_key_hash IS NOT NULL;
                ALTER TABLE crawl_job_stages ADD COLUMN IF NOT EXISTS
                    task_generation_completed SMALLINT NOT NULL DEFAULT 1;
                ALTER TABLE crawl_job_tasks ADD COLUMN IF NOT EXISTS
                    failure_policy TEXT NOT NULL DEFAULT 'fail_job';
                ALTER TABLE crawl_job_tasks ADD COLUMN IF NOT EXISTS lease_token TEXT;
                ALTER TABLE crawl_job_tasks ADD COLUMN IF NOT EXISTS
                    lease_generation BIGINT NOT NULL DEFAULT 0;
                """)
            connection.execute('DROP TABLE IF EXISTS webhook_outbox')
            connection.execute('DROP TABLE IF EXISTS job_progress_snapshots')
            connection.execute(
                """
                INSERT INTO control_schema_migrations (version, name, applied_at)
                VALUES (1, 'phase_02_job_engine', CURRENT_TIMESTAMP)
                ON CONFLICT (version) DO NOTHING
                """
            )
            connection.execute(
                """INSERT INTO control_schema_migrations (version, name, applied_at)
                   VALUES (6, 'phase_08_taskless_job_pipeline', CURRENT_TIMESTAMP)
                   ON CONFLICT (version) DO UPDATE SET name = EXCLUDED.name"""
            )

            connection.execute(
                """INSERT INTO control_schema_migrations (version, name, applied_at)
                   VALUES (5, 'phase_06_job_polling_progress', CURRENT_TIMESTAMP)
                   ON CONFLICT (version) DO UPDATE SET name = EXCLUDED.name"""
            )
            connection.execute(
                """
                INSERT INTO control_schema_migrations (version, name, applied_at)
                VALUES (2, 'phase_02_job_engine_hardening', CURRENT_TIMESTAMP)
                ON CONFLICT (version) DO NOTHING
                """
            )

    @staticmethod
    def _progress_value(value):
        return Jsonb(value)

    def _insert_tasks(
        self,
        connection: _PostgresConnection,
        job_id: str,
        tasks: Sequence[JobTaskSpec],
        timestamp: str,
    ) -> int:
        self._lock_job(connection, job_id)
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) AS value FROM crawl_job_tasks WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        next_sequence = int(row['value']) + 1
        inserted = 0
        import uuid
        for offset, task in enumerate(tasks):
            cursor = connection.execute(
                """
                INSERT INTO crawl_job_tasks (
                    task_id, job_id, sequence_number, task_key, stage_name,
                    task_type, payload_json, status, failure_policy, max_attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                ON CONFLICT (job_id, task_key) DO NOTHING
                """,
                (
                    str(uuid.uuid4()), job_id, next_sequence + offset, task.task_key,
                    task.stage_name, task.task_type, _json_dump(task.payload),
                    task.failure_policy, task.max_attempts, timestamp, timestamp,
                ),
            )
            inserted += cursor.rowcount
        return inserted

    def _resolve_now(self, value: datetime | None) -> datetime:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError('control database timestamps must be timezone-aware')
            return value.astimezone(timezone.utc)
        with closing(self._connect()) as connection:
            row = connection.execute(
                'SELECT CURRENT_TIMESTAMP AS current_time'
            ).fetchone()
            return row['current_time'].astimezone(timezone.utc)

    @staticmethod
    def _lock_account(connection: _PostgresConnection, account_key: str) -> None:
        connection.execute(
            'SELECT pg_advisory_xact_lock(hashtext(?))', (account_key,)
        )

    @staticmethod
    def _lock_job(connection: _PostgresConnection, job_id: str) -> None:
        connection.execute('SELECT pg_advisory_xact_lock(hashtext(?))', (job_id,))

    @staticmethod
    def _lock_idempotency(
        connection: _PostgresConnection,
        owner_id: str,
        idempotency_key_hash: str,
    ) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext('api-idempotency:' || ? || ':' || ?))",
            (owner_id, idempotency_key_hash),
        )

    @staticmethod
    def _select_job_for_claim(
        connection: _PostgresConnection,
        timestamp: str,
    ):
        return connection.execute(
            """
            SELECT job_id FROM crawl_jobs
            WHERE status = 'queued' AND pipeline_version = 2 AND available_at <= ?
            ORDER BY created_at, job_id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (timestamp,),
        ).fetchone()

    @staticmethod
    def _select_expired_jobs(connection: _PostgresConnection, timestamp: str):
        return connection.execute(
            """
            SELECT job_id, status FROM crawl_jobs
            WHERE status IN ('running', 'cancelling')
              AND (
                  worker_id IS NULL OR lease_token IS NULL
                  OR lease_expires_at IS NULL OR lease_expires_at <= ?
              )
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            """,
            (timestamp,),
        ).fetchall()

    @staticmethod
    def _select_tasks_for_claim(
        connection: _PostgresConnection,
        job_id: str,
        stage_name: str,
        limit: int,
    ):
        return connection.execute(
            """
            SELECT task_id FROM crawl_job_tasks
            WHERE job_id = ? AND stage_name = ? AND status = 'pending'
            ORDER BY sequence_number
            FOR UPDATE SKIP LOCKED
            LIMIT ?
            """,
            (job_id, stage_name, limit),
        ).fetchall()

    @staticmethod
    def _select_waiting_for_promotion(connection: _PostgresConnection):
        return connection.execute(
            """
            SELECT candidate.job_id, candidate.account_key
            FROM crawl_jobs AS candidate
            WHERE candidate.status = 'waiting_account'
              AND NOT EXISTS (
                  SELECT 1 FROM crawl_jobs AS active
                  WHERE active.account_key = candidate.account_key
                    AND active.status IN ('queued', 'running', 'cancelling')
              )
            ORDER BY candidate.queue_order, candidate.created_at, candidate.job_id
            FOR UPDATE OF candidate SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()

    @contextmanager
    def _transaction(self) -> Iterator[_PostgresConnection]:
        raw = psycopg.connect(
            self._database_url,
            connect_timeout=self.connect_timeout_seconds,
            options=self._connection_options,
            row_factory=dict_row,
            autocommit=False,
        )
        connection = _PostgresConnection(raw)
        try:
            connection.execute("SET LOCAL TIME ZONE 'UTC'")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> _PostgresConnection:
        raw = psycopg.connect(
            self._database_url,
            connect_timeout=self.connect_timeout_seconds,
            options=self._connection_options,
            row_factory=dict_row,
            autocommit=True,
        )
        connection = _PostgresConnection(raw)
        connection.execute("SET TIME ZONE 'UTC'")
        return connection


class _PostgresConnection:
    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    def execute(self, statement: str, parameters: Sequence[object] = ()):
        return self._connection.execute(_qmark_to_postgres(statement), parameters)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def _qmark_to_postgres(statement: str) -> str:
    return statement.replace('?', '%s')


def _execute_script(connection: _PostgresConnection, script: str) -> None:
    for statement in script.split(';'):
        if statement.strip():
            connection.execute(statement)


_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS crawl_jobs (
    job_id TEXT PRIMARY KEY,
    account_key TEXT NOT NULL,
    company_tax_code TEXT NOT NULL,
    job_type TEXT NOT NULL,
    queue_order BIGINT NOT NULL DEFAULT 0,
    parameters_json TEXT NOT NULL,
    status TEXT NOT NULL,
    current_stage TEXT,
    worker_id TEXT,
    lease_token TEXT,
    lease_generation BIGINT NOT NULL DEFAULT 0,
    lease_started_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    available_at TIMESTAMPTZ NOT NULL,
    cancel_requested_at TIMESTAMPTZ,
    warning_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    last_error_code TEXT,
    last_error_message TEXT,
    progress_percent DOUBLE PRECISION NOT NULL DEFAULT 0,
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
    ,stage_progress_percent NUMERIC(7,4) NOT NULL DEFAULT 0
    ,progress_state JSONB
    ,progress_updated_at TIMESTAMPTZ
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
    task_generation_completed SMALLINT NOT NULL DEFAULT 1,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
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
    lease_generation BIGINT NOT NULL DEFAULT 0,
    lease_started_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_retry_at TIMESTAMPTZ,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
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
