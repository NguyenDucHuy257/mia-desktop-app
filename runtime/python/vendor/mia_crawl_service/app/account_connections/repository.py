from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Protocol, Sequence
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg.rows import dict_row

from app.account_connections.models import AccountConnection
from app.postgres_migration_lock import acquire_control_schema_migration_lock


CONTROL_DATABASE_URL_ENV = 'MIA_CONTROL_DATABASE_URL'
DEFAULT_SQLITE_PATH = Path('data/control/control.sqlite3')
_ACTIVE_SESSION_STATUSES = ('pending', 'authenticating', 'active', 'authentication_failed')


class AccountConnectionError(RuntimeError):
    pass


class AccountConnectionNotFoundError(AccountConnectionError):
    pass


class AccountConnectionConflictError(AccountConnectionError):
    pass


class AccountConnectionRepository(Protocol):
    def migrate(self) -> None: ...
    def ping(self) -> bool: ...
    def get(self, connection_id: str, *, owner_id: str) -> AccountConnection: ...
    def get_by_username(
        self, username: str, *, owner_id: str
    ) -> AccountConnection | None: ...
    def find_unmapped_session_hash(
        self, username: str, *, owner_id: str
    ) -> str | None: ...
    def bind(
        self, *, connection_id: str, owner_id: str, username: str,
        session_hash: str, now: datetime | None = None,
    ) -> AccountConnection: ...
    def reconnect(
        self, connection_id: str, *, owner_id: str,
        credential_envelope: str, credential_key_id: str,
        expires_at: datetime, now: datetime | None = None,
    ) -> AccountConnection: ...
    def revoke(
        self, connection_id: str, *, owner_id: str,
        now: datetime | None = None,
    ) -> AccountConnection: ...


