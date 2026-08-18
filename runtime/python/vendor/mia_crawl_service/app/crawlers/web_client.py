from __future__ import annotations

from typing import Any

import requests

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

    def build_headers(self, 
        authorization: str | None = None, 
        endpoint: str | None = '/tra-cuu/tra-cuu-hoa-don',
        ua: str = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'
        ) -> dict[str,str]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "vi",
            "referer": "https://hoadondientu.gdt.gov.vn/tra-cuu/tra-cuu-hoa-don",
            "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
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

        return headers
        
    def get(self, 
        url: str, 
        params: dict[str,Any] | None = None,
        headers: dict[str,str] | None = None,
        timeout: float | tuple[float, float] | None = None,
        retry_attempts: int | None = None,
        rate_limit_attempts: int | None = None,
        ) -> requests.Response:

        response = self.session.get(
            url,
            params = params,
            headers = headers,
            timeout=self.timeout if timeout is None else timeout,
        )
        response.raise_for_status()
        return response

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
            headers = headers,
            json = json_payload,
            timeout=self.timeout if timeout is None else timeout,
        )
        response.raise_for_status()
        return response
