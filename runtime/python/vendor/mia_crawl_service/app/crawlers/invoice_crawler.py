from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable
import logging
import math
import random
import time

import requests

from app.config.crawl_config import (
    RATE_LIMIT_ATTEMPTS,
    RATE_LIMIT_BACKOFF_MS,
    OverviewCrawlConfig,
    category_to_query_type,
)
from app.crawlers.endpoints import invoice_export_url, invoice_list_url
from app.crawlers.web_client import WebClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageAuditEntry:
    page: int
    size: int
    expected_received: int | None
    received: int
    fetched_before: int
    fetched_after: int
    total: int | None
    state: str | None
    http_status: int
    retry_count: int
    timeout_count: int
    note: str = ''


@dataclass(frozen=True)
class SuspiciousPage:
    page: int
    reason: str
    expected_received: int | None
    received: int
    http_status: int | None
    note: str = ''


@dataclass(frozen=True)
class InvoiceFetchResult:
    records: list[dict[str, Any]]
    first_page_total: int | None
    pages: int
    final_page_size: int
    final_status: str = 'completed'
    final_state: str | None = None
    estimated_pages: int = 0
    range_class: str = 'light'
    page_details: tuple[PageAuditEntry, ...] = ()
    suspicious_pages: tuple[SuspiciousPage, ...] = ()
    retry_count: int = 0
    timeout_count: int = 0
    consecutive_429_count: int = 0
    checkpoint_state: str | None = None
    error_message: str | None = None

    @property
    def missing_count(self) -> int:
        if self.first_page_total is None:
            return 0
        return max(self.first_page_total - len(self.records), 0)

    @property
    def needs_audit(self) -> bool:
        return (
            self.final_status != 'completed'
            or self.retry_count > 0
            or self.timeout_count > 0
            or bool(self.suspicious_pages)
            or len(self.records) != self.first_page_total
        )