class RelationalAccountConnectionRepository:
    """Persistent public connection IDs mapped to worker-safe session hashes.

    Existing encrypted session/token storage remains authoritative. This layer
    makes the stable connection identifier recoverable and idempotent, avoiding
    the legacy orphan-session failure mode where only the client knew the raw
    internal session ID.
    """

    def migrate(self) -> None:
        with self._transaction() as connection:
            self._before_migrate(connection)
            _execute_script(connection, self._schema())
            connection.execute(
                self._migration_sql(),
                self._migration_parameters(),
            )

    def ping(self) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute('SELECT 1 AS value').fetchone()
            return bool(row and int(row['value']) == 1)

    def get(self, connection_id: str, *, owner_id: str) -> AccountConnection:
        _validate_connection_id(connection_id)
        _validate_owner(owner_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                _SELECT_CONNECTION + ' WHERE c.connection_id = ? AND c.owner_id = ?',
                (connection_id, owner_id),
            ).fetchone()
            if row is None:
                raise AccountConnectionNotFoundError('account connection was not found')
            return _connection_from_row(row)

    def get_by_username(
        self, username: str, *, owner_id: str
    ) -> AccountConnection | None:
        normalized = _normalize_username(username)
        _validate_owner(owner_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                _SELECT_CONNECTION
                + " WHERE c.owner_id = ? AND c.username = ? AND c.status <> 'revoked'",
                (owner_id, normalized),
            ).fetchone()
            return _connection_from_row(row) if row is not None else None

    def find_unmapped_session_hash(
        self, username: str, *, owner_id: str
    ) -> str | None:
        normalized = _normalize_username(username)
        _validate_owner(owner_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT s.session_hash
                FROM internal_sessions AS s
                JOIN source_accounts AS a ON a.account_id = s.account_id
                LEFT JOIN account_connections AS c
                  ON c.session_hash = s.session_hash
                WHERE s.owner_id = ?
                  AND a.account_key = ?
                  AND s.status IN (
                    'pending', 'authenticating', 'active', 'authentication_failed'
                  )
                  AND c.connection_id IS NULL
                ORDER BY s.updated_at DESC
                LIMIT 1
                """,
                (owner_id, normalized.casefold()),
            ).fetchone()
            return str(row['session_hash']) if row is not None else None

    def bind(
        self,
        *,
        connection_id: str,
        owner_id: str,
        username: str,
        session_hash: str,
        now: datetime | None = None,
    ) -> AccountConnection:
        _validate_connection_id(connection_id)
        _validate_owner(owner_id)
        normalized = _normalize_username(username)
        _validate_session_hash(session_hash)
        timestamp = _timestamp(_as_utc(now))
        with self._transaction() as connection:
            self._lock_owner_username(connection, owner_id, normalized)
            existing = connection.execute(
                _SELECT_CONNECTION
                + " WHERE c.owner_id = ? AND c.username = ? AND c.status <> 'revoked'",
                (owner_id, normalized),
            ).fetchone()
            if existing is not None:
                return _connection_from_row(existing)
            session = connection.execute(
                """
                SELECT session_hash FROM internal_sessions
                WHERE session_hash = ? AND owner_id = ?
                """,
                (session_hash, owner_id),
            ).fetchone()
            if session is None:
                raise AccountConnectionNotFoundError('internal authentication state was not found')
            try:
                connection.execute(
                    """
                    INSERT INTO account_connections (
                        connection_id, owner_id, username, session_hash,
                        status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        connection_id, owner_id, normalized, session_hash,
                        timestamp, timestamp,
                    ),
                )
            except Exception as error:
                # A concurrent idempotent create may have won the unique key.
                existing = connection.execute(
                    _SELECT_CONNECTION
                    + " WHERE c.owner_id = ? AND c.username = ? AND c.status <> 'revoked'",
                    (owner_id, normalized),
                ).fetchone()
                if existing is None:
                    raise AccountConnectionConflictError(
                        'account connection could not be created'
                    ) from error
                return _connection_from_row(existing)
            return self._get_locked(connection, connection_id, owner_id)

    def reconnect(
        self,
        connection_id: str,
        *,
        owner_id: str,
        credential_envelope: str,
        credential_key_id: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> AccountConnection:
        _validate_connection_id(connection_id)
        _validate_owner(owner_id)
        current = _as_utc(now)
        expiry = _as_utc(expires_at)
        if expiry <= current:
            raise ValueError('connection expiry must be in the future')
        timestamp = _timestamp(current)
        with self._transaction() as connection:
            record = self._get_locked(connection, connection_id, owner_id)
            if record.status == 'revoked':
                raise AccountConnectionNotFoundError('account connection was revoked')
            updated = connection.execute(
                """
                UPDATE internal_sessions
                SET status = 'pending', credential_envelope = ?,
                    credential_key_id = ?, source_token_envelope = NULL,
                    source_token_key_id = NULL, auth_owner = NULL,
                    auth_lease_token = NULL, auth_lease_expires_at = NULL,
                    expires_at = ?, last_auth_error_code = NULL,
                    last_auth_error_message = NULL, updated_at = ?
                WHERE session_hash = ? AND owner_id = ?
                """,
                (
                    credential_envelope, credential_key_id,
                    _timestamp(expiry), timestamp,
                    record.session_hash, owner_id,
                ),
            ).rowcount
            if updated != 1:
                raise AccountConnectionNotFoundError('authentication state was not found')
            connection.execute(
                """
                UPDATE account_connections
                SET status = 'pending', revoked_at = NULL, updated_at = ?
                WHERE connection_id = ? AND owner_id = ?
                """,
                (timestamp, connection_id, owner_id),
            )
            return self._get_locked(connection, connection_id, owner_id)

    def revoke(
        self,
        connection_id: str,
        *,
        owner_id: str,
        now: datetime | None = None,
    ) -> AccountConnection:
        _validate_connection_id(connection_id)
        _validate_owner(owner_id)
        timestamp = _timestamp(_as_utc(now))
        with self._transaction() as connection:
            record = self._get_locked(connection, connection_id, owner_id)
            if record.status == 'revoked':
                return record
            connection.execute(
                """
                UPDATE internal_sessions
                SET status = 'revoked', credential_envelope = NULL,
                    credential_key_id = NULL, source_token_envelope = NULL,
                    source_token_key_id = NULL, auth_owner = NULL,
                    auth_lease_token = NULL, auth_lease_expires_at = NULL,
                    revoked_at = ?, updated_at = ?
                WHERE session_hash = ? AND owner_id = ?
                """,
                (timestamp, timestamp, record.session_hash, owner_id),
            )
            connection.execute(
                """
                UPDATE account_connections
                SET status = 'revoked', revoked_at = ?, updated_at = ?
                WHERE connection_id = ? AND owner_id = ?
                """,
                (timestamp, timestamp, connection_id, owner_id),
            )
            return self._get_locked(connection, connection_id, owner_id)

    def _get_locked(
        self, connection, connection_id: str, owner_id: str
    ) -> AccountConnection:
        self._lock_connection(connection, connection_id)
        row = connection.execute(
            _SELECT_CONNECTION + ' WHERE c.connection_id = ? AND c.owner_id = ?',
            (connection_id, owner_id),
        ).fetchone()
        if row is None:
            raise AccountConnectionNotFoundError('account connection was not found')
        return _connection_from_row(row)

    def _schema(self) -> str:
        raise NotImplementedError

    def _migration_sql(self) -> str:
        raise NotImplementedError

    def _migration_parameters(self) -> Sequence[object]:
        return ()

    def _lock_owner_username(self, connection, owner_id: str, username: str) -> None:
        return None

    def _lock_connection(self, connection, connection_id: str) -> None:
        return None

    def _before_migrate(self, connection) -> None:
        return None

    @contextmanager
    def _transaction(self):
        raise NotImplementedError

    def _connect(self):
        raise NotImplementedError


class SQLiteAccountConnectionRepository(RelationalAccountConnectionRepository):
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser().resolve()

    def _schema(self) -> str:
        return _SQLITE_SCHEMA

    def _migration_sql(self) -> str:
        return """
            INSERT OR IGNORE INTO control_schema_migrations
                (version, name, applied_at)
            VALUES (7, 'account_connections', ?)
        """

    def _migration_parameters(self) -> Sequence[object]:
        return (_timestamp(datetime.now(timezone.utc)),)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.database_path, timeout=30, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA busy_timeout = 30000')
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
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        return connection


class PostgreSQLAccountConnectionRepository(RelationalAccountConnectionRepository):
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _before_migrate(self, connection) -> None:
        acquire_control_schema_migration_lock(connection)

    def _schema(self) -> str:
        return _POSTGRES_SCHEMA

    def _migration_sql(self) -> str:
        return """
            INSERT INTO control_schema_migrations (version, name, applied_at)
            VALUES (7, 'account_connections', CURRENT_TIMESTAMP)
            ON CONFLICT (version) DO NOTHING
        """

    @contextmanager
    def _transaction(self) -> Iterator['_PostgresConnection']:
        raw = psycopg.connect(
            self.database_url, connect_timeout=10,
            row_factory=dict_row, autocommit=False,
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

    def _connect(self) -> '_PostgresConnection':
        raw = psycopg.connect(
            self.database_url, connect_timeout=10,
            row_factory=dict_row, autocommit=True,
        )
        connection = _PostgresConnection(raw)
        connection.execute("SET TIME ZONE 'UTC'")
        return connection

    def _lock_owner_username(self, connection, owner_id: str, username: str) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext('connection:' || ? || ':' || ?))",
            (owner_id, username),
        )

    def _lock_connection(self, connection, connection_id: str) -> None:
        connection.execute(
            'SELECT connection_id FROM account_connections '
            'WHERE connection_id = ? FOR UPDATE',
            (connection_id,),
        )


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


