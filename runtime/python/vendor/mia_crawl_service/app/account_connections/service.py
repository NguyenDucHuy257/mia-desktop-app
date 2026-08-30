from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.account_connections.models import AccountConnection
from app.account_connections.repository import (
    AccountConnectionNotFoundError,
    AccountConnectionRepository,
    new_connection_id,
)
from app.session_manager.crypto import SessionCipher
from app.session_manager.models import (
    ActiveSessionExistsError,
    CredentialBundle,
)
from app.session_manager.service import (
    SessionTokenManager,
    hash_internal_session_id,
)


DEFAULT_CONNECTION_TTL_SECONDS = 30 * 24 * 60 * 60


class AccountConnectionManager:
    """Create and recover stable account connections over encrypted auth state."""

    def __init__(
        self,
        repository: AccountConnectionRepository,
        session_manager: SessionTokenManager,
        cipher: SessionCipher,
        *,
        connection_ttl_seconds: int | None = None,
    ) -> None:
        ttl = connection_ttl_seconds
        if ttl is None:
            ttl = int(os.getenv(
                'MIA_ACCOUNT_CONNECTION_TTL_SECONDS',
                str(DEFAULT_CONNECTION_TTL_SECONDS),
            ))
        if ttl < 60:
            raise ValueError('account connection TTL must be at least 60 seconds')
        self.repository = repository
        self.session_manager = session_manager
        self.cipher = cipher
        self.connection_ttl_seconds = ttl

    def initialize(self) -> None:
        # The connection table references internal_sessions, so auth-state
        # migration must be applied first on both SQLite and PostgreSQL.
        self.session_manager.initialize()
        self.repository.migrate()

    def create(
        self,
        *,
        username: str,
        password: str,
        owner_id: str,
    ) -> tuple[AccountConnection, bool]:
        normalized = _normalize_username(username)
        if not password:
            raise ValueError('password is required')
        # Reconcile durable expiry before deciding whether an existing
        # connection can be reused. Without this sweep, an expired source
        # session can still look ready to the public connection query.
        self.session_manager.repository.expire_sessions()
        existing = self.repository.get_by_username(normalized, owner_id=owner_id)
        if existing is not None:
            if existing.status in {'auth_failed', 'suspended'}:
                return self.reconnect(
                    existing.connection_id,
                    username=normalized,
                    password=password,
                    owner_id=owner_id,
                ), True
            return existing, True

        legacy_hash = self.repository.find_unmapped_session_hash(
            normalized, owner_id=owner_id
        )
        if legacy_hash is not None:
            return self.repository.bind(
                connection_id=new_connection_id(),
                owner_id=owner_id,
                username=normalized,
                session_hash=legacy_hash,
            ), True

        credentials = CredentialBundle(
            account_key=normalized,
            username=normalized,
            password=password,
            proxy_url=None,
        )
        try:
            handle = self.session_manager.create_session(
                credentials,
                ttl_seconds=self.connection_ttl_seconds,
                owner_id=owner_id,
            )
        except ActiveSessionExistsError:
            # A legacy request or concurrent create may have inserted auth state
            # before it was mapped to a stable connection. Recover it instead of
            # exposing the orphan-session conflict to Server A.
            legacy_hash = self.repository.find_unmapped_session_hash(
                normalized, owner_id=owner_id
            )
            if legacy_hash is None:
                existing = self.repository.get_by_username(
                    normalized, owner_id=owner_id
                )
                if existing is not None:
                    return existing, True
                raise
            return self.repository.bind(
                connection_id=new_connection_id(),
                owner_id=owner_id,
                username=normalized,
                session_hash=legacy_hash,
            ), True

        connection = self.repository.bind(
            connection_id=new_connection_id(),
            owner_id=owner_id,
            username=normalized,
            session_hash=hash_internal_session_id(handle.internal_session_id),
        )
        return connection, False

    def get(self, connection_id: str, *, owner_id: str) -> AccountConnection:
        return self.repository.get(connection_id, owner_id=owner_id)

    def session_hash(
        self, connection_id: str, *, owner_id: str
    ) -> tuple[AccountConnection, str]:
        connection = self.get(connection_id, owner_id=owner_id)
        if connection.status in {'revoked', 'suspended'}:
            raise AccountConnectionNotFoundError(
                'account connection is not available'
            )
        return connection, connection.session_hash

    def reconnect(
        self,
        connection_id: str,
        *,
        username: str,
        password: str,
        owner_id: str,
    ) -> AccountConnection:
        connection = self.get(connection_id, owner_id=owner_id)
        normalized = _normalize_username(username)
        if normalized != connection.username:
            raise ValueError('username must match the existing account connection')
        if not password:
            raise ValueError('password is required')
        credentials = CredentialBundle(
            account_key=normalized,
            username=normalized,
            password=password,
            proxy_url=None,
        )
        envelope = self.cipher.encrypt_credentials(
            credentials, subject=connection.session_hash
        )
        return self.repository.reconnect(
            connection_id,
            owner_id=owner_id,
            credential_envelope=envelope,
            credential_key_id=self.cipher.key_id,
            expires_at=(
                datetime.now(timezone.utc)
                + timedelta(seconds=self.connection_ttl_seconds)
            ),
        )

    def revoke(self, connection_id: str, *, owner_id: str) -> AccountConnection:
        return self.repository.revoke(connection_id, owner_id=owner_id)


def _normalize_username(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError('username is required')
    if len(normalized) > 256:
        raise ValueError('username is too long')
    return normalized
