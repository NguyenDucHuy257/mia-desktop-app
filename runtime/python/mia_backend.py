"""Compatibility name for the source-of-truth crawl runtime.

The crawler, account/session state, job admission, recovery, cache/coverage,
progress and result persistence live in the vendored mia-crawl-service modules.
This file keeps the historical ``ProductionBackend`` import while applying only
JSON-RPC/desktop presentation concerns around that source backend.
"""

from __future__ import annotations

import threading

from mia_source_backend import SourceBackend


_PUBLIC_JOB_ERROR_MESSAGES = {
    "invalid_source_credentials": "Tên đăng nhập hoặc mật khẩu không đúng",
    "source_account_locked": (
        "Tài khoản đã bị khoá vì đã nhập sai thông tin quá số lần quy định"
    ),
    "source_login_rejected": "Cổng hóa đơn từ chối đăng nhập",
    "worker_restarted": "Worker đã khởi động lại; vui lòng tạo job mới",
    "worker_lease_expired": "Worker mất lease; vui lòng tạo job mới",
}

_RETRYABLE_JOB_ERRORS = {
    "worker_restarted",
    "worker_lease_expired",
    "auth_service_unavailable",
    "source_auth_wait_timeout",
    "authentication_lease_lost",
    "source_timeout",
    "source_connect_failure",
    "proxy_connection_failure",
    "source_rate_limited",
}


def _public_job_error_message(error_code: str, error_message: str | None) -> str:
    """Mirror the pinned source external_api.app public status contract."""
    message = error_message.strip() if isinstance(error_message, str) else ""
    mapped = _PUBLIC_JOB_ERROR_MESSAGES.get(error_code)
    if mapped is not None and (not message or message == error_code):
        return mapped
    return message or "Job processing failed"


class ProductionBackend(SourceBackend):
    """Thin desktop transport over SourceBackend; no crawl policy lives here."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Display-name metadata is desktop-only and its helpers can be nested
        # (_save -> _load/_write). RLock prevents a self-deadlock without
        # changing any source crawler/session/job behavior.
        self._metadata_lock = threading.RLock()

    @staticmethod
    def public_job(job):
        value = SourceBackend.public_job(job)
        if job.last_error_code:
            code = str(job.last_error_code)
            value["error"] = {
                "code": code,
                "message": _public_job_error_message(code, job.last_error_message),
                "retryable": code in _RETRYABLE_JOB_ERRORS,
            }
        return value


__all__ = ["ProductionBackend", "SourceBackend"]
