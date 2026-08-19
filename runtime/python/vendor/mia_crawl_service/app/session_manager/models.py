from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


SESSION_PENDING = 'pending'
SESSION_AUTHENTICATING = 'authenticating'
SESSION_ACTIVE = 'active'
SESSION_AUTHENTICATION_FAILED = 'authentication_failed'
SESSION_REVOKED = 'revoked'
SESSION_EXPIRED = 'expired'

ACTIVE_SESSION_STATUSES = (
    SESSION_PENDING,
    SESSION_AUTHENTICATING,
    SESSION_ACTIVE,
    SESSION_AUTHENTICATION_FAILED,
)
TERMINAL_SESSION_STATUSES = (SESSION_REVOKED, SESSION_EXPIRED)


class SessionManagerError(RuntimeError):
    pass


class SessionNotFoundError(SessionManagerError):
    pass


class ActiveSessionExistsError(SessionManagerError):
    pass


class SessionUnavailableError(SessionManagerError):
    pass


class AuthenticationFailedError(SessionManagerError):
    def __init__(
        self,
        error_code: str = 'authentication_failed',
        message: str = 'Source authentication failed',
        *,
        retryable: bool = False,
        retry_delay_seconds: int = 0,
    ) -> None:
        self.error_code = error_code
        self.code = error_code
        self.retryable = retryable
        self.retry_delay_seconds = retry_delay_seconds
        super().__init__(message)


class AuthenticationLeaseLostError(SessionManagerError):
    pass


class AuthenticationWaitTimeoutError(SessionManagerError):
    pass


@dataclass(frozen=True)
class CredentialBundle:
    account_key: str
    username: str
    password: str = field(repr=False)
    proxy_url: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.account_key.strip():
            raise ValueError('account_key is required')
        if not self.username.strip():
            raise ValueError('username is required')
        if not self.password:
            raise ValueError('password is required')


@dataclass(frozen=True)
class PersistedSession:
    session_hash: str
    account_id: str
    account_key: str
    status: str
    credential_envelope: str | None = field(repr=False)
    credential_key_id: str | None
    source_token_envelope: str | None = field(repr=False)
    source_token_key_id: str | None
    token_generation: int
    auth_owner: str | None
    auth_lease_token: str | None = field(repr=False)
    auth_lease_generation: int
    auth_lease_expires_at: datetime | None
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    last_authenticated_at: datetime | None
    last_auth_error_code: str | None
    last_auth_error_message: str | None = field(repr=False)
    updated_at: datetime
    owner_id: str | None = None


@dataclass(frozen=True)
class SessionHandle:
    internal_session_id: str = field(repr=False)
    account_id: str
    account_key: str
    status: str
    token_generation: int
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class TokenSnapshot:
    token: str = field(repr=False)
    generation: int


@dataclass(frozen=True)
class AuthenticationLease:
    acquired: bool
    session: PersistedSession
    worker_id: str | None = None
    lease_token: str | None = field(default=None, repr=False)

