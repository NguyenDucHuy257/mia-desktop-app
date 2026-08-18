# - Lấy captcha
# - Lưu captcha_id / captcha_token nếu có
# - Gửi username/password/captcha để login
# - Lấy access_token
# - Tạo auth headers

from app.crawlers.web_client import WebClient
from app.captcha.solver import CaptchaSolver
import logging
import requests
from collections.abc import Callable

from app.crawlers.endpoints import GET_CAPTCHA_API, LOGIN_API, GET_COMPANY_INFO_API

logger = logging.getLogger(__name__)
class AuthCrawler:
    def __init__(self, client: WebClient, captcha_solver: CaptchaSolver | None = None) -> None:
        self.client = client
        self.captcha_solver = captcha_solver if captcha_solver is not None else CaptchaSolver()
    def get_captcha(self, headers: dict[str,str]) -> dict[str,str]:
        response = self.client.get(GET_CAPTCHA_API, headers=headers)
        captcha_key = response.json().get('key')
        captcha_content = response.json().get('content')
        if not captcha_key:
            raise RuntimeError("Captcha key not found")

        if not captcha_content:
            raise RuntimeError("Captcha content not found")

        try:
            captcha_cvalue = self.captcha_solver.solve(captcha_content)
            logger.debug('Captcha solved by model: %s', captcha_cvalue)
        except Exception as error:
            logger.exception('Captcha model failed')
            raise RuntimeError('Captcha model failed') from error
        logger.debug(
            'Captcha received: key=%s, value=%s',
            captcha_key,
            captcha_cvalue,
        )
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
            login_res = self.client.post(
                LOGIN_API,
                headers=headers,
                json_payload=json_data,
            )
        except requests.HTTPError as error:
            response = error.response

            if response is not None and response.status_code == 401:
                try:
                    error_data = response.json()
                except ValueError:
                    error_data = {}

                error_message = error_data.get("message")

                logger.warning(
                    "Login rejected: reason=%s",
                    error_message,
                )

                raise RuntimeError(
                    error_message or "Login rejected"
                ) from error

            raise
        login_data = login_res.json().get('token')
        if login_data is not None:
            account_token = login_data
            logger.debug('Login successfully')
        else:
            logger.error('Token not found: %s',login_res.json())
            raise Exception('Token not found: ',login_res.json())

        return account_token

    def authenticate(
        self,
        username: str,
        password: str,
        ua: str,
        attempts: int = 3,
        on_rate_limit: Callable[[], object] | None = None,
    ) -> str:
        """Solve a fresh captcha and log in, retrying OCR/login failures."""
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                headers = self.client.build_headers(ua=ua)
                captcha = self.get_captcha(headers)
                return self.login(
                    username=username,
                    password=password,
                    ckey=captcha['captcha_key'],
                    cvalue=captcha['captcha_cvalue'],
                    headers=headers,
                )
            except Exception as error:
                last_error = error
                current: BaseException | None = error
                while current is not None:
                    response = getattr(current, 'response', None)
                    if response is not None and response.status_code == 429:
                        if on_rate_limit is not None:
                            on_rate_limit()
                        break
                    current = current.__cause__ or current.__context__
                logger.warning('Authentication attempt %s/%s failed: %s', attempt, attempts, error)
        raise RuntimeError(f'Authentication failed after {attempts} attempts') from last_error
    def get_company_info(self, headers: dict[str,str]) -> dict[str,str]:
        company_info= {'name':'Unknow'}

        info_res = self.client.get(GET_COMPANY_INFO_API,headers=headers)

        company_name = info_res.json().get('name')
        company_info['name'] = company_name
        logger.debug('Comany name = %s', company_name)
        return company_info