@dataclass(frozen=True)
class AdaptivePagingOptions:
    """Reusable policy for state-cursor invoice pagination."""

    page_schedule: tuple[tuple[int, float], ...] = (
        (50, 5.0),
        (30, 3.0),
        (15, 1.0),
    )
    # Optional per-size attempt limits. Unlisted sizes use one attempt,
    # except the smallest size which falls back to minimum_size_attempts.
    page_size_attempts: tuple[tuple[int, int], ...] = ()
    connect_timeout: float = 3.0
    minimum_size_attempts: int = 20
    same_size_delay_step: float = 0.5
    same_size_delay_cap: float = 2.0
    successful_page_delay: float = 0.0
    authentication_attempts: int = 3
    rate_limit_attempts: int = RATE_LIMIT_ATTEMPTS
    rate_limit_base_delay: float = 5.0
    rate_limit_max_delay: float = 30.0
    rate_limit_backoff_ms: tuple[int, ...] = RATE_LIMIT_BACKOFF_MS
    profile: str = 'fast_balanced'
    min_start_gap_ms: int = 0
    min_idle_gap_ms: int = 0
    jitter_ms: tuple[int, int] = (0, 0)
    pause_every_success: int = 0
    pause_ms: tuple[int, int] = (0, 0)
    adaptive_throttle_enabled: bool = False
    page_sleep_enabled: bool = False
    page_sleep_every_pages: int = 50
    page_sleep_ms: tuple[int, int] = (30000, 40000)
    heavy_range_page_threshold: int = 100
    heavy_range_row_threshold: int = 5000
    heavy_range_sleep_after_done_ms: tuple[int, int] = (60000, 90000)
    extreme_range_page_threshold: int = 150
    extreme_range_row_threshold: int = 7500
    extreme_page_sleep_every_pages: int = 30
    extreme_page_sleep_ms: tuple[int, int] = (30000, 45000)
    extreme_range_sleep_after_done_ms: tuple[int, int] = (90000, 180000)
    continue_on_incomplete: bool = False
    continue_on_rate_limited: bool = False
    write_page_audit_report: bool = True
    incomplete_report_dir_name: str = 'overview_audit'
    page_size_trace_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.page_schedule:
            raise ValueError('page_schedule must not be empty')
        if any(size <= 0 or timeout <= 0 for size, timeout in self.page_schedule):
            raise ValueError('page sizes and timeouts must be positive')
        schedule_sizes = {size for size, _ in self.page_schedule}
        configured_sizes = [size for size, _ in self.page_size_attempts]
        if len(configured_sizes) != len(set(configured_sizes)):
            raise ValueError('page_size_attempts must not contain duplicate sizes')
        if any(size not in schedule_sizes for size in configured_sizes):
            raise ValueError('page_size_attempts contains a size not present in page_schedule')
        if any(attempts < 1 for _, attempts in self.page_size_attempts):
            raise ValueError('page-size attempts must be at least 1')
        if self.connect_timeout <= 0:
            raise ValueError('connect_timeout must be positive')
        if (
            self.minimum_size_attempts < 1
            or self.authentication_attempts < 1
            or self.rate_limit_attempts < 1
        ):
            raise ValueError('retry attempts must be at least 1')
        if (
            self.same_size_delay_step < 0
            or self.same_size_delay_cap < 0
            or self.successful_page_delay < 0
        ):
            raise ValueError('retry delays must not be negative')
        if any(
            isinstance(delay, bool) or not isinstance(delay, int) or delay <= 0
            for delay in self.rate_limit_backoff_ms
        ):
            raise ValueError('rate_limit_backoff_ms values must be positive integers')
        if self.min_start_gap_ms < 0 or self.min_idle_gap_ms < 0:
            raise ValueError('rate-limit gaps must not be negative')
        if (
            len(self.jitter_ms) != 2
            or self.jitter_ms[0] < 0
            or self.jitter_ms[0] > self.jitter_ms[1]
        ):
            raise ValueError('jitter_ms must be an ordered non-negative pair')
        if self.pause_every_success < 0:
            raise ValueError('pause_every_success must not be negative')
        if (
            len(self.pause_ms) != 2
            or self.pause_ms[0] < 0
            or self.pause_ms[0] > self.pause_ms[1]
        ):
            raise ValueError('pause_ms must be an ordered non-negative pair')
        for field_name in (
            'adaptive_throttle_enabled',
            'page_sleep_enabled',
            'continue_on_incomplete',
            'continue_on_rate_limited',
            'write_page_audit_report',
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f'{field_name} must be a bool')
        for field_name in (
            'page_sleep_every_pages',
            'heavy_range_page_threshold',
            'heavy_range_row_threshold',
            'extreme_range_page_threshold',
            'extreme_range_row_threshold',
            'extreme_page_sleep_every_pages',
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f'{field_name} must be a positive integer')
        for field_name in (
            'page_sleep_ms',
            'heavy_range_sleep_after_done_ms',
            'extreme_page_sleep_ms',
            'extreme_range_sleep_after_done_ms',
        ):
            value = getattr(self, field_name)
            if (
                len(value) != 2
                or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
                or value[0] < 0
                or value[0] > value[1]
            ):
                raise ValueError(f'{field_name} must be an ordered non-negative integer pair')
        if self.extreme_range_page_threshold < self.heavy_range_page_threshold:
            raise ValueError('extreme page threshold must be at least the heavy threshold')
        if self.extreme_range_row_threshold < self.heavy_range_row_threshold:
            raise ValueError('extreme row threshold must be at least the heavy threshold')
        if not self.incomplete_report_dir_name.strip():
            raise ValueError('incomplete_report_dir_name must not be empty')

    def attempts_for_size(self, size: int, *, is_smallest: bool) -> int:
        configured = dict(self.page_size_attempts)
        if size in configured:
            return configured[size]
        return self.minimum_size_attempts if is_smallest else 1


