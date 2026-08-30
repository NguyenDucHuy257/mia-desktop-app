from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.session_manager.models import (
    SESSION_AUTHENTICATING,
    SESSION_EXPIRED,
    SESSION_REVOKED,
    ActiveSessionExistsError,
    AuthenticationLease,
    AuthenticationLeaseLostError,
    PersistedSession,
    SessionNotFoundError,
    SessionUnavailableError,
)


class _RelationalSessionRepository:
    """Shared session state policy; adapters provide transactions and locking."""

    def __init__(self, database_path: Path | str, *, busy_timeout_seconds: int = 30) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.busy_timeout_seconds = busy_timeout_seconds

    def migrate(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute('PRAGMA journal_mode = WAL')
            connection.executescript(_SQLITE_SESSION_SCHEMA)
            self._ensure_column(connection, 'internal_sessions', 'owner_id', 'TEXT')
            connection.execute(
                """
                INSERT OR IGNORE INTO control_schema_migrations (version, name, applied_at)
                VALUES (3, 'phase_03_session_token', ?)
                """,
                (_timestamp(_utc_now()),),
            )

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

    def create_session(
        self,
        *,
        session_hash: str,
        account_key: str,
        credential_envelope: str,
        credential_key_id: str,
        expires_at: datetime,
        owner_id: str | None = None,
        now: datetime | None = None,
    ) -> PersistedSession:
        _validate_session_hash(session_hash)
        normalized_account_key = _normalize_account_key(account_key)
        current = self._resolve_now(now)
        expiry = _as_utc(expires_at)
        if expiry <= current:
            raise ValueError('session expires_at must be in the future')
        timestamp = _timestamp(current)
        account_id = str(uuid.uuid4())
        with self._transaction() as connection:
            self._lock_account(connection, normalized_account_key)
            connection.execute(
                """
                INSERT INTO source_accounts (
                    account_id, account_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (account_key) DO NOTHING
                """,
                (account_id, normalized_account_key, timestamp, timestamp),
            )
            account = connection.execute(
                'SELECT account_id FROM source_accounts WHERE account_key = ?',
                (normalized_account_key,),
            ).fetchone()
            account_id = account['account_id']
            connection.execute(
                """
                UPDATE internal_sessions
                SET status = 'expired', auth_owner = NULL,
                    auth_lease_token = NULL, auth_lease_expires_at = NULL,
                    credential_envelope = NULL, source_token_envelope = NULL,
                    updated_at = ?
                WHERE account_id = ? AND expires_at <= ?
                  AND status IN (
                      'pending', 'authenticating', 'active',
                      'authentication_failed'
                  )
                """,
                (timestamp, account_id, timestamp),
            )
            active = connection.execute(
                """
                SELECT 1 FROM internal_sessions
                WHERE account_id = ?
                  AND status IN ('pending', 'authenticating', 'active', 'authentication_failed')
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if active is not None:
                raise ActiveSessionExistsError(
                    'an active internal session already exists for this account'
                )
            connection.execute(
                """
                INSERT INTO internal_sessions (
                    session_hash, account_id, owner_id, status,
                    credential_envelope, credential_key_id,
                    token_generation, auth_lease_generation,
                    created_at, expires_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, 0, 0, ?, ?, ?)
                """,
                (
                    session_hash,
                    account_id,
                    owner_id,
                    credential_envelope,
                    credential_key_id,
                    timestamp,
                    _timestamp(expiry),
                    timestamp,
                ),
            )
            return self._get_session(connection, session_hash)

    def get_session(self, session_hash: str) -> PersistedSession:
        _validate_session_hash(session_hash)
        with closing(self._connect()) as connection:
            return self._get_session(connection, session_hash)

    def claim_authentication(
        self,
        session_hash: str,
        worker_id: str,
        *,
        expected_generation: int,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AuthenticationLease:
        _validate_auth_request(worker_id, lease_seconds, expected_generation)
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        with self._transaction() as connection:
            self._lock_session(connection, session_hash)
            session = self._get_session(connection, session_hash)
            if session.expires_at <= current:
                self._expire_one(connection, session_hash, timestamp)
                raise SessionUnavailableError('internal session is expired')
            if session.status in (SESSION_REVOKED, SESSION_EXPIRED):
                raise SessionUnavailableError(
                    f'internal session is {session.status}'
                )
            if session.token_generation > expected_generation:
                return AuthenticationLease(False, session)
            if session.token_generation != expected_generation:
                raise ValueError('expected token generation is ahead of durable state')
            lease_is_active = (
                session.status == SESSION_AUTHENTICATING
                and session.auth_lease_expires_at is not None
                and session.auth_lease_expires_at > current
            )
            if lease_is_active:
                return AuthenticationLease(False, session)

            lease_token = str(uuid.uuid4())
            expires = _timestamp(current + timedelta(seconds=lease_seconds))
            connection.execute(
                """
                UPDATE internal_sessions
                SET status = 'authenticating', auth_owner = ?, auth_lease_token = ?,
                    auth_lease_generation = auth_lease_generation + 1,
                    auth_lease_expires_at = ?, updated_at = ?,
                    last_auth_error_code = NULL, last_auth_error_message = NULL
                WHERE session_hash = ?
                """,
                (worker_id, lease_token, expires, timestamp, session_hash),
            )
            return AuthenticationLease(
                True,
                self._get_session(connection, session_hash),
                worker_id,
                lease_token,
            )

    def renew_authentication_lease(
        self,
        session_hash: str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> PersistedSession:
        _validate_auth_request(worker_id, lease_seconds, 0)
        if not lease_token:
            raise ValueError('authentication lease token is required')
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        expires = _timestamp(current + timedelta(seconds=lease_seconds))
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE internal_sessions
                SET auth_lease_expires_at = ?, updated_at = ?
                WHERE session_hash = ? AND status = 'authenticating'
                  AND auth_owner = ? AND auth_lease_token = ?
                  AND auth_lease_expires_at > ?
                  AND expires_at > ?
                """,
                (
                    expires, timestamp, session_hash, worker_id, lease_token,
                    timestamp, timestamp,
                ),
            )
            if cursor.rowcount != 1:
                raise AuthenticationLeaseLostError('authentication lease was lost')
            return self._get_session(connection, session_hash)

    def complete_authentication(
        self,
        session_hash: str,
        worker_id: str,
        lease_token: str,
        *,
        token_envelope: str,
        token_key_id: str,
        now: datetime | None = None,
    ) -> PersistedSession:
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE internal_sessions
                SET status = 'active', source_token_envelope = ?,
                    source_token_key_id = ?, token_generation = token_generation + 1,
                    auth_owner = NULL, auth_lease_token = NULL,
                    auth_lease_expires_at = NULL, last_authenticated_at = ?,
                    last_auth_error_code = NULL, last_auth_error_message = NULL,
                    updated_at = ?
                WHERE session_hash = ? AND status = 'authenticating'
                  AND auth_owner = ? AND auth_lease_token = ?
                  AND auth_lease_expires_at > ?
                  AND expires_at > ?
                """,
                (
                    token_envelope,
                    token_key_id,
                    timestamp,
                    timestamp,
                    session_hash,
                    worker_id,
                    lease_token,
                    timestamp,
                    timestamp,
                ),
            )
            if cursor.rowcount != 1:
                raise AuthenticationLeaseLostError(
                    'stale authenticator cannot store a source token'
                )
            return self._get_session(connection, session_hash)

    def fail_authentication(
        self,
        session_hash: str,
        worker_id: str,
        lease_token: str,
        *,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> PersistedSession:
        current = self._resolve_now(now)
        timestamp = _timestamp(current)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE internal_sessions
                SET status = 'authentication_failed',
                    source_token_envelope = NULL, source_token_key_id = NULL,
                    auth_owner = NULL, auth_lease_token = NULL,
                    auth_lease_expires_at = NULL,
                    last_auth_error_code = ?, last_auth_error_message = ?,
                    updated_at = ?
                WHERE session_hash = ? AND status = 'authenticating'
                  AND auth_owner = ? AND auth_lease_token = ?
                  AND auth_lease_expires_at > ?
                  AND expires_at > ?
                """,
                (
                    _bounded(error_code, 80),
                    _bounded(error_message, 500),
                    timestamp,
                    session_hash,
                    worker_id,
                    lease_token,
                    timestamp,
                    timestamp,
                ),
            )
            if cursor.rowcount != 1:
                raise AuthenticationLeaseLostError(
                    'stale authenticator cannot store an authentication failure'
                )
            return self._get_session(connection, session_hash)

    def revoke_session(
        self,
        session_hash: str,
        *,
        now: datetime | None = None,
    ) -> PersistedSession:
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            self._lock_session(connection, session_hash)
            session = self._get_session(connection, session_hash)
            if session.status == SESSION_REVOKED:
                return session
            connection.execute(
                """
                UPDATE internal_sessions
                SET status = 'revoked', credential_envelope = NULL,
                    credential_key_id = NULL, source_token_envelope = NULL,
                    source_token_key_id = NULL, auth_owner = NULL,
                    auth_lease_token = NULL, auth_lease_expires_at = NULL,
                    revoked_at = ?, updated_at = ?
                WHERE session_hash = ?
                """,
                (timestamp, timestamp, session_hash),
            )
            return self._get_session(connection, session_hash)

    def expire_sessions(self, *, now: datetime | None = None) -> int:
        timestamp = _timestamp(self._resolve_now(now))
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE internal_sessions
                SET status = 'expired', credential_envelope = NULL,
                    credential_key_id = NULL, source_token_envelope = NULL,
                    source_token_key_id = NULL, auth_owner = NULL,
                    auth_lease_token = NULL, auth_lease_expires_at = NULL,
                    updated_at = ?
                WHERE expires_at <= ?
                  AND status IN ('pending', 'authenticating', 'active', 'authentication_failed')
                """,
                (timestamp, timestamp),
            )
            return cursor.rowcount

    def _expire_one(self, connection, session_hash: str, timestamp: str) -> None:
        connection.execute(
            """
            UPDATE internal_sessions
            SET status = 'expired', credential_envelope = NULL,
                credential_key_id = NULL, source_token_envelope = NULL,
                source_token_key_id = NULL, auth_owner = NULL,
                auth_lease_token = NULL, auth_lease_expires_at = NULL,
                updated_at = ?
            WHERE session_hash = ?
            """,
            (timestamp, session_hash),
        )

    def _get_session(self, connection, session_hash: str) -> PersistedSession:
        row = connection.execute(
            """
            SELECT session.*, account.account_key
            FROM internal_sessions AS session
            JOIN source_accounts AS account ON account.account_id = session.account_id
            WHERE session.session_hash = ?
            """,
            (session_hash,),
        ).fetchone()
        if row is None:
            raise SessionNotFoundError('internal session was not found')
        return _session_from_row(row)

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
    def _lock_account(connection, account_key: str) -> None:
        return None

    @staticmethod
    def _lock_session(connection, session_hash: str) -> None:
        return None


class SQLiteSessionRepository(_RelationalSessionRepository):
    """SQLite session adapter for local development and offline acceptance."""


def _session_from_row(row) -> PersistedSession:
    return PersistedSession(
        session_hash=row['session_hash'],
        account_id=row['account_id'],
        account_key=row['account_key'],
        status=row['status'],
        credential_envelope=row['credential_envelope'],
        credential_key_id=row['credential_key_id'],
        source_token_envelope=row['source_token_envelope'],
        source_token_key_id=row['source_token_key_id'],
        token_generation=int(row['token_generation']),
        auth_owner=row['auth_owner'],
        auth_lease_token=row['auth_lease_token'],
        auth_lease_generation=int(row['auth_lease_generation']),
        auth_lease_expires_at=_parse_time(row['auth_lease_expires_at']),
        created_at=_parse_time(row['created_at']),
        expires_at=_parse_time(row['expires_at']),
        revoked_at=_parse_time(row['revoked_at']),
        last_authenticated_at=_parse_time(row['last_authenticated_at']),
        last_auth_error_code=row['last_auth_error_code'],
        last_auth_error_message=row['last_auth_error_message'],
        updated_at=_parse_time(row['updated_at']),
        owner_id=row['owner_id'],
    )


def _normalize_account_key(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized:
        raise ValueError('account_key is required')
    if len(normalized) > 256:
        raise ValueError('account_key is too long')
    return normalized


def _validate_session_hash(value: str) -> None:
    if len(value) != 64 or any(character not in '0123456789abcdef' for character in value):
        raise ValueError('session_hash must be a lowercase SHA-256 hex digest')


def _validate_auth_request(worker_id: str, lease_seconds: int, generation: int) -> None:
    if not worker_id.strip():
        raise ValueError('worker_id is required')
    if lease_seconds < 2:
        raise ValueError('authentication lease_seconds must be at least 2')
    if generation < 0:
        raise ValueError('token generation cannot be negative')


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime:
    value = value or _utc_now()
    if value.tzinfo is None:
        raise ValueError('session timestamps must be timezone-aware')
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def _parse_time(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded(value: str, limit: int) -> str:
    return str(value).replace('\r', ' ').replace('\n', ' ')[:limit]


_SQLITE_SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS control_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_accounts (
    account_id TEXT PRIMARY KEY,
    account_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
    token_generation INTEGER NOT NULL DEFAULT 0,
    auth_owner TEXT,
    auth_lease_token TEXT,
    auth_lease_generation INTEGER NOT NULL DEFAULT 0,
    auth_lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    last_authenticated_at TEXT,
    last_auth_error_code TEXT,
    last_auth_error_message TEXT,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_internal_sessions_active_account
ON internal_sessions (account_id)
WHERE status IN ('pending', 'authenticating', 'active', 'authentication_failed');
CREATE INDEX IF NOT EXISTS idx_internal_sessions_expiry
ON internal_sessions (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_internal_sessions_auth_lease
ON internal_sessions (status, auth_lease_expires_at);
"""
