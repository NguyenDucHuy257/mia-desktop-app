from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Callable
from urllib.parse import quote

import requests

from app.config.crawl_config import (
    RATE_LIMIT_ATTEMPTS,
    VALID_DIRECTIONS,
    VALID_QUERY_TYPES,
)
from app.crawlers.endpoints import API_BASE_URL
from app.crawlers.web_client import WebClient

logger = logging.getLogger(__name__)

VALID_EXPORT_KINDS = frozenset({'xml', 'html'})
MISSING_ORIGINAL_MESSAGE = 'Không tồn tại hồ sơ gốc của hóa đơn.'


class InvoicePackageUnavailableError(RuntimeError):
    """The portal confirms that no original package exists for this invoice."""


def build_invoice_package_headers(
    base_headers: dict[str, str],
    direction: str,
    export_kind: str,
) -> dict[str, str]:
    """Build export headers without mutating or exposing authenticated headers."""
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f'Unsupported direction: {direction!r}')
    if export_kind not in VALID_EXPORT_KINDS:
        raise ValueError(f'Unsupported export_kind: {export_kind!r}')

    headers = dict(base_headers)

    def set_header(name: str, value: str) -> None:
        for existing_name in list(headers):
            if existing_name.lower() == name.lower():
                del headers[existing_name]
        headers[name] = value

    set_header('Accept', 'application/json, text/plain, */*')
    set_header('Referer', 'https://hoadondientu.gdt.gov.vn/tra-cuu/tra-cuu-hoa-don')
    set_header('End-Point', '/tra-cuu/tra-cuu-hoa-don')
    set_header('Accept-Language', 'vi')
    if export_kind == 'xml':
        action = (
            'Xuất xml (hóa đơn bán ra)'
            if direction == 'sold'
            else 'Xuất xml (hóa đơn mua vào)'
        )
    else:
        action = (
            'In hóa đơn (hóa đơn bán ra)'
            if direction == 'sold'
            else 'In hóa đơn (hóa đơn mua vào)'
        )
    # Match the browser/header convention already used by the detail crawler.
    set_header('Action', quote(action, safe='()'))
    return headers


class InvoicePackageCrawler:
    """Download an invoice ZIP package without persistence or extraction."""

    def __init__(
        self,
        client: WebClient,
        request_get: Callable[..., requests.Response] | None = None,
        request_timeout: float | tuple[float, float] = (15.0, 75.0),
        retry_attempts: int = 3,
        rate_limit_attempts: int = RATE_LIMIT_ATTEMPTS,
    ) -> None:
        if retry_attempts < 1:
            raise ValueError('retry_attempts must be at least 1')
        if rate_limit_attempts < 1:
            raise ValueError('rate_limit_attempts must be at least 1')
        self.client = client
        # Inject TaxPortalSession.get so package requests receive the same
        # timeout/network/401/429/5xx retry policy as invoice-detail requests.
        self.request_get = request_get
        self.request_timeout = request_timeout
        self.retry_attempts = retry_attempts
        self.rate_limit_attempts = rate_limit_attempts

    def download_invoice_package(
        self,
        headers: dict[str, str],
        query_type: str,
        nbmst: str,
        khhdon: str,
        shdon: str | int,
        khmshdon: str | int,
    ) -> bytes:
        if query_type not in VALID_QUERY_TYPES:
            raise ValueError(f'Unsupported query_type: {query_type!r}')

        invoice_key = f'{nbmst}/{khhdon}/{shdon}/{khmshdon}'
        logger.info('Starting invoice package download key=%s', invoice_key)
        request_get = self.request_get or self.client.get
        try:
            response = request_get(
                f'{API_BASE_URL}/{query_type}/invoices/export-xml',
                params={
                    'nbmst': str(nbmst),
                    'khhdon': str(khhdon),
                    'shdon': str(shdon),
                    'khmshdon': str(khmshdon),
                },
                headers=headers,
                timeout=self.request_timeout,
                retry_attempts=self.retry_attempts,
                rate_limit_attempts=self.rate_limit_attempts,
            )
        except requests.HTTPError as error:
            response = error.response
            status_code = response.status_code if response is not None else None
            portal_message: str | None = None
            if response is not None:
                try:
                    payload = response.json()
                except (requests.JSONDecodeError, ValueError):
                    payload = None
                if isinstance(payload, dict) and isinstance(payload.get('message'), str):
                    portal_message = payload['message']
            message_suffix = f' message={portal_message}' if portal_message else ''
            error_type = (
                InvoicePackageUnavailableError
                if portal_message == MISSING_ORIGINAL_MESSAGE
                else RuntimeError
            )
            raise error_type(
                f'Tax portal returned HTTP {status_code} for invoice package '
                f'key={invoice_key}{message_suffix}'
            ) from error
        status_code = getattr(response, 'status_code', None)
        if status_code != 200:
            raise RuntimeError(
                f'Tax portal returned HTTP {status_code} for invoice package key={invoice_key}'
            )
        content = bytes(getattr(response, 'content', b''))
        if not content:
            raise RuntimeError(
                f'Tax portal returned an empty invoice package for key={invoice_key}'
            )
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise RuntimeError(
                f'Tax portal returned invalid ZIP invoice package for key={invoice_key} '
                f'bytes={len(content)}'
            )
        logger.info(
            'Downloaded invoice package key=%s bytes=%d', invoice_key, len(content)
        )
        return content