def adaptive_paging_options_from_config(
    settings: OverviewCrawlConfig,
    *,
    authentication_attempts: int,
    profile: str,
    page_size_trace_path: Path | None = None,
) -> AdaptivePagingOptions:
    """Map every overview tuning field into the runtime cursor policy."""
    settings.validate()
    return AdaptivePagingOptions(
        page_schedule=settings.page_schedule,
        page_size_attempts=tuple(settings.page_size_attempts.items()),
        connect_timeout=settings.connect_timeout_seconds,
        minimum_size_attempts=settings.minimum_size_attempts,
        same_size_delay_step=settings.same_size_delay_step_seconds,
        same_size_delay_cap=settings.same_size_delay_cap_seconds,
        successful_page_delay=settings.successful_page_delay_seconds,
        authentication_attempts=authentication_attempts,
        rate_limit_attempts=settings.rate_limit_attempts,
        rate_limit_base_delay=settings.rate_limit_base_delay_seconds,
        rate_limit_max_delay=settings.rate_limit_max_delay_seconds,
        rate_limit_backoff_ms=settings.rate_limit_backoff_ms,
        profile=profile,
        min_start_gap_ms=settings.min_start_gap_ms,
        min_idle_gap_ms=settings.min_idle_gap_ms,
        jitter_ms=settings.jitter_ms,
        pause_every_success=settings.pause_every_success,
        pause_ms=settings.pause_ms,
        adaptive_throttle_enabled=settings.adaptive_throttle_enabled,
        page_sleep_enabled=settings.page_sleep_enabled,
        page_sleep_every_pages=settings.page_sleep_every_pages,
        page_sleep_ms=settings.page_sleep_ms,
        heavy_range_page_threshold=settings.heavy_range_page_threshold,
        heavy_range_row_threshold=settings.heavy_range_row_threshold,
        heavy_range_sleep_after_done_ms=settings.heavy_range_sleep_after_done_ms,
        extreme_range_page_threshold=settings.extreme_range_page_threshold,
        extreme_range_row_threshold=settings.extreme_range_row_threshold,
        extreme_page_sleep_every_pages=settings.extreme_page_sleep_every_pages,
        extreme_page_sleep_ms=settings.extreme_page_sleep_ms,
        extreme_range_sleep_after_done_ms=settings.extreme_range_sleep_after_done_ms,
        continue_on_incomplete=settings.continue_on_incomplete,
        continue_on_rate_limited=settings.continue_on_rate_limited,
        write_page_audit_report=settings.write_page_audit_report,
        incomplete_report_dir_name=settings.incomplete_report_dir_name,
        page_size_trace_path=page_size_trace_path,
    )


class InvoiceRateLimitError(RuntimeError):
    """The portal kept returning HTTP 429 after controlled cooldowns."""


class IncompleteCursorError(RuntimeError):
    """A cursor ended before the advertised total was fetched."""

    def __init__(self, message: str, result: InvoiceFetchResult) -> None:
        super().__init__(message)
        self.result = result