def create_account_connection_repository(
    *, database_url: str | None = None, sqlite_path: Path | str | None = None,
) -> AccountConnectionRepository:
    if sqlite_path is not None and database_url is None:
        return SQLiteAccountConnectionRepository(sqlite_path)
    url = database_url or os.getenv(CONTROL_DATABASE_URL_ENV)
    if not url:
        return SQLiteAccountConnectionRepository(DEFAULT_SQLITE_PATH)
    scheme = urlsplit(url).scheme.casefold()
    if scheme in {'postgresql', 'postgres'}:
        return PostgreSQLAccountConnectionRepository(url)
    if scheme == 'sqlite':
        parsed = urlsplit(url)
        if parsed.netloc not in {'', 'localhost'}:
            raise ValueError('sqlite URL must reference a local path')
        raw_path = unquote(parsed.path)
        if raw_path.startswith('/') and len(raw_path) > 3 and raw_path[2] == ':':
            raw_path = raw_path[1:]
        return SQLiteAccountConnectionRepository(raw_path)
    raise ValueError('unsupported MIA control database URL scheme')


def new_connection_id() -> str:
    return 'conn_' + uuid.uuid4().hex


def _connection_from_row(row) -> AccountConnection:
    source_status = str(row['source_status'])
    status = {
        'pending': 'pending',
        'authenticating': 'pending',
        'active': 'ready',
        'authentication_failed': 'auth_failed',
        'revoked': 'revoked',
        'expired': 'suspended',
    }.get(source_status, str(row['connection_status']))
    if str(row['connection_status']) == 'revoked':
        status = 'revoked'
    return AccountConnection(
        connection_id=str(row['connection_id']),
        owner_id=str(row['owner_id']),
        username=str(row['username']),
        session_hash=str(row['session_hash']),
        status=status,
        token_generation=int(row['token_generation']),
        created_at=_parse_timestamp(row['created_at']),
        updated_at=_parse_timestamp(row['updated_at']),
        revoked_at=(
            _parse_timestamp(row['revoked_at'])
            if row['revoked_at'] is not None else None
        ),
    )


