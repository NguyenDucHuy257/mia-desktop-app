from __future__ import annotations

# - Lấy captcha
# - Lưu captcha_id / captcha_token nếu có
# - Gửi username/password/captcha để login
# - Lấy access_token
# - Tạo auth headers

from app.crawlers.web_client import WebClient
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.captcha.solver import CaptchaSolver
import logging
import requests
from collections.abc import Callable
import unicodedata

from app.crawlers.endpoints import GET_CAPTCHA_API, LOGIN_API, GET_COMPANY_INFO_API

logger = logging.getLogger(__name__)


class SourceAuthenticationError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str = 'Source authentication failed',
        *,
        retryable: bool = False,
        retry_delay_seconds: int = 0,
    ) -> None:
        self.error_code = error_code
        self.code = error_code
        self.retryable = retryable
        self.retry_delay_seconds = retry_delay_seconds
        super().__init__(message)


class AuthCrawler:
    def __init__(self, client: WebClient, captcha_solver: CaptchaSolver | None = None,
                 progress_callback=None) -> None:
        self.client = client
        self.captcha_solver = captcha_solver
        self.progress_callback = progress_callback or (lambda _event: None)
    def _get_captcha_solver(self) -> CaptchaSolver:
        if self.captcha_solver is None:
            from app.captcha.solver import CaptchaSolver

            self.captcha_solver = CaptchaSolver()
        return self.captcha_solver

    def get_captcha(self, headers: dict[str,str]) -> dict[str,str]:
        self.progress_callback('captcha_request_started')
        response = self.client.get(GET_CAPTCHA_API, headers=headers)
        captcha_key = response.json().get('key')
        captcha_content = response.json().get('content')
        if not captcha_key:
            raise RuntimeError("Captcha key not found")

        if not captcha_content:
            raise RuntimeError("Captcha content not found")
        self.progress_callback('captcha_fetched')
        self.progress_callback('captcha_payload_validated')

        try:
            captcha_cvalue = self._get_captcha_solver().solve(captcha_content)
            self.progress_callback('captcha_solved')
            logger.debug('Captcha solved by local model')
        except Exception as error:
            logger.exception('Captcha model failed')
            raise RuntimeError('Captcha model failed') from error
        return {
            'captcha_key': captcha_key,
            'captcha_cvalue': captcha_cvalue
        }
    def login(self, username: str, password: str, ckey: str, cvalue: str, headers: dict[str,str]) -> str:
        json_data = {
            'username': username,
            'password': password,
            'cvalue': cvalue,
            'ckey': ckey,
        }
        try:
            self.progress_callback('login_request_started')
            login_res = self.client.post(
                LOGIN_API,
                headers=headers,
                json_payload=json_data,
            )
        except requests.HTTPError as error:
            response = error.response
            status_code = getattr(response, 'status_code', None)
            if status_code == 401:
                message = _response_message(response)
                code, safe_message = _classify_login_rejection(message)
                logger.warning(
                    'Source login rejected status_code=401 error_code=%s', code,
                )
                raise SourceAuthenticationError(code, safe_message) from error

            if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
                code = 'source_rate_limited' if status_code == 429 else f'source_http_{status_code}'
                raise SourceAuthenticationError(
                    code,
                    'Dịch vụ nguồn tạm thời không khả dụng',
                    retryable=True,
                    retry_delay_seconds=20,
                ) from error

            raise
        self.progress_callback('login_request_succeeded')
        self.progress_callback('login_response_received')
        login_data = login_res.json().get('token')
        if login_data is not None:
            account_token = login_data
            self.progress_callback('login_response_validated')
            self.progress_callback('login_token_received')
            logger.debug('Login successfully')
        else:
            logger.error('Source login response did not contain a token')
            raise SourceAuthenticationError('source_token_missing')

        return account_token

    def authenticate(
        self,
        username: str,
        password: str,
        ua: str,
        attempts: int = 3,
        on_rate_limit: Callable[[], object] | None = None,
    ) -> str:
        """Solve one fresh captcha and make exactly one login request per task attempt."""
        headers = self.client.build_headers(ua=ua)
        captcha = self.get_captcha(headers)
        try:
            return self.login(
                username=username,
                password=password,
                ckey=captcha['captcha_key'],
                cvalue=captcha['captcha_cvalue'],
                headers=headers,
            )
        except Exception as error:
            response = getattr(error, 'response', None)
            if getattr(response, 'status_code', None) == 429 and on_rate_limit is not None:
                on_rate_limit()
            logger.warning('Authentication failed error_type=%s', type(error).__name__)
            raise
    def get_company_info(self, headers: dict[str,str]) -> dict[str,str]:
        company_info= {'name':'Unknow'}

        info_res = self.client.get(GET_COMPANY_INFO_API,headers=headers)

        company_name = info_res.json().get('name')
        company_info['name'] = company_name
        logger.debug('Comany name = %s', company_name)
        return company_info


_INVALID_CREDENTIALS = 'Tên đăng nhập hoặc mật khẩu không đúng'
_ACCOUNT_LOCKED = 'Tài khoản đã bị khoá vì đã nhập sai thông tin quá số lần quy định'


def _response_message(response: object) -> str:
    try:
        payload = response.json()  # type: ignore[attr-defined]
    except (TypeError, ValueError, AttributeError):
        return ''
    message = payload.get('message') if isinstance(payload, dict) else None
    if not isinstance(message, str):
        return ''
    return ' '.join(unicodedata.normalize('NFC', message).strip().split())


def _classify_login_rejection(message: str) -> tuple[str, str]:
    comparable = message.casefold()
    if _INVALID_CREDENTIALS.casefold() in comparable:
        return 'invalid_source_credentials', _INVALID_CREDENTIALS
    if _ACCOUNT_LOCKED.casefold() in comparable:
        return 'source_account_locked', _ACCOUNT_LOCKED
    # Do not echo an untrusted upstream body into durable state or logs.
    return 'source_login_rejected', 'Cổng hóa đơn từ chối đăng nhập'