class InvoiceCrawler:
    def __init__(
        self,
        client: WebClient,
        request_get: Callable[..., requests.Response] | None = None,
        reauthenticate: Callable[[], str] | None = None,
        paging_options: AdaptivePagingOptions | None = None,
    ) -> None:
        self.client = client
        self.request_get = request_get or client.get
        self.reauthenticate = reauthenticate
        self.paging_options = paging_options or AdaptivePagingOptions()
    #https://hoadondientu.gdt.gov.vn/api/query/invoices/purchase? sort=tdlap:desc&size=15&search=tdlap=ge=10/06/2026T00:00:00;tdlap=le=09/07/2026T23:59:59;ttxly==5
    def get_invoice_list(
        self, 
        headers: dict[str,str],
        query_type: str,
        invoice_type: str,
        params: dict[str,Any]
        ) -> dict[str,Any]:

        invoice_list = self.request_get(
            f'https://hoadondientu.gdt.gov.vn/api/{query_type}/invoices/{invoice_type}',
            params=params,
            headers=headers,
        ).json()
        datas = invoice_list.get('datas',[])
        total = invoice_list.get('total')
        is_next_page = bool(invoice_list.get('state'))

        logger.debug(
            'Invoice list fetched: %s invoices in this page, total=%s invoices, next_page?= %s',
            len(datas),
            total,
            is_next_page
        )
        return invoice_list

    @staticmethod
    def build_search(begin_date: date, end_date: date, status: int | None = None) -> str:
        search = (
            f"tdlap=ge={begin_date:%d/%m/%Y}T00:00:00;"
            f"tdlap=le={end_date:%d/%m/%Y}T23:59:59"
        )
        if status is not None:
            search += f';ttxly=={status}'
        return search

    def fetch_page(
        self,
        *,
        headers: dict[str, str],
        direction: str,
        category: str,
        begin_date: date,
        end_date: date,
        status: int | None = None,
        state: str | None = None,
        page_size: int = 50,
        timeout: float | tuple[float, float] | None = None,
        retry_attempts: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            'sort': 'tdlap:desc',
            'size': str(page_size),
            'search': self.build_search(begin_date, end_date, status),
        }
        if state:
            params['state'] = state
        response = self.request_get(
            invoice_list_url(category, direction),
            params=params,
            headers=headers,
            timeout=timeout,
            retry_attempts=retry_attempts,
            # Adaptive overview pagination owns its 429 counter so the session
            # must surface each individual 429 without a nested retry loop.
            rate_limit_attempts=1,
        )
        payload = response.json()
        if not isinstance(payload.get('datas', []), list):
            raise RuntimeError('Invalid invoice-list response: datas is not a list')
        return payload

    def fetch_all(
        self,
        *,
        headers: dict[str, str],
        direction: str,
        category: str,
        begin_date: date,
        end_date: date,
        status: int | None = None,
        page_size: int = 50,
        max_pages: int = 10000,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        state: str | None = None
        seen_states: set[str] = set()
        for page_number in range(1, max_pages + 1):
            payload = self.fetch_page(
                headers=headers,
                direction=direction,
                category=category,
                begin_date=begin_date,
                end_date=end_date,
                status=status,
                state=state,
                page_size=page_size,
            )
            rows.extend(payload.get('datas', []))
            next_state = payload.get('state')
            if not next_state:
                return rows
            if next_state in seen_states:
                raise RuntimeError('Invoice pagination returned a repeated state cursor')
            seen_states.add(next_state)
            state = next_state
            self._wait_after_successful_page(page_number)
        raise RuntimeError(f'Invoice pagination exceeded {max_pages} pages')

    def fetch_all_adaptive(
        self,
        *,
        headers: dict[str, str],
        direction: str,
        category: str,
        begin_date: date,
        end_date: date,
        status: int | None = None,
        max_pages: int = 10000,
    ) -> InvoiceFetchResult:
        """Page by cursor and return auditable partial state when a range cannot finish."""
        options = self.paging_options
        schedules = options.page_schedule
        records: list[dict[str, Any]] = []
        page_details: list[PageAuditEntry] = []
        suspicious_pages: list[SuspiciousPage] = []
        state: str | None = None
        seen_states: set[str] = set()
        first_page_total: int | None = None
        estimated_pages = 0
        range_class = 'light'
        pages = 0
        total_retry_count = 0
        total_timeout_count = 0
        max_consecutive_429_count = 0
        status_label = 'all' if status is None else str(status)
        endpoint = invoice_list_url(category, direction)
        query_type = category_to_query_type(category)
        query_label = (
            f'{direction}/{category} | {begin_date:%d/%m/%Y}-{end_date:%d/%m/%Y} '
            f'| status={status_label}'
        )
        logger.info('===== [BẮT ĐẦU] %s | tổng=đang xác định =====', query_label)

        while pages < max_pages:
            schedule_index = 0
            attempted_sizes: list[int] = []
            payload: dict[str, Any] | None = None
            last_error: Exception | None = None
            page_retry_count = 0
            page_timeout_count = 0
            for index, (page_size, read_timeout) in enumerate(schedules):
                attempted_sizes.append(page_size)
                size_attempt_limit = options.attempts_for_size(
                    page_size, is_smallest=index == len(schedules) - 1
                )
                slow_failures = 0
                auth_attempt = 0
                rate_attempt = 0
                while True:
                    try:
                        payload = self.fetch_page(
                            headers=headers,
                            direction=direction,
                            category=category,
                            begin_date=begin_date,
                            end_date=end_date,
                            status=status,
                            state=state,
                            page_size=page_size,
                            timeout=(options.connect_timeout, read_timeout),
                            retry_attempts=1,
                        )
                        schedule_index = index
                        break
                    except (RuntimeError, requests.RequestException) as error:
                        last_error = error
                        page_retry_count += 1
                        total_retry_count += 1
                        response = self._find_http_response(error)
                        if response is not None and response.status_code == 401:
                            auth_attempt += 1
                            if (
                                self.reauthenticate is None
                                or auth_attempt >= options.authentication_attempts
                            ):
                                raise RuntimeError(
                                    'Tax portal kept rejecting refreshed tokens with HTTP 401'
                                ) from error
                            logger.warning(
                                'HTTP 401 page=%d attempt=%d/%d; refreshing authentication once '
                                'while preserving cursor',
                                pages + 1, auth_attempt, options.authentication_attempts,
                            )
                            self.reauthenticate()
                            continue

                        if response is not None and response.status_code == 429:
                            rate_attempt += 1
                            max_consecutive_429_count = max(
                                max_consecutive_429_count, rate_attempt
                            )
                            retry_after = self._retry_after_seconds(response)
                            if rate_attempt >= options.rate_limit_attempts:
                                expected_received = (
                                    min(page_size, max(first_page_total - len(records), 0))
                                    if first_page_total is not None else None
                                )
                                page_details.append(PageAuditEntry(
                                    page=pages + 1,
                                    size=page_size,
                                    expected_received=expected_received,
                                    received=0,
                                    fetched_before=len(records),
                                    fetched_after=len(records),
                                    total=first_page_total,
                                    state=state,
                                    http_status=429,
                                    retry_count=page_retry_count,
                                    timeout_count=page_timeout_count,
                                    note='rate_limit_exhausted',
                                ))
                                suspicious_pages.append(SuspiciousPage(
                                    page=pages + 1,
                                    reason='rate_limit_exhausted',
                                    expected_received=expected_received,
                                    received=0,
                                    http_status=429,
                                    note='range stopped at current checkpoint',
                                ))
                                logger.error(
                                    '429 exhausted; marking range rate_limited and continuing next job '
                                    'direction=%s query_type=%s range=%s..%s fetched=%s/%s',
                                    direction, query_type, begin_date, end_date, len(records),
                                    first_page_total if first_page_total is not None else '?',
                                )
                                result = self._build_partial_result(
                                    records=records,
                                    total=first_page_total,
                                    pages=pages,
                                    page_size=page_size,
                                    final_status='rate_limited',
                                    state=state,
                                    estimated_pages=estimated_pages,
                                    range_class=range_class,
                                    page_details=page_details,
                                    suspicious_pages=suspicious_pages,
                                    retry_count=total_retry_count,
                                    timeout_count=total_timeout_count,
                                    consecutive_429_count=rate_attempt,
                                    error_message='HTTP 429 retries exhausted',
                                )
                                if not options.continue_on_rate_limited:
                                    raise InvoiceRateLimitError(
                                        'Tax portal kept returning HTTP 429 after cooldowns'
                                    ) from error
                                return result

                            if options.rate_limit_backoff_ms:
                                backoff_index = min(
                                    rate_attempt - 1,
                                    len(options.rate_limit_backoff_ms) - 1,
                                )
                                wait_seconds = (
                                    options.rate_limit_backoff_ms[backoff_index] / 1000
                                )
                            else:
                                wait_seconds = min(
                                    options.rate_limit_base_delay * rate_attempt,
                                    options.rate_limit_max_delay,
                                )
                            logger.warning(
                                'Rate limited endpoint=%s status_code=429 attempt=%d/%d '
                                'cooldown_ms=%d profile=%s retry_after_ignored=%s',
                                endpoint, rate_attempt, options.rate_limit_attempts,
                                round(wait_seconds * 1000), options.profile,
                                retry_after if retry_after is not None else 'none',
                            )
                            time.sleep(wait_seconds)
                            continue

                        slow_failures += 1
                        if self._is_timeout_error(error):
                            page_timeout_count += 1
                            total_timeout_count += 1
                        has_retry_at_same_size = slow_failures < size_attempt_limit
                        next_size = schedules[index + 1][0] if index + 1 < len(schedules) else None
                        if has_retry_at_same_size:
                            wait_seconds = min(
                                options.same_size_delay_step * slow_failures,
                                options.same_size_delay_cap,
                            )
                            logger.warning(
                                'Overview page retry page=%d attempt=%d/%d size=%d '
                                'timeout=%.1fs cooldown_ms=%d checkpoint=%s',
                                pages + 1, slow_failures + 1, size_attempt_limit,
                                page_size, read_timeout, round(wait_seconds * 1000),
                                state or 'initial',
                            )
                            time.sleep(wait_seconds)
                            continue
                        if next_size is not None:
                            logger.warning(
                                'Overview reducing page size page=%d from=%d to=%d; preserving cursor',
                                pages + 1, page_size, next_size,
                            )
                        break
                if payload is not None:
                    break

            if payload is None:
                final_status = (
                    'timeout_failed' if last_error and self._is_timeout_error(last_error)
                    else 'partial_saved'
                )
                expected_received = (
                    min(schedules[-1][0], max(first_page_total - len(records), 0))
                    if first_page_total is not None else None
                )
                page_details.append(PageAuditEntry(
                    page=pages + 1,
                    size=schedules[-1][0],
                    expected_received=expected_received,
                    received=0,
                    fetched_before=len(records),
                    fetched_after=len(records),
                    total=first_page_total,
                    state=state,
                    http_status=0,
                    retry_count=page_retry_count,
                    timeout_count=page_timeout_count,
                    note='timeout_exhausted' if final_status == 'timeout_failed' else 'retry_exhausted',
                ))
                suspicious_pages.append(SuspiciousPage(
                    page=pages + 1,
                    reason='timeout_exhausted' if final_status == 'timeout_failed' else 'retry_exhausted',
                    expected_received=expected_received,
                    received=0,
                    http_status=None,
                    note='range stopped at current checkpoint',
                ))
                result = self._build_partial_result(
                    records=records,
                    total=first_page_total,
                    pages=pages,
                    page_size=schedules[-1][0],
                    final_status=final_status,
                    state=state,
                    estimated_pages=estimated_pages,
                    range_class=range_class,
                    page_details=page_details,
                    suspicious_pages=suspicious_pages,
                    retry_count=total_retry_count,
                    timeout_count=total_timeout_count,
                    consecutive_429_count=max_consecutive_429_count,
                    error_message=str(last_error) if last_error else 'page retries exhausted',
                )
                if options.continue_on_incomplete:
                    return result
                sizes_label = '/'.join(str(size) for size, _ in schedules)
                raise RuntimeError(
                    f'Invoice page failed at sizes {sizes_label} for {begin_date}..{end_date}'
                ) from last_error

            pages += 1
            page_records = payload.get('datas', [])
            current_page_size = schedules[schedule_index][0]
            if first_page_total is None:
                first_page_total = self._parse_total(payload.get('total'), len(page_records))
                estimated_pages = (
                    math.ceil(first_page_total / current_page_size)
                    if first_page_total else 0
                )
                range_class = self._classify_range(first_page_total, estimated_pages)
                logger.info(
                    'Overview range classified direction=%s query_type=%s total=%s '
                    'estimated_pages=%s class=%s',
                    direction, query_type, first_page_total, estimated_pages, range_class,
                )

            fetched_before = len(records)
            expected_received = min(
                current_page_size, max(first_page_total - fetched_before, 0)
            )
            records.extend(page_records)
            fetched_after = len(records)
            next_state = payload.get('state')
            note = ''
            if len(page_records) != expected_received:
                note = 'received_count_mismatch'
                suspicious_pages.append(SuspiciousPage(
                    page=pages,
                    reason='received_count_mismatch',
                    expected_received=expected_received,
                    received=len(page_records),
                    http_status=200,
                    note='cursor result differs from expected remaining rows',
                ))
            page_details.append(PageAuditEntry(
                page=pages,
                size=current_page_size,
                expected_received=expected_received,
                received=len(page_records),
                fetched_before=fetched_before,
                fetched_after=fetched_after,
                total=first_page_total,
                state=str(next_state) if next_state is not None else None,
                http_status=200,
                retry_count=page_retry_count,
                timeout_count=page_timeout_count,
                note=note,
            ))
            self._append_page_size_trace(attempted_sizes)
            progress = min(fetched_after / first_page_total * 100, 100.0) if first_page_total else 100.0
            logger.info(
                'Overview page=%d direction=%s query_type=%s received=%d fetched=%d/%d '
                'progress=%.1f%% size=%d state=%s',
                pages, direction, query_type, len(page_records), fetched_after,
                first_page_total, progress, current_page_size,
                'next' if next_state else 'end',
            )

            if not next_state:
                if fetched_after == first_page_total:
                    final_status = (
                        'completed_with_warning'
                        if total_retry_count or total_timeout_count or suspicious_pages
                        else 'completed'
                    )
                else:
                    final_status = 'incomplete'
                result = InvoiceFetchResult(
                    records=records,
                    first_page_total=first_page_total,
                    pages=pages,
                    final_page_size=current_page_size,
                    final_status=final_status,
                    final_state=None,
                    estimated_pages=estimated_pages,
                    range_class=range_class,
                    page_details=tuple(page_details),
                    suspicious_pages=tuple(suspicious_pages),
                    retry_count=total_retry_count,
                    timeout_count=total_timeout_count,
                    consecutive_429_count=max_consecutive_429_count,
                )
                if final_status == 'incomplete' and not options.continue_on_incomplete:
                    raise IncompleteCursorError(
                        f'Cursor ended with {fetched_after}/{first_page_total} rows', result
                    )
                if final_status == 'incomplete':
                    logger.error(
                        'Overview incomplete direction=%s query_type=%s range=%s..%s '
                        'fetched=%s total=%s missing=%s report=pending',
                        direction, query_type, begin_date, end_date, fetched_after,
                        first_page_total, result.missing_count,
                    )
                else:
                    logger.info(
                        'Overview range finished status=%s direction=%s query_type=%s '
                        'fetched=%d/%d pages=%d',
                        final_status, direction, query_type, fetched_after,
                        first_page_total, pages,
                    )
                return result

            if next_state in seen_states:
                raise RuntimeError('Invoice pagination returned a repeated state cursor')
            seen_states.add(next_state)
            state = str(next_state)
            self._wait_after_successful_page(
                pages,
                range_class=range_class,
                total=first_page_total,
                estimated_pages=estimated_pages,
            )

        result = self._build_partial_result(
            records=records,
            total=first_page_total,
            pages=pages,
            page_size=schedules[0][0],
            final_status='partial_saved',
            state=state,
            estimated_pages=estimated_pages,
            range_class=range_class,
            page_details=page_details,
            suspicious_pages=suspicious_pages,
            retry_count=total_retry_count,
            timeout_count=total_timeout_count,
            consecutive_429_count=max_consecutive_429_count,
            error_message=f'pagination exceeded {max_pages} pages',
        )
        if options.continue_on_incomplete:
            return result
        raise RuntimeError(f'Invoice pagination exceeded {max_pages} pages')

    def _wait_after_successful_page(
        self,
        success_count: int,
        *,
        range_class: str = 'light',
        total: int | None = None,
        estimated_pages: int = 0,
    ) -> None:
        options = self.paging_options
        base_delay = max(
            options.successful_page_delay,
            options.min_start_gap_ms / 1000,
            options.min_idle_gap_ms / 1000,
        )
        jitter = random.uniform(*options.jitter_ms) / 1000
        wait_seconds = base_delay + jitter
        if wait_seconds > 0:
            logger.debug(
                'Waiting %.3fs after successful page %d profile=%s',
                wait_seconds,
                success_count,
                options.profile,
            )
            time.sleep(wait_seconds)
        if (
            options.pause_every_success > 0
            and success_count % options.pause_every_success == 0
        ):
            pause_seconds = random.uniform(*options.pause_ms) / 1000
            if pause_seconds > 0:
                logger.info(
                    'Periodic overview pause success_count=%d cooldown_ms=%d profile=%s',
                    success_count,
                    round(pause_seconds * 1000),
                    options.profile,
                )
                time.sleep(pause_seconds)
        if not options.adaptive_throttle_enabled or not options.page_sleep_enabled:
            return
        if range_class == 'extreme':
            every_pages = options.extreme_page_sleep_every_pages
            cooldown_range = options.extreme_page_sleep_ms
        elif range_class == 'heavy':
            every_pages = options.page_sleep_every_pages
            cooldown_range = options.page_sleep_ms
        else:
            return
        if success_count % every_pages != 0:
            return
        cooldown_ms = round(random.uniform(*cooldown_range))
        logger.info(
            'Periodic overview page sleep class=%s page_count=%s total=%s '
            'estimated_pages=%s cooldown_ms=%s profile=%s',
            range_class, success_count, total, estimated_pages,
            cooldown_ms, options.profile,
        )
        if cooldown_ms > 0:
            time.sleep(cooldown_ms / 1000)

    def range_done_cooldown_ms(self, result: InvoiceFetchResult) -> int:
        """Return a randomized post-range cooldown based only on measured volume."""
        options = self.paging_options
        if (
            not options.adaptive_throttle_enabled
            or result.final_status not in {'completed', 'completed_with_warning'}
        ):
            return 0
        if result.range_class == 'extreme':
            return round(random.uniform(*options.extreme_range_sleep_after_done_ms))
        if result.range_class == 'heavy':
            return round(random.uniform(*options.heavy_range_sleep_after_done_ms))
        return 0

    def _classify_range(self, total: int, estimated_pages: int) -> str:
        options = self.paging_options
        if not options.adaptive_throttle_enabled:
            return 'light'
        if (
            estimated_pages >= options.extreme_range_page_threshold
            or total >= options.extreme_range_row_threshold
        ):
            return 'extreme'
        if (
            estimated_pages >= options.heavy_range_page_threshold
            or total >= options.heavy_range_row_threshold
        ):
            return 'heavy'
        return 'light'

    @staticmethod
    def _parse_total(value: Any, fallback: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return fallback
        return max(parsed, 0)

    @staticmethod
    def _is_timeout_error(error: BaseException) -> bool:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, requests.Timeout):
                return True
            current = current.__cause__ or current.__context__
        return False

    @staticmethod
    def _build_partial_result(
        *,
        records: list[dict[str, Any]],
        total: int | None,
        pages: int,
        page_size: int,
        final_status: str,
        state: str | None,
        estimated_pages: int,
        range_class: str,
        page_details: list[PageAuditEntry],
        suspicious_pages: list[SuspiciousPage],
        retry_count: int,
        timeout_count: int,
        consecutive_429_count: int = 0,
        error_message: str | None = None,
    ) -> InvoiceFetchResult:
        return InvoiceFetchResult(
            records=records,
            first_page_total=total,
            pages=pages,
            final_page_size=page_size,
            final_status=final_status,
            final_state=state,
            estimated_pages=estimated_pages,
            range_class=range_class,
            page_details=tuple(page_details),
            suspicious_pages=tuple(suspicious_pages),
            retry_count=retry_count,
            timeout_count=timeout_count,
            consecutive_429_count=consecutive_429_count,
            checkpoint_state=state,
            error_message=error_message,
        )

    def _append_page_size_trace(self, attempted_sizes: list[int]) -> None:
        path = self.paging_options.page_size_trace_path
        if path is None:
            return
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='ascii', newline='') as stream:
            stream.write(' '.join(str(size) for size in attempted_sizes) + '\n')

    @staticmethod
    def _find_http_response(error: BaseException) -> requests.Response | None:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, requests.HTTPError) and current.response is not None:
                return current.response
            current = current.__cause__ or current.__context__
        return None

    @staticmethod
    def _retry_after_seconds(response: requests.Response) -> float | None:
        value = response.headers.get('Retry-After')
        if not value:
            return None
        try:
            return max(1.0, min(float(value), 60.0))
        except ValueError:
            return None

    def download_export(
        self,
        *,
        headers: dict[str, str],
        direction: str,
        category: str,
        begin_date: date,
        end_date: date,
        status: int | None = None,
    ) -> bytes:
        status_label = 'all' if status is None else str(status)
        export_label = (
            f'{direction}/{category} | {begin_date:%d/%m/%Y}-{end_date:%d/%m/%Y} '
            f'| status={status_label}'
        )
        params: dict[str, Any] = {
            'sort': 'tdlap:desc',
            'search': self.build_search(begin_date, end_date, status),
        }
        if direction == 'purchase':
            params['type'] = 'purchase'
        logger.info('===== [EXPORT START] %s =====', export_label)
        try:
            response = self.request_get(
                invoice_export_url(category, direction),
                params=params,
                headers=headers,
                timeout=(15, 120),
                # Export has no page cursor, so retry the identical month/status.
                # TaxPortalSession owns the fixed ten-attempt HTTP 429 policy.
                retry_attempts=2 if category == 'cash_register' else 5,
            )
        except Exception as error:
            http_response = self._find_http_response(error)
            logger.error(
                '===== [EXPORT ERROR] %s | error=%s | HTTP=%s =====',
                export_label,
                type(error).__name__,
                http_response.status_code if http_response is not None else 'none',
            )
            raise
        content = response.content
        if not content.startswith(b'PK'):
            content_type = response.headers.get('Content-Type', '')
            content_length = response.headers.get('Content-Length', 'none')
            disposition = response.headers.get('Content-Disposition', 'none')
            preview = self._invalid_export_preview(content, content_type)
            logger.error(
                '===== [EXPORT INVALID] %s | HTTP=%s | content-type=%s '
                '| bytes=%d | content-length=%s | disposition=%s '
                '| first-bytes-hex=%s | preview=%r =====',
                export_label, response.status_code, content_type or 'none',
                len(content), content_length, disposition,
                content[:16].hex() or 'empty', preview,
            )
            raise RuntimeError(
                f'Tax portal did not return an XLSX file for {export_label}; '
                f'HTTP={response.status_code}, content-type={content_type or "none"}, '
                f'bytes={len(content)}'
            )
        logger.info(
            '===== [EXPORT OK] %s | HTTP=%s | content-type=%s | bytes=%d =====',
            export_label, response.status_code,
            response.headers.get('Content-Type', 'none'), len(content),
        )
        return content

    @staticmethod
    def _invalid_export_preview(content: bytes, content_type: str) -> str:
        """Return a short single-line preview only for likely textual errors."""
        lowered_type = content_type.lower()
        stripped = content.lstrip()
        is_text = (
            any(marker in lowered_type for marker in ('json', 'text', 'html', 'xml'))
            or stripped.startswith((b'{', b'[', b'<'))
        )
        if not is_text:
            return '<non-text response>'
        return content[:300].decode('utf-8', errors='replace').replace('\r', ' ').replace('\n', ' ')
