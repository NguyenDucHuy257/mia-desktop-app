from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol
from urllib.parse import urlsplit

import psycopg

from app.postgres_migration_lock import acquire_control_schema_migration_lock


class ApiControlRepository(Protocol):
    def migrate(self) -> None: ...
    def ping(self) -> bool: ...
    def record_audit(self, **values: object) -> None: ...


class SQLiteApiControlRepository:
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser().resolve()

    def migrate(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.executescript(_SQLITE_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO control_schema_migrations VALUES (4, 'phase_04_external_api', ?)",
                (_now(),),
            )
            connection.commit()

    def ping(self) -> bool:
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                return connection.execute('SELECT 1').fetchone() == (1,)
        except sqlite3.Error:
            return False

    def record_audit(self, **values: object) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """INSERT INTO api_request_audit (
                    request_id, caller_id, key_id, method, route, status_code,
                    duration_ms, remote_address_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _audit_values(values),
            )
            connection.commit()


class PostgreSQLApiControlRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect_timeout_seconds: int = 10,
        query_timeout_seconds: int = 5,
    ) -> None:
        if urlsplit(database_url).scheme.casefold() not in {'postgresql', 'postgres'}:
            raise ValueError('PostgreSQL control URL is required')
        self.database_url = database_url
        if connect_timeout_seconds < 1:
            raise ValueError('PostgreSQL connect timeout must be at least 1 second')
        if query_timeout_seconds < 1:
            raise ValueError('PostgreSQL query timeout must be at least 1 second')
        self.connect_timeout_seconds = connect_timeout_seconds
        self.query_timeout_seconds = query_timeout_seconds

    @property
    def _connection_options(self) -> str:
        timeout_ms = self.query_timeout_seconds * 1000
        return f'-c statement_timeout={timeout_ms} -c lock_timeout={timeout_ms}'

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=self.connect_timeout_seconds,
            options=self._connection_options,
            autocommit=False,
        ) as connection:
            yield connection

    def migrate(self) -> None:
        with self._connection() as connection:
            acquire_control_schema_migration_lock(connection)
            connection.execute(_POSTGRES_SCHEMA)
            connection.execute(
                """INSERT INTO control_schema_migrations VALUES
                   (4, 'phase_04_external_api', CURRENT_TIMESTAMP)
                   ON CONFLICT (version) DO NOTHING"""
            )

    def ping(self) -> bool:
        try:
            with self._connection() as connection:
                return connection.execute('SELECT 1').fetchone() == (1,)
        except psycopg.Error:
            return False

    def record_audit(self, **values: object) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO api_request_audit (
                    request_id, caller_id, key_id, method, route, status_code,
                    duration_ms, remote_address_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                _audit_values(values),
            )


def _audit_values(values: dict[str, object]) -> tuple[object, ...]:
    return (
        values.get('request_id'), values.get('caller_id'), values.get('key_id'),
        values.get('method'), values.get('route'), values.get('status_code'),
        values.get('duration_ms'), values.get('remote_address_hash'), _now(),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_schema_migrations (
    version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_request_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL, caller_id TEXT, key_id TEXT,
    method TEXT NOT NULL, route TEXT NOT NULL, status_code INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL, remote_address_hash TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_request_audit_created
ON api_request_audit (created_at);
"""


_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_request_audit (
    audit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id TEXT NOT NULL, caller_id TEXT, key_id TEXT,
    method TEXT NOT NULL, route TEXT NOT NULL, status_code INTEGER NOT NULL,
    duration_ms BIGINT NOT NULL, remote_address_hash TEXT, created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_request_audit_created
ON api_request_audit (created_at)
"""
