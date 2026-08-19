from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence


@dataclass
class _Route:
    route_id: str
    proxy_url: str | None = field(repr=False)
    capacity: int = 1
    active_leases: dict[str, str] = field(default_factory=dict, repr=False)
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    quarantine_until: float = 0.0
    success_count: int = 0
    rate_limit_count: int = 0


@dataclass(frozen=True)
class ProxyLease:
    route_id: str
    proxy_url: str | None = field(repr=False)
    owner_id: str
    lease_token: str = field(repr=False)

    @property
    def uses_proxy(self) -> bool:
        return self.proxy_url is not None


class ProxyUnavailableError(RuntimeError):
    retryable = True
    code = 'proxy_route_unavailable'
    retry_delay_seconds = 5


class ProxyRegistry:
    """Process-local exclusive proxy leases with cooldown and quarantine."""

    def __init__(
        self,
        *,
        direct_capacity: int = 1,
        failure_cooldown_seconds: float = 20.0,
        quarantine_after_failures: int = 3,
        quarantine_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if direct_capacity < 1 or quarantine_after_failures < 1:
            raise ValueError('proxy capacities and thresholds must be positive')
        if failure_cooldown_seconds < 0 or quarantine_seconds < 0:
            raise ValueError('proxy cooldowns must not be negative')
        self.direct_capacity = direct_capacity
        self.failure_cooldown_seconds = failure_cooldown_seconds
        self.quarantine_after_failures = quarantine_after_failures
        self.quarantine_seconds = quarantine_seconds
        self.clock = clock
        self._routes_by_account: dict[str, list[_Route]] = {}
        self._condition = threading.Condition()

    def register(self, account_ref: str, routes: Sequence[str | None]) -> None:
        if not account_ref:
            raise ValueError('account_ref is required')
        normalized: list[str | None] = []
        for raw_route in routes:
            value = raw_route.strip() if isinstance(raw_route, str) else None
            if value not in normalized:
                normalized.append(value)
        if not normalized:
            normalized.append(None)
        with self._condition:
            existing = {route.route_id: route for route in self._routes_by_account.get(account_ref, [])}
            result: list[_Route] = []
            for value in normalized:
                route_id = _route_id(value)
                route_record = existing.get(route_id) or _Route(
                    route_id=route_id,
                    proxy_url=value,
                    capacity=self.direct_capacity if value is None else 1,
                )
                result.append(route_record)
            self._routes_by_account[account_ref] = result
            self._condition.notify_all()

    def acquire(
        self,
        account_ref: str,
        owner_id: str,
        *,
        wait_timeout_seconds: float = 30.0,
    ) -> ProxyLease:
        deadline = self.clock() + max(0, wait_timeout_seconds)
        with self._condition:
            while True:
                now = self.clock()
                routes = self._routes_by_account.get(account_ref, [])
                candidates = [
                    route for route in routes
                    if now >= route.cooldown_until
                    and now >= route.quarantine_until
                    and len(route.active_leases) < route.capacity
                ]
                if candidates:
                    route = min(
                        candidates,
                        key=lambda item: (len(item.active_leases), item.success_count, item.route_id),
                    )
                    token = secrets.token_urlsafe(18)
                    route.active_leases[token] = owner_id
                    return ProxyLease(route.route_id, route.proxy_url, owner_id, token)
                remaining = deadline - now
                if remaining <= 0:
                    raise ProxyUnavailableError('no healthy route lease is currently available')
                self._condition.wait(min(remaining, 0.25))

    def release(self, account_ref: str, lease: ProxyLease, *, outcome: str) -> None:
        with self._condition:
            route = self._find_route(account_ref, lease.route_id)
            if route.active_leases.get(lease.lease_token) != lease.owner_id:
                raise RuntimeError('proxy lease ownership was lost')
            del route.active_leases[lease.lease_token]
            now = self.clock()
            if outcome == 'success':
                route.success_count += 1
                route.consecutive_failures = 0
            elif outcome == 'rate_limited':
                route.rate_limit_count += 1
            elif outcome == 'proxy_failure':
                route.consecutive_failures += 1
                route.cooldown_until = max(
                    route.cooldown_until, now + self.failure_cooldown_seconds
                )
                if route.consecutive_failures >= self.quarantine_after_failures:
                    route.quarantine_until = max(
                        route.quarantine_until, now + self.quarantine_seconds
                    )
            elif outcome != 'neutral_failure':
                raise ValueError(f'unsupported proxy outcome: {outcome}')
            self._condition.notify_all()

    def snapshot(self, account_ref: str) -> tuple[dict[str, object], ...]:
        with self._condition:
            now = self.clock()
            return tuple({
                'route_id': route.route_id,
                'uses_proxy': route.proxy_url is not None,
                'active_leases': len(route.active_leases),
                'healthy': now >= route.cooldown_until and now >= route.quarantine_until,
                'consecutive_failures': route.consecutive_failures,
                'success_count': route.success_count,
                'rate_limit_count': route.rate_limit_count,
            } for route in self._routes_by_account.get(account_ref, []))

    def _find_route(self, account_ref: str, route_id: str) -> _Route:
        for route in self._routes_by_account.get(account_ref, []):
            if route.route_id == route_id:
                return route
        raise RuntimeError('proxy route no longer exists')


class EndpointCooldowns:
    """Coordinate account/endpoint cooldown after exhausted HTTP 429 responses."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._deadlines: dict[tuple[str, str], float] = {}
        self._condition = threading.Condition()

    def wait(self, account_ref: str, endpoint: str) -> None:
        key = (account_ref, endpoint)
        with self._condition:
            while True:
                remaining = self._deadlines.get(key, 0.0) - self.clock()
                if remaining <= 0:
                    return
                self._condition.wait(min(remaining, 0.5))

    def defer(self, account_ref: str, endpoint: str, seconds: float) -> None:
        if seconds < 0:
            raise ValueError('cooldown must not be negative')
        with self._condition:
            key = (account_ref, endpoint)
            self._deadlines[key] = max(
                self._deadlines.get(key, 0.0), self.clock() + seconds
            )
            self._condition.notify_all()

    def remaining(self, account_ref: str, endpoint: str) -> float:
        with self._condition:
            return max(0.0, self._deadlines.get((account_ref, endpoint), 0.0) - self.clock())


def _route_id(proxy_url: str | None) -> str:
    if proxy_url is None:
        return 'direct'
    return 'proxy-' + hashlib.sha256(proxy_url.encode('utf-8')).hexdigest()[:12]
