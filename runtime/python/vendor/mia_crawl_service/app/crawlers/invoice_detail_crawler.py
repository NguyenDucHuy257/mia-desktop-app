from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
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

def _prepare_requests_headers(headers: dict[str, str]) -> dict[str, str]:
    """Preserve UTF-8 header bytes through requests' Latin-1 wire encoding."""
    prepared: dict[str, str] = {}
    for name, value in headers.items():
        try:
            value.encode('latin-1')
        except UnicodeEncodeError:
            # urllib3 encodes str header values as Latin-1. This carrier string
            # makes the bytes sent on the wire equal to value.encode('utf-8').
            value = value.encode('utf-8').decode('latin-1')
        prepared[name] = value
    return prepared


def build_detail_headers(
    base_headers: dict[str, str],
    direction: str,
) -> dict[str, str]:
    """Build portal detail headers without exposing or replacing the token."""
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f'Unsupported direction: {direction!r}')
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
    action = (
        'In hóa đơn (hóa đơn bán ra)'
        if direction == 'sold'
        else 'In hóa đơn (hóa đơn mua vào)'
    )
    # The portal sends this custom header in percent-encoded UTF-8 form.
    # Keeping parentheses unescaped matches its browser request exactly.
    set_header('Action', quote(action, safe='()'))
    return headers


class InvoiceDetailCrawler:
    """Fetch raw invoice detail JSON without performing persistence."""

    def __init__(
        self,
        client: WebClient,
        request_get: Callable[..., requests.Response] | None = None,
        request_timeout: float | tuple[float, float] = (3.0, 5.0),
        retry_attempts: int = 3,
        rate_limit_attempts: int = RATE_LIMIT_ATTEMPTS,
    ) -> None:
        if retry_attempts < 1:
            raise ValueError('retry_attempts must be at least 1')
        if rate_limit_attempts < 1:
            raise ValueError('rate_limit_attempts must be at least 1')
        self.client = client
        # TaxPortalSession.get can be injected here so 401/429 behavior remains
        # owned by the existing authenticated session layer.
        self.request_get = request_get
        self.request_timeout = request_timeout
        self.retry_attempts = retry_attempts
        self.rate_limit_attempts = rate_limit_attempts

    def get_invoice_detail(
        self,
        headers: dict[str, str],
        query_type: str,
        nbmst: str,
        khhdon: str,
        shdon: str | int,
        khmshdon: str | int,
    ) -> dict[str, Any]:
        if query_type not in VALID_QUERY_TYPES:
            raise ValueError(f'Unsupported query_type: {query_type!r}')
        invoice_key = f'{nbmst}/{khhdon}/{shdon}/{khmshdon}'
        logger.info('Starting invoice detail download key=%s', invoice_key)
        request_get = self.request_get or self.client.get
        response = request_get(
            f'{API_BASE_URL}/{query_type}/invoices/detail',
            params={
                'nbmst': str(nbmst),
                'khhdon': str(khhdon),
                'shdon': str(shdon),
                'khmshdon': str(khmshdon),
            },
            headers=_prepare_requests_headers(headers),
            timeout=self.request_timeout,
            retry_attempts=self.retry_attempts,
            rate_limit_attempts=self.rate_limit_attempts,
        )
        try:
            payload = response.json()
        except (requests.JSONDecodeError, ValueError) as error:
            raise RuntimeError(
                f'Tax portal returned non-JSON invoice detail for key={invoice_key}'
            ) from error
        if not isinstance(payload, dict):
            raise RuntimeError(
                f'Tax portal returned invalid invoice detail object for key={invoice_key}'
            )
        logger.info('Downloaded invoice detail key=%s', invoice_key)
        return payload
