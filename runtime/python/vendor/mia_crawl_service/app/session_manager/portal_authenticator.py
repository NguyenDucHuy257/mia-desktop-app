from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading

from app.crawlers.auth_crawler import AuthCrawler
from app.crawlers.web_client import WebClient
from app.session_manager.models import CredentialBundle


DEFAULT_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/149.0.0.0 Safari/537.36'
)
_ROUTE_OVERRIDE: ContextVar[str | None | object] = ContextVar(
    'mia_auth_route_override', default=...
)


@contextmanager
def portal_auth_route(proxy_url: str | None):
    """Bind one fixed direct/proxy route to a complete CAPTCHA/login call."""
    token = _ROUTE_OVERRIDE.set(proxy_url)
    try:
        yield
    finally:
        _ROUTE_OVERRIDE.reset(token)


class PortalAuthenticator:
    """One-shot source login; plaintext credentials only exist during this call."""

    def __init__(
        self,
        *,
        timeout: float | tuple[float, float] = (15, 75),
        attempts: int = 1,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self.attempts = 1
        self.user_agent = user_agent
        self._captcha_solver = None
        self._captcha_solver_init_lock = threading.Lock()

    def __call__(self, credentials: CredentialBundle) -> str:
        return self.authenticate_with_progress(credentials, None)

    def _get_captcha_solver(self):
        solver = self._captcha_solver
        if solver is not None:
            return solver
        with self._captcha_solver_init_lock:
            if self._captcha_solver is None:
                from app.captcha.solver import CaptchaSolver
                self._captcha_solver = CaptchaSolver()
            return self._captcha_solver

    def authenticate_with_progress(self, credentials, progress_callback) -> str:
        override = _ROUTE_OVERRIDE.get()
        proxy_url = credentials.proxy_url if override is ... else override
        if proxy_url is not None and not isinstance(proxy_url, str):
            raise TypeError('authentication route override must be a string or None')
        client = WebClient(timeout=self.timeout, proxy=proxy_url)
        return AuthCrawler(
            client,
            captcha_solver=self._get_captcha_solver(),
            progress_callback=progress_callback,
        ).authenticate(
            username=credentials.username,
            password=credentials.password,
            ua=self.user_agent,
            attempts=1,
        )
