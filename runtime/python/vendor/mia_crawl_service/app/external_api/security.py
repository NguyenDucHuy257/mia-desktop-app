from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from app.external_api.config import ExternalApiSettings


@dataclass(frozen=True)
class ServicePrincipal:
    caller_id: str
    key_id: str


class ServiceAuthenticator:
    def __init__(self, settings: ExternalApiSettings) -> None:
        self.settings = settings

    def authenticate(self, supplied: str | None) -> ServicePrincipal:
        if not supplied:
            raise HTTPException(status_code=401, detail='service authentication required')
        matched_key_id = None
        for key_id, expected in self.settings.api_keys.items():
            if hmac.compare_digest(supplied.encode(), expected.encode()):
                matched_key_id = key_id
        if matched_key_id is None:
            raise HTTPException(status_code=401, detail='invalid service credential')
        return ServicePrincipal(self.settings.caller_id, matched_key_id)


class FixedWindowRateLimiter:
    def __init__(self, limit: int, *, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, identity: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events[identity]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


def build_service_dependency(
    authenticator: ServiceAuthenticator,
    limiter: FixedWindowRateLimiter,
):
    def dependency(
        request: Request,
        api_key: str | None = Header(default=None, alias='X-MIA-API-Key'),
    ) -> ServicePrincipal:
        principal = authenticator.authenticate(api_key)
        if not limiter.allow(f'{principal.caller_id}:{principal.key_id}'):
            raise HTTPException(status_code=429, detail='service rate limit exceeded')
        request.state.service_principal = principal
        return principal

    return dependency


def hash_remote_address(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:16]
