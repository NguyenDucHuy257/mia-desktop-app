from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import requests

from app.config.crawl_config import (
    RATE_LIMIT_ATTEMPTS,
    RATE_LIMIT_BACKOFF_MS,
    mask_secret,
    parse_proxy_list,
)
from app.crawlers.auth_crawler import AuthCrawler
from app.crawlers.diagnostics import emit
from app.crawlers.endpoints import GET_COMPANY_INFO_API
from app.crawlers.web_client import PORTAL_ROOT_URL, WebClient
from app.services.rate_limit_diagnostics import (
    RateLimitEvidenceLog,
    decode_jwt_claims,
    describe_rate_limit_response,
)
from app.session_manager.contracts import BoundTokenProvider

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = (
    2.0, 5.0, 10.0, 20.0, 40.0,
    60.0, 80.0, 100.0, 120.0, 140.0,
)

class TaxPortalSession:
    """Sequential session with a fixed route; refresh tokens only after HTTP 401."""

    RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        token_provider: BoundTokenProvider | None = None,
        proxy: str | None = None,
        proxies: Sequence[str] | None = None,
        user_agent: str | None = None,
        timeout: float | tuple[float, float] = (15, 75),
        max_retries: int = 3,
        backoff_seconds: float = 1.5,
        backoff_max_seconds: float = 8.0,
        rate_limit_delay_step: float = 2.0,
        rate_limit_max_delay: float = 40.0,
        rate_limit_backoff_seconds: Sequence[float] | None = None,
        rate_limit_attempts: int = RATE_LIMIT_ATTEMPTS,
        crawl_profile: str = 'fast_balanced',
        client: WebClient | None = None,
        auth: AuthCrawler | None = None,
        evidence_log: RateLimitEvidenceLog | None = None,
        account_label: str | None = None,
        interruption_check: Callable[[], None] | None = None,
    ) -> None:
        if token_provider is None and (not username or not password):
            raise ValueError('username/password or token_provider is required')
        self.username = username
        self.password = password
        self.token_provider = token_provider
        self.user_agent = user_agent or (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/152.0.0.0 Safari/537.36'
        )
        if isinstance(proxies, str):
            configured_proxies = parse_proxy_list(proxies)
        else:
            configured_proxies = proxies if proxies is not None else ((proxy,) if proxy else ())
        self.proxy_pool = tuple(item.strip() for item in configured_proxies if item and item.strip())
        self.proxy_index = 0
        initial_proxy = self.proxy_pool[0] if self.proxy_pool else None
        self.client = client or WebClient(timeout=timeout, proxy=initial_proxy)
        if client is not None and initial_proxy is not None:
            self.client.set_proxy(initial_proxy)
        self.auth = auth or (
            AuthCrawler(self.client) if token_provider is None else None
        )
        self.max_retries = max(1, max_retries)
        self.backoff_seconds = max(0, backoff_seconds)
        self.backoff_max_seconds = max(0, backoff_max_seconds)
        self.rate_limit_delay_step = max(0, rate_limit_delay_step)
        self.rate_limit_max_delay = max(0, rate_limit_max_delay)
        self.rate_limit_backoff_seconds = tuple(
            rate_limit_backoff_seconds or DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
        )
        if any(delay <= 0 for delay in self.rate_limit_backoff_seconds):
            raise ValueError('rate_limit_backoff_seconds values must be positive')
        if rate_limit_attempts < 1:
            raise ValueError('rate_limit_attempts must be at least 1')
        self.rate_limit_attempts = rate_limit_attempts
        self.crawl_profile = crawl_profile
        # Optional 429 forensics. Off by default so normal crawls are unchanged.
        self.evidence_log = evidence_log
        # A stable, non-secret label ("A"/"B") used to compare accounts in the
        # evidence file without ever writing the username to disk.
        self.account_label = account_label
        self.interruption_check = interruption_check
        self.token: str | None = None
        self.token_generation = 0
        self.company_info: dict[str, str] | None = None

    @property
    def current_proxy_number(self) -> int | None:
        """One-based proxy position for safe logging without leaking credentials."""
        return self.proxy_index + 1 if self.proxy_pool else None

    @property
    def current_route_label(self) -> str:
        """Credential-free label used to compare direct and proxy routes in logs."""
        proxy_number = self.current_proxy_number
        if proxy_number is None:
            return 'direct'
        return f'proxy {proxy_number}/{len(self.proxy_pool)}'

    def rotate_proxy(self) -> bool:
        """Move to the next direct/proxy route, wrapping at the end."""
        if not self.proxy_pool:
            return False
        previous_label = self.current_route_label
        self.proxy_index = (self.proxy_index + 1) % len(self.proxy_pool)
        self.client.set_proxy(self.proxy_pool[self.proxy_index])
        logger.info(
            'Manually switched route %s -> %s',
            previous_label,
            self.current_route_label,
        )
        return True

    @property
    def headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError('Not authenticated')
        return self.client.build_headers(authorization=self.token, ua=self.user_agent)

    @property
    def authentication_headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError('Not authenticated')
        return self.client.build_headers(
            authorization=self.token,
            endpoint='/',
            ua=self.user_agent,
            referer=PORTAL_ROOT_URL,
            action='',
        )

    def login(self) -> str:
        if self.token_provider is not None:
            snapshot = self.token_provider.get_token()
            self.token = snapshot.token
            self.token_generation = snapshot.generation
        else:
            if self.auth is None:
                raise RuntimeError('legacy authentication is not configured')
            self.token = self.auth.authenticate(
                username=self.username or '',
                password=self.password or '',
                ua=self.user_agent,
            )
        logger.info(
            'Authenticated tax account %s via route %s token_generation=%d',
            self.account_label or mask_secret(self.username or 'managed'),
            self.current_route_label,
            self.token_generation,
        )
        if self.evidence_log is not None:
            self._record_token_claims()
        return self.token

    def get_company_info(self) -> dict[str, str]:
        if self.auth is not None:
            self.company_info = self.auth.get_company_info(self.authentication_headers)
            return self.company_info
        response = self.get(GET_COMPANY_INFO_API, headers=self.authentication_headers)
        try:
            payload = response.json()
        except (requests.JSONDecodeError, ValueError) as error:
            raise RuntimeError('Tax portal returned invalid company info JSON') from error
        if not isinstance(payload, dict) or not isinstance(payload.get('name'), str):
            raise RuntimeError('Tax portal returned invalid company info object')
        self.company_info = {'name': payload['name']}
        return self.company_info

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request('get', url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        if not self.token:
            self.login()

        extra_headers = dict(kwargs.pop('headers', {}) or {})
        retry_override = kwargs.pop('retry_attempts', None)
        request_retries = (
            self.max_retries
            if retry_override is None
            else max(1, int(retry_override))
        )
        # Kept for call-site compatibility. HTTP 429 no longer has a terminal
        # retry budget; the request waits and resumes until it succeeds or the
        # worker interruption callback raises.
        kwargs.pop('rate_limit_attempts', None)
        interruption_check = kwargs.pop(
            'interruption_check', self.interruption_check
        )
        last_error: Exception | None = None
        normal_failures = 0
        rate_limit_failures = 0

        while True:
            if interruption_check is not None:
                interruption_check()
            rate_limited = False
            headers = self.headers
            headers.update({
                key: value
                for key, value in extra_headers.items()
                if key.lower() != 'authorization'
            })
            try:
                response = getattr(self.client, method)(
                    url, headers=headers, **kwargs
                )
                if rate_limit_failures:
                    logger.warning(
                        'RATE_LIMIT_RECOVERED endpoint=%s route=%s '
                        'consecutive_429=%d previous_cooldown_ms=%d',
                        url,
                        self.current_route_label,
                        rate_limit_failures,
                        round(self._rate_limit_wait_seconds(rate_limit_failures) * 1000),
                    )
                return response
            except requests.HTTPError as error:
                last_error = error
                status = (
                    error.response.status_code
                    if error.response is not None else None
                )
                if status == 429:
                    rate_limited = True
                    rate_limit_failures += 1
                    response_headers = (
                        error.response.headers if error.response is not None else {}
                    )
                    retry_after = response_headers.get('Retry-After')
                    rate_limit_reset = (
                        response_headers.get('RateLimit-Reset')
                        or response_headers.get('X-RateLimit-Reset')
                        or response_headers.get('X-Rate-Limit-Reset')
                    )
                    logger.warning(
                        'HTTP 429 on fixed route %s; keeping route and token '
                        'retry_after=%s rate_limit_reset=%s',
                        self.current_route_label,
                        retry_after or '-',
                        rate_limit_reset or '-',
                    )
                    self._record_rate_limit_evidence(
                        error.response,
                        url,
                        rate_limit_failures,
                        0,
                    )
                elif status == 401:
                    normal_failures += 1
                    emit('source_unauthorized', generation=self.token_generation,
                         normal_failures=normal_failures, retry_limit=request_retries)
                    if normal_failures >= request_retries:
                        raise
                    logger.warning(
                        'Token generation %d rejected; refreshing before retry',
                        self.token_generation,
                    )
                    if self.token_provider is not None:
                        snapshot = self.token_provider.refresh_after_unauthorized(
                            self.token_generation
                        )
                        self.token = snapshot.token
                        self.token_generation = snapshot.generation
                    else:
                        self.login()
                elif status in self.RETRYABLE_STATUS:
                    if self._is_permanent_invoice_export_error(error.response):
                        logger.warning(
                            'Invoice package is unavailable on the tax portal; '
                            'not retrying a permanent HTTP %s response',
                            status,
                        )
                        raise
                    normal_failures += 1
                    if normal_failures >= request_retries:
                        break
                else:
                    raise
            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = error
                normal_failures += 1
                logger.warning(
                    'Network %s on route %s attempt %d/%d for %s; %s',
                    type(error).__name__,
                    self.current_route_label,
                    normal_failures,
                    request_retries,
                    url,
                    'retrying with the same token/route'
                    if normal_failures < request_retries
                    else 'retry attempts exhausted',
                )
                if normal_failures >= request_retries:
                    break

            if rate_limited:
                wait_seconds = self._rate_limit_wait_seconds(
                    rate_limit_failures
                )
                emit('rate_limit_wait', consecutive_429=rate_limit_failures,
                     cooldown_seconds=wait_seconds, generation=self.token_generation)
                logger.warning(
                    'Rate limited endpoint=%s status_code=429 attempt=%d '
                    'cooldown_ms=%d profile=%s terminal=false',
                    url,
                    rate_limit_failures,
                    round(wait_seconds * 1000),
                    self.crawl_profile,
                )
                self._interruptible_sleep(
                    wait_seconds,
                    interruption_check,
                )
                continue

            wait_seconds = min(
                self.backoff_seconds * (2 ** (normal_failures - 1)),
                self.backoff_max_seconds,
            )
            self._interruptible_sleep(wait_seconds, interruption_check)

        response = getattr(last_error, 'response', None)
        status_code = getattr(response, 'status_code', None)
        last_error_name = (
            type(last_error).__name__ if last_error is not None else 'unknown'
        )
        status_text = (
            f' HTTP {status_code}' if status_code is not None else ''
        )
        raise RuntimeError(
            f'Tax API failed after {request_retries} attempts: '
            f'{last_error_name}{status_text}: {url}'
        ) from last_error

    def _rate_limit_wait_seconds(self, failure_count: int) -> float:
        if failure_count < 1:
            raise ValueError('failure_count must be positive')
        index = min(
            failure_count - 1,
            len(self.rate_limit_backoff_seconds) - 1,
        )
        return min(float(self.rate_limit_backoff_seconds[index]), 300.0)

    @staticmethod
    def _interruptible_sleep(
        wait_seconds: float,
        interruption_check: Callable[[], None] | None,
    ) -> None:
        remaining = max(0.0, float(wait_seconds))
        if interruption_check is None:
            time.sleep(remaining)
            return
        while remaining > 0:
            interruption_check()
            sleep_slice = min(1.0, remaining)
            time.sleep(sleep_slice)
            remaining -= sleep_slice
        interruption_check()

    def _record_token_claims(self) -> None:
        """Log which claims the portal binds into a fresh token."""
        if self.evidence_log is None or not self.token:
            return
        try:
            claims = decode_jwt_claims(self.token)
        except ValueError as error:
            logger.debug('Access token is not a decodable JWT: %s', error)
            return
        self.evidence_log.record({
            'event': 'token_issued',
            'account_label': self.account_label,
            'route_label': self.current_route_label,
            'claims': claims,
        })

    def _record_rate_limit_evidence(
        self,
        response: requests.Response | None,
        url: str,
        attempt: int,
        max_attempts: int,
    ) -> None:
        """Persist one 429 observation; never break the crawl if it fails."""
        if self.evidence_log is None or response is None:
            return
        try:
            event = describe_rate_limit_response(
                response,
                endpoint=url,
                attempt=attempt,
                max_attempts=max_attempts,
                route_label=self.current_route_label,
                crawl_profile=self.crawl_profile,
                account_label=self.account_label,
            )
            event['event'] = 'rate_limited'
            self.evidence_log.record(event)
        except Exception:
            logger.exception('Could not record rate-limit evidence')

    @staticmethod
    def _is_permanent_invoice_export_error(
        response: requests.Response | None,
    ) -> bool:
        """Recognize export errors that repeating the same request cannot fix."""
        if response is None or response.status_code != 500:
            return False
        if '/invoices/export-xml' not in (response.url or ''):
            return False
        try:
            payload = response.json()
        except (requests.JSONDecodeError, ValueError):
            return False
        message = payload.get('message') if isinstance(payload, dict) else None
        return message == 'Không tồn tại hồ sơ gốc của hóa đơn.'
