from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.session_manager.models import (
    AuthenticationLease,
    PersistedSession,
    TokenSnapshot,
)


class SessionRepository(Protocol):
    def migrate(self) -> None: ...

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
    ) -> PersistedSession: ...

    def get_session(self, session_hash: str) -> PersistedSession: ...

    def claim_authentication(
        self,
        session_hash: str,
        worker_id: str,
        *,
        expected_generation: int,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AuthenticationLease: ...

    def renew_authentication_lease(
        self,
        session_hash: str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> PersistedSession: ...

    def complete_authentication(
        self,
        session_hash: str,
        worker_id: str,
        lease_token: str,
        *,
        token_envelope: str,
        token_key_id: str,
        now: datetime | None = None,
    ) -> PersistedSession: ...

    def fail_authentication(
        self,
        session_hash: str,
        worker_id: str,
        lease_token: str,
        *,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> PersistedSession: ...

    def revoke_session(
        self,
        session_hash: str,
        *,
        now: datetime | None = None,
    ) -> PersistedSession: ...

    def expire_sessions(self, *, now: datetime | None = None) -> int: ...


class BoundTokenProvider(Protocol):
    def get_token(self) -> TokenSnapshot: ...

    def refresh_after_unauthorized(self, rejected_generation: int) -> TokenSnapshot: ...

