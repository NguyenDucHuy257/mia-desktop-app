from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


CONNECTION_PENDING = 'pending'
CONNECTION_READY = 'ready'
CONNECTION_AUTH_FAILED = 'auth_failed'
CONNECTION_SUSPENDED = 'suspended'
CONNECTION_REVOKED = 'revoked'
CONNECTION_STATUSES = (
    CONNECTION_PENDING,
    CONNECTION_READY,
    CONNECTION_AUTH_FAILED,
    CONNECTION_SUSPENDED,
    CONNECTION_REVOKED,
)


@dataclass(frozen=True)
class AccountConnection:
    connection_id: str
    owner_id: str
    username: str
    session_hash: str
    status: str
    token_generation: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.connection_id.strip():
            raise ValueError('connection_id is required')
        if not self.owner_id.strip():
            raise ValueError('owner_id is required')
        if not self.username.strip():
            raise ValueError('username is required')
        if len(self.session_hash) != 64:
            raise ValueError('session_hash must be a SHA-256 digest')
        if self.status not in CONNECTION_STATUSES:
            raise ValueError(f'unsupported connection status: {self.status}')
