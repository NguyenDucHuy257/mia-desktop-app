from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Iterator, Sequence
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from app.postgres_migration_lock import acquire_control_schema_migration_lock
from app.session_manager.repository import _RelationalSessionRepository


class PostgreSQLSessionRepository(_RelationalSessionRepository):
    """Production session adapter using PostgreSQL row/advisory locking."""

    def __init__(self, database_url: str, *, connect_timeout_seconds: int = 10) -> None:
        scheme = urlsplit(database_url).scheme.casefold()
        if scheme not in {'postgresql', 'postgres'}:
            raise ValueError('PostgreSQL control URL must use postgresql:// or postgres://')
        self._database_url = database_url
        self.connect_timeout_seconds = connect_timeout_seconds

    def migrate(self) -> None:
        with self._transaction() as connection:
            acquire_control_schema_migration_lock(connection)
            _execute_script(connection, _POSTGRES_SESSION_SCHEMA)
            connection.execute(
                'ALTER TABLE internal_sessions ADD COLUMN IF NOT EXISTS owner_id TEXT'
            )
            connection.execute(
                """
                INSERT INTO control_schema_migrations (version, name, applied_at)
                VALUES (3, 'phase_03_session_token', CURRENT_TIMESTAMP)
                ON CONFLICT (version) DO NOTHING
                """
            )

    def _resolve_now(self, value: datetime | None) -> datetime:
        if value is not None:
            if value.tzinfo is None:
                raise ValueError('session timestamps must be timezone-aware')
            return value.astimezone(timezone.utc)
        with closing(self._connect()) as connection:
            row = connection.execute(
                'SELECT CURRENT_TIMESTAMP AS current_time'
            ).fetchone()
            return row['current_time'].astimezone(timezone.utc)

    @staticmethod
    def _lock_account(connection: _PostgresConnection, account_key: str) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext('session-account:' || ?))",
            (account_key,),
        )

    @staticmethod
    def _lock_session(connection: _PostgresConnection, session_hash: str) -> None:
        connection.execute(
            'SELECT session_hash FROM internal_sessions WHERE session_hash = ? FOR UPDATE',
            (session_hash,),
        )

    @contextmanager
    def _transaction(self) -> Iterator[_PostgresConnection]:
        raw = psycopg.connect(
            self._database_url,
            connect_timeout=self.connect_timeout_seconds,
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
        return self._connection.execute(statement.replace('?', '%s'), parameters)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def _execute_script(connection: _PostgresConnection, script: str) -> None:
    for statement in script.split(';'):
        if statement.strip():
            connection.execute(statement)


_POSTGRES_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS source_accounts (
    account_id TEXT PRIMARY KEY,
    account_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS internal_sessions (
    session_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES source_accounts(account_id),
    owner_id TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'pending', 'authenticating', 'active',
            'authentication_failed', 'revoked', 'expired'
        )
    ),
    credential_envelope TEXT,
    credential_key_id TEXT,
    source_token_envelope TEXT,
    source_token_key_id TEXT,
    token_generation BIGINT NOT NULL DEFAULT 0,
    auth_owner TEXT,
    auth_lease_token TEXT,
    auth_lease_generation BIGINT NOT NULL DEFAULT 0,
    auth_lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    last_authenticated_at TIMESTAMPTZ,
    last_auth_error_code TEXT,
    last_auth_error_message TEXT,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_internal_sessions_active_account
ON internal_sessions (account_id)
WHERE status IN ('pending', 'authenticating', 'active', 'authentication_failed');
CREATE INDEX IF NOT EXISTS idx_internal_sessions_expiry
ON internal_sessions (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_internal_sessions_auth_lease
ON internal_sessions (status, auth_lease_expires_at);
"""
