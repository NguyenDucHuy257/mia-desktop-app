from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import requests

PORTAL_ROOT_URL = 'https://hoadondientu.gdt.gov.vn/'
INVOICE_LOOKUP_URL = f'{PORTAL_ROOT_URL}tra-cuu/tra-cuu-hoa-don'
DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/152.0.0.0 Safari/537.36'
)

class WebClient:
    def __init__(
        self,
        timeout: float | tuple[float, float] = (15, 75),
        proxy: str | None = None,
    ) -> None:
        self.session = requests.Session()
        self.timeout = timeout
        if proxy:
            self.set_proxy(proxy)

    def set_proxy(self, proxy: str | None) -> None:
        self.session.proxies.clear()
        if proxy:
            self.session.proxies.update({'http': proxy, 'https': proxy})

    def build_headers(
        self,
        authorization: str | None = None,
        endpoint: str | None = '/tra-cuu/tra-cuu-hoa-don',
        ua: str = DEFAULT_USER_AGENT,
        referer: str = INVOICE_LOOKUP_URL,
        action: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "vi",
            "referer": referer,
            "request-id": str(uuid4()),
            "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": ua,
        }
        
        if authorization:
            headers["authorization"] = "Bearer " + authorization

        if endpoint:
            headers["end-point"] = endpoint

        if action is not None:
            headers["action"] = action

        return headers

    @staticmethod
    def _fresh_request_headers(headers: dict[str, str] | None) -> dict[str, str]:
        """Clone headers and assign one correlation id to this wire attempt."""
        prepared = dict(headers or {})
        for name in list(prepared):
            if name.lower() == 'request-id':
                del prepared[name]
        prepared['request-id'] = str(uuid4())
        return prepared
        
    def get(self,
        url: str, 
        params: dict[str,Any] | None = None,
        headers: dict[str,str] | None = None,
        timeout: float | tuple[float, float] | None = None,
        retry_attempts: int | None = None,
        rate_limit_attempts: int | None = None,
        ) -> requests.Response:

        """GET with bounded direct-client retries.

        Managed ``TaxPortalSession`` consumes these overrides before calling
        this method, so its authenticated retry loop is never nested here.
        """
        normal_limit = max(1, int(retry_attempts or 1))
        rate_limit = max(1, int(rate_limit_attempts or 1))
        normal_failures = 0
        rate_failures = 0
        last_error: BaseException | None = None
        for _ in range(normal_limit + rate_limit - 1):
            try:
                response = self.session.get(
                    url, params=params, headers=self._fresh_request_headers(headers),
                    timeout=self.timeout if timeout is None else timeout,
                )
                response.raise_for_status()
                return response
            except requests.HTTPError as error:
                last_error = error
                status = error.response.status_code if error.response is not None else None
                if status == 429:
                    rate_failures += 1
                    if rate_failures >= rate_limit:
                        raise
                    time.sleep((2, 10, 20, 40)[min(rate_failures - 1, 3)])
                    continue
                if status not in {500, 502, 503, 504}:
                    raise
            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = error
            normal_failures += 1
            if normal_failures >= normal_limit:
                raise last_error
            time.sleep(min(2 ** (normal_failures - 1), 10))
        if last_error is not None:
            raise last_error
        raise RuntimeError('WebClient GET retry loop ended without a response')

    def post(self, 
        url:str,
        payload: dict[str,Any] | None = None,
        json_payload: dict[str,Any] | None = None, 
        headers: dict[str,str] | None = None,
        timeout: float | tuple[float, float] | None = None,
        ) -> requests.Response:
        response = self.session.post(
            url,
            data = payload,
            headers = self._fresh_request_headers(headers),
            json = json_payload,
            timeout=self.timeout if timeout is None else timeout,
        )
        response.raise_for_status()
        return response