def _execute_script(connection, script: str) -> None:
    if isinstance(connection, sqlite3.Connection):
        connection.executescript(script)
        return
    for statement in script.split(';'):
        if statement.strip():
            connection.execute(statement)


def _normalize_username(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError('username is required')
    if len(normalized) > 256:
        raise ValueError('username is too long')
    return normalized


def _validate_owner(owner_id: str) -> None:
    if not owner_id or not owner_id.strip():
        raise ValueError('owner_id is required')


def _validate_connection_id(connection_id: str) -> None:
    if not connection_id or not connection_id.startswith('conn_'):
        raise ValueError('connection_id is invalid')


def _validate_session_hash(session_hash: str) -> None:
    if len(session_hash) != 64 or any(ch not in '0123456789abcdef' for ch in session_hash):
        raise ValueError('session_hash is invalid')


def _as_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError('timestamps must be timezone-aware')
    return current.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _parse_timestamp(value) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    return _as_utc(datetime.fromisoformat(str(value)))


_SELECT_CONNECTION = """
SELECT
    c.connection_id,
    c.owner_id,
    c.username,
    c.session_hash,
    c.status AS connection_status,
    c.created_at,
    c.updated_at,
    c.revoked_at,
    s.status AS source_status,
    s.token_generation
FROM account_connections AS c
JOIN internal_sessions AS s ON s.session_hash = c.session_hash
"""


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_connections (
    connection_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    username TEXT NOT NULL,
    session_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'ready', 'auth_failed', 'suspended', 'revoked')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY(session_hash) REFERENCES internal_sessions(session_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_account_connections_owner_username
ON account_connections (owner_id, username)
WHERE status <> 'revoked';
CREATE INDEX IF NOT EXISTS idx_account_connections_session
ON account_connections (session_hash);
"""


_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS account_connections (
    connection_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    username TEXT NOT NULL,
    session_hash TEXT NOT NULL UNIQUE REFERENCES internal_sessions(session_hash),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'ready', 'auth_failed', 'suspended', 'revoked')
    ),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_account_connections_owner_username
ON account_connections (owner_id, username)
WHERE status <> 'revoked';
CREATE INDEX IF NOT EXISTS idx_account_connections_session
ON account_connections (session_hash);
"""
