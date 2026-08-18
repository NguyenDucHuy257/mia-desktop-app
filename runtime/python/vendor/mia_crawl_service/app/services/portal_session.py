from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

import requests

from app.config.crawl_config import (
    RATE_LIMIT_ATTEMPTS,
    RATE_LIMIT_BACKOFF_MS,
    parse_proxy_list,
)
from app.crawlers.auth_crawler import AuthCrawler
from app.crawlers.web_client import WebClient
from app.services.rate_limit_diagnostics import (
    RateLimitEvidenceLog,
    decode_jwt_claims,
    describe_rate_limit_response,
)

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = tuple(
    delay / 1000 for delay in RATE_LIMIT_BACKOFF_MS
)

class TaxPortalSession:
    """Sequential session with a fixed route; refresh tokens only after HTTP 401."""

    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        username: str,
        password: str,
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
    ) -> None:
        self.username = username
        self.password = password
        self.user_agent = user_agent or (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/149.0.0.0 Safari/537.36'
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
        self.auth = auth or AuthCrawler(self.client)
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
        self.token: str | None = None
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

    def login(self) -> str:
        self.token = self.auth.authenticate(
            username=self.username,
            password=self.password,
            ua=self.user_agent,
        )
        logger.info(
            'Authenticated tax account %s via route %s',
            self.username,
            self.current_route_label,
        )
        if self.evidence_log is not None:
            self._record_token_claims()
        return self.token

    def get_company_info(self) -> dict[str, str]:
        self.company_info = self.auth.get_company_info(self.headers)
        return self.company_info

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self._request('get', url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        if not self.token:
            self.login()

        extra_headers = dict(kwargs.pop('headers', {}) or {})
        retry_override = kwargs.pop('retry_attempts', None)
        request_retries = self.max_retries if retry_override is None else max(1, int(retry_override))
        rate_limit_override = kwargs.pop('rate_limit_attempts', None)
        request_rate_limit_attempts = (
            self.rate_limit_attempts
            if rate_limit_override is None
            else max(1, int(rate_limit_override))
        )
        last_error: Exception | None = None
        normal_failures = 0
        rate_limit_failures = 0

        # 429 has its own budget and does not consume ordinary network/5xx
        # retries. The local counters reset naturally after any successful call.
        max_total_failures = request_retries + request_rate_limit_attempts - 1
        for _ in range(max_total_failures):
            rate_limited = False
            headers = self.headers
            headers.update({k: v for k, v in extra_headers.items() if k.lower() != 'authorization'})
            try:
                return getattr(self.client, method)(url, headers=headers, **kwargs)
            except requests.HTTPError as error:
                last_error = error
                status = error.response.status_code if error.response is not None else None
                if status == 429:
                    rate_limited = True
                    rate_limit_failures += 1
                    logger.warning(
                        'HTTP 429 on fixed route %s; keeping route and token',
                        self.current_route_label,
                    )
                    self._record_rate_limit_evidence(
                        error.response,
                        url,
                        rate_limit_failures,
                        request_rate_limit_attempts,
                    )
                    if rate_limit_failures >= request_rate_limit_attempts:
                        logger.error(
                            'Rate limited endpoint=%s status_code=429 attempt=%d/%d '
                            'cooldown_ms=0 profile=%s; retries exhausted',
                            url,
                            rate_limit_failures,
                            request_rate_limit_attempts,
                            self.crawl_profile,
                        )
                        break
                elif status == 401:
                    normal_failures += 1
                    if normal_failures >= request_retries:
                        raise
                    logger.warning('Token rejected; logging in again before retry')
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
                if self.rate_limit_backoff_seconds:
                    backoff_index = min(
                        rate_limit_failures - 1,
                        len(self.rate_limit_backoff_seconds) - 1,
                    )
                    wait_seconds = self.rate_limit_backoff_seconds[backoff_index]
                else:
                    wait_seconds = min(
                        self.rate_limit_delay_step * rate_limit_failures,
                        self.rate_limit_max_delay,
                    )
                logger.warning(
                    'Rate limited endpoint=%s status_code=429 attempt=%d/%d '
                    'cooldown_ms=%d profile=%s',
                    url,
                    rate_limit_failures,
                    request_rate_limit_attempts,
                    round(wait_seconds * 1000),
                    self.crawl_profile,
                )
            else:
                wait_seconds = min(
                    self.backoff_seconds * (2 ** (normal_failures - 1)),
                    self.backoff_max_seconds,
                )
            time.sleep(wait_seconds)

        response = getattr(last_error, 'response', None)
        status_code = getattr(response, 'status_code', None)
        last_error_name = type(last_error).__name__ if last_error is not None else 'unknown'
        status_text = f' HTTP {status_code}' if status_code is not None else ''
        raise RuntimeError(
            f'Tax API failed after {request_retries} attempts: '
            f'{last_error_name}{status_text}: {url}'
        ) from last_error

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
