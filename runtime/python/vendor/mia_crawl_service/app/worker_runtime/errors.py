from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import requests

from app.crawlers.invoice_package_crawler import InvoicePackageUnavailableError
from app.session_manager.models import (
    AuthenticationFailedError,
    AuthenticationWaitTimeoutError,
    SessionUnavailableError,
)


@dataclass(frozen=True)
class ErrorClassification:
    code: str
    retryable: bool
    retry_delay_seconds: int
    affects_proxy_health: bool = False
    applies_endpoint_cooldown: bool = False


class TaskExecutionError(RuntimeError):
    """Safe error contract consumed by the durable job supervisor."""

    def __init__(
        self,
        classification: ErrorClassification,
        message: str | None = None,
    ) -> None:
        super().__init__(message or _default_error_message(classification.code))
        self.code = classification.code
        self.retryable = classification.retryable
        self.retry_delay_seconds = classification.retry_delay_seconds
        self.affects_proxy_health = classification.affects_proxy_health
        self.applies_endpoint_cooldown = classification.applies_endpoint_cooldown


_AUTH_ERROR_MESSAGES = {
    'invalid_source_credentials': 'Tên đăng nhập hoặc mật khẩu không đúng',
    'source_account_locked': (
        'Tài khoản đã bị khoá vì đã nhập sai thông tin quá số lần quy định'
    ),
    'source_login_rejected': 'Cổng hóa đơn từ chối đăng nhập',
}


def _default_error_message(code: str) -> str:
    return _AUTH_ERROR_MESSAGES.get(code, code)


def classify_task_error(
    error: BaseException,
    *,
    uses_proxy: bool,
    attempt_count: int = 1,
) -> ErrorClassification:
    """Classify an exception chain without copying source response data into state."""
    if hasattr(error, 'retryable') and hasattr(error, 'code'):
        return ErrorClassification(
            str(getattr(error, 'code')),
            bool(getattr(error, 'retryable')),
            int(getattr(error, 'retry_delay_seconds', 30)),
            bool(getattr(error, 'affects_proxy_health', False)),
            bool(getattr(error, 'applies_endpoint_cooldown', False)),
        )
    chain = tuple(_exception_chain(error))
    if any(isinstance(item, InvoicePackageUnavailableError) for item in chain):
        return ErrorClassification('package_unavailable', False, 0)
    if any(isinstance(item, requests.exceptions.ProxyError) for item in chain):
        return ErrorClassification(
            'proxy_connection_failure', attempt_count < 5, 20, True
        )
    if any(isinstance(item, requests.exceptions.Timeout) for item in chain):
        return ErrorClassification('source_timeout', attempt_count < 5, 15)
    if any(isinstance(item, requests.exceptions.ConnectionError) for item in chain):
        return ErrorClassification(
            'proxy_connection_failure' if uses_proxy else 'source_connect_failure',
            attempt_count < 5,
            20,
            uses_proxy,
        )
    if any(isinstance(item, AuthenticationWaitTimeoutError) for item in chain):
        return ErrorClassification('source_auth_wait_timeout', True, 30)
    if any(isinstance(item, AuthenticationFailedError) for item in chain):
        return ErrorClassification('source_authentication_failed', True, 30)
    if any(isinstance(item, SessionUnavailableError) for item in chain):
        return ErrorClassification('internal_session_unavailable', False, 0)

    status = _http_status(chain)
    if status == 429:
        delays = (2, 10, 20, 40)
        return ErrorClassification(
            'source_rate_limited',
            attempt_count < 10,
            delays[min(attempt_count - 1, len(delays) - 1)],
            False,
            True,
        )
    if status == 401:
        return ErrorClassification('source_unauthorized', attempt_count < 3, 15)
    if status in {500, 502, 503, 504}:
        return ErrorClassification(f'source_http_{status}', attempt_count < 5, 20)
    if status is not None and 400 <= status < 500:
        return ErrorClassification(f'source_business_http_{status}', False, 0)

    if any(isinstance(item, sqlite3.Error) for item in chain):
        return ErrorClassification('storage_database_failure', True, 20)
    if any(isinstance(item, OSError) for item in chain):
        return ErrorClassification('storage_filesystem_failure', True, 20)

    text = ' '.join(str(item).casefold() for item in chain)
    if 'http 429' in text or 'rate limit' in text:
        delays = (2, 10, 20, 40)
        return ErrorClassification(
            'source_rate_limited',
            attempt_count < 10,
            delays[min(attempt_count - 1, len(delays) - 1)],
            False,
            True,
        )
    if any(marker in text for marker in ('non-json', 'invalid invoice detail', 'invalid zip')):
        return ErrorClassification('source_schema_failure', False, 0)
    if any(marker in text for marker in ('missing', 'does not contain')):
        return ErrorClassification('source_parse_failure', False, 0)
    return ErrorClassification('unexpected_task_failure', False, 0)


def _exception_chain(error: BaseException):
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _http_status(chain: tuple[BaseException, ...]) -> int | None:
    for item in chain:
        response = getattr(item, 'response', None)
        status = getattr(response, 'status_code', None)
        if isinstance(status, int):
            return status
    return None
