from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from app.session_manager.contracts import SessionRepository
from app.session_manager.crypto import SessionCipher
from app.session_manager.models import (
    SESSION_ACTIVE,
    SESSION_AUTHENTICATION_FAILED,
    SESSION_AUTHENTICATING,
    SESSION_EXPIRED,
    SESSION_REVOKED,
    AuthenticationFailedError,
    AuthenticationLeaseLostError,
    AuthenticationWaitTimeoutError,
    CredentialBundle,
    PersistedSession,
    SessionHandle,
    SessionUnavailableError,
    TokenSnapshot,
)


logger = logging.getLogger('mia.session_manager')
SourceAuthenticator = Callable[[CredentialBundle], str]
_ERROR_CODE = re.compile(r'[^a-z0-9_.-]+')


class SessionTokenManager:
    """Own encrypted credentials/tokens and coordinate source authentication."""

    def __init__(
        self,
        repository: SessionRepository,
        cipher: SessionCipher,
        authenticator: SourceAuthenticator,
        *,
        auth_lease_seconds: int = 120,
        wait_timeout_seconds: float = 130.0,
        wait_poll_seconds: float = 0.1,
    ) -> None:
        if auth_lease_seconds < 2:
            raise ValueError('auth_lease_seconds must be at least 2')
        if wait_timeout_seconds <= 0 or wait_poll_seconds <= 0:
            raise ValueError('authentication wait settings must be positive')
        self.repository = repository
        self.cipher = cipher
        self.authenticator = authenticator
        self.auth_lease_seconds = auth_lease_seconds
        self.wait_timeout_seconds = wait_timeout_seconds
        self.wait_poll_seconds = wait_poll_seconds
        self._known_generations: dict[str, int] = {}
        self._known_generations_lock = threading.Lock()

    def initialize(self) -> None:
        self.repository.migrate()

    def create_session(
        self,
        credentials: CredentialBundle,
        *,
        ttl_seconds: int = 8 * 60 * 60,
        owner_id: str | None = None,
        now: datetime | None = None,
    ) -> SessionHandle:
        if ttl_seconds < 60:
            raise ValueError('session ttl_seconds must be at least 60')
        current = _as_utc(now)
        internal_session_id = secrets.token_urlsafe(32)
        session_hash = hash_internal_session_id(internal_session_id)
        envelope = self.cipher.encrypt_credentials(credentials, subject=session_hash)
        record = self.repository.create_session(
            session_hash=session_hash,
            account_key=credentials.account_key,
            credential_envelope=envelope,
            credential_key_id=self.cipher.key_id,
            expires_at=current + timedelta(seconds=ttl_seconds),
            owner_id=owner_id,
            now=current,
        )
        logger.info(
            'event=session_created session_ref=%s account_id=%s status=%s',
            session_reference(session_hash),
            record.account_id,
            record.status,
        )
        return _handle(internal_session_id, record)

    def create_authenticated_session(
        self,
        credentials: CredentialBundle,
        *,
        worker_id: str,
        ttl_seconds: int = 8 * 60 * 60,
        now: datetime | None = None,
    ) -> SessionHandle:
        handle = self.create_session(credentials, ttl_seconds=ttl_seconds, now=now)
        try:
            self.authenticate_session(handle.internal_session_id, worker_id=worker_id)
        except AuthenticationFailedError:
            # The caller still needs the opaque ID to inspect/retry/revoke a
            # durably recorded authentication failure.
            pass
        return _handle(
            handle.internal_session_id,
            self._get_available_session(handle.internal_session_id),
        )

    def get_session(self, internal_session_id: str) -> PersistedSession:
        return self._get_available_session(internal_session_id)

    def authenticate_session(
        self,
        internal_session_id: str,
        *,
        worker_id: str,
        force: bool = False,
    ) -> TokenSnapshot:
        return self.authenticate_session_hash(
            hash_internal_session_id(internal_session_id),
            worker_id=worker_id,
            force=force,
        )

    def authenticate_session_hash(
        self,
        session_hash: str,
        *,
        worker_id: str,
        force: bool = False,
        progress_callback=None,
    ) -> TokenSnapshot:
        """Authenticate a session referenced by the durable worker-safe hash."""
        record = self._get_available_session_hash(session_hash)
        if (
            not force
            and record.status == SESSION_ACTIVE
            and record.source_token_envelope is not None
            and self._is_generation_known(record)
        ):
            return self._token_from_record(record)
        return self.refresh_hash_after_unauthorized(
            session_hash,
            rejected_generation=record.token_generation,
            worker_id=worker_id,
            progress_callback=progress_callback,
        )

    def get_token(self, internal_session_id: str, *, worker_id: str) -> TokenSnapshot:
        return self.get_token_by_hash(
            hash_internal_session_id(internal_session_id), worker_id=worker_id
        )

    def get_token_by_hash(self, session_hash: str, *, worker_id: str) -> TokenSnapshot:
        record = self._get_available_session_hash(session_hash)
        if (
            record.status == SESSION_ACTIVE
            and record.source_token_envelope
            and self._is_generation_known(record)
        ):
            return self._token_from_record(record)
        if record.status == SESSION_AUTHENTICATING:
            return self._wait_for_refresh_hash(
                session_hash,
                rejected_generation=record.token_generation,
            )
        return self.refresh_hash_after_unauthorized(
            session_hash,
            rejected_generation=record.token_generation,
            worker_id=worker_id,
        )

    def refresh_after_unauthorized(
        self,
        internal_session_id: str,
        rejected_generation: int,
        *,
        worker_id: str,
    ) -> TokenSnapshot:
        return self.refresh_hash_after_unauthorized(
            hash_internal_session_id(internal_session_id),
            rejected_generation,
            worker_id=worker_id,
        )

    def refresh_hash_after_unauthorized(
        self,
        session_hash: str,
        rejected_generation: int,
        *,
        worker_id: str,
        progress_callback=None,
    ) -> TokenSnapshot:
        if rejected_generation < 0:
            raise ValueError('rejected_generation cannot be negative')
        _validate_session_hash(session_hash)
        self.repository.expire_sessions()
        current = self.repository.get_session(session_hash)
        _assert_available(current)
        if current.token_generation > rejected_generation:
            return self._token_from_record(current)

        claim = self.repository.claim_authentication(
            session_hash,
            worker_id,
            expected_generation=rejected_generation,
            lease_seconds=self.auth_lease_seconds,
        )
        if not claim.acquired:
            if claim.session.token_generation > rejected_generation:
                return self._token_from_record(claim.session)
            return self._wait_for_refresh_hash(
                session_hash,
                rejected_generation=rejected_generation,
            )
        return self._authenticate_as_owner(
            session_hash,
            claim.session,
            worker_id,
            claim.lease_token or '',
            rejected_generation,
            progress_callback,
        )

    def revoke_session(self, internal_session_id: str) -> PersistedSession:
        session_hash = hash_internal_session_id(internal_session_id)
        record = self.repository.revoke_session(session_hash)
        logger.info(
            'event=session_revoked session_ref=%s account_id=%s status=%s',
            session_reference(session_hash),
            record.account_id,
            record.status,
        )
        return record

    def bind(self, internal_session_id: str, *, worker_id: str) -> BoundSessionTokenProvider:
        self._get_available_session(internal_session_id)
        return BoundSessionTokenProvider(self, internal_session_id, worker_id)

    def bind_session_hash(
        self, session_hash: str, *, worker_id: str
    ) -> BoundSessionHashTokenProvider:
        self._get_available_session_hash(session_hash)
        return BoundSessionHashTokenProvider(self, session_hash, worker_id)

    def build_portal_session(self, internal_session_id: str, *, worker_id: str, **kwargs):
        """Build the existing HTTP session with managed tokens and an encrypted route."""
        from app.services.portal_session import TaxPortalSession

        credentials = self._credentials_for_session(internal_session_id)
        if 'proxy' in kwargs or 'proxies' in kwargs:
            raise ValueError('managed portal route comes from encrypted session credentials')
        return TaxPortalSession(
            token_provider=self.bind(internal_session_id, worker_id=worker_id),
            proxy=credentials.proxy_url,
            account_label=session_reference(
                hash_internal_session_id(internal_session_id)
            ),
            **kwargs,
        )

    def build_worker_portal_session(
        self,
        session_hash: str,
        *,
        worker_id: str,
        proxy: str | None | object = ...,
        **kwargs,
    ):
        """Build a managed portal session from a durable, non-secret session hash."""
        from app.services.portal_session import TaxPortalSession

        credentials = self._credentials_for_session_hash(session_hash)
        selected_proxy = credentials.proxy_url if proxy is ... else proxy
        if selected_proxy is not None and not isinstance(selected_proxy, str):
            raise TypeError('proxy override must be a string or None')
        return TaxPortalSession(
            token_provider=self.bind_session_hash(session_hash, worker_id=worker_id),
            proxy=selected_proxy,
            account_label=session_reference(session_hash),
            **kwargs,
        )

    def get_worker_proxy_url(self, session_hash: str) -> str | None:
        """Return the decrypted route only to the in-process worker scheduler."""
        return self._credentials_for_session_hash(session_hash).proxy_url

    def _authenticate_as_owner(
        self,
        session_hash: str,
        session: PersistedSession,
        worker_id: str,
        lease_token: str,
        rejected_generation: int,
        progress_callback=None,
    ) -> TokenSnapshot:
        heartbeat = _AuthenticationHeartbeat(
            self.repository,
            session.session_hash,
            worker_id,
            lease_token,
            self.auth_lease_seconds,
        )
        heartbeat.start()
        try:
            credentials = self._credentials_from_record(session)
            if progress_callback is not None:
                progress_callback('credentials_ready')
            authenticate_with_progress = getattr(
                self.authenticator, 'authenticate_with_progress', None
            )
            token = (
                authenticate_with_progress(credentials, progress_callback)
                if callable(authenticate_with_progress)
                else self.authenticator(credentials)
            )
            if not isinstance(token, str) or not token.strip():
                raise AuthenticationFailedError('source_token_missing')
            token_envelope = self.cipher.encrypt_text(
                token,
                purpose='source-token',
                subject=session.session_hash,
            )
            try:
                updated = self.repository.complete_authentication(
                    session.session_hash,
                    worker_id,
                    lease_token,
                    token_envelope=token_envelope,
                    token_key_id=self.cipher.key_id,
                )
                if progress_callback is not None:
                    progress_callback('token_persisted')
            except AuthenticationLeaseLostError:
                return self._wait_for_refresh_hash(
                    session_hash,
                    rejected_generation=rejected_generation,
                )
            logger.info(
                'event=source_authenticated session_ref=%s account_id=%s '
                'token_generation=%d status=%s',
                session_reference(session.session_hash),
                updated.account_id,
                updated.token_generation,
                updated.status,
            )
            self._mark_generation(updated)
            return TokenSnapshot(token, updated.token_generation)
        except AuthenticationWaitTimeoutError:
            raise
        except Exception as error:
            error_code = _safe_error_code(error)
            error_message = _safe_error_message(error)
            retryable, retry_delay_seconds = _authentication_retry_policy(error)
            try:
                failed = self.repository.fail_authentication(
                    session.session_hash,
                    worker_id,
                    lease_token,
                    error_code=error_code,
                    error_message=error_message,
                )
            except AuthenticationLeaseLostError:
                return self._wait_for_refresh_hash(
                    session_hash,
                    rejected_generation=rejected_generation,
                )
            logger.warning(
                'event=source_authentication_failed session_ref=%s account_id=%s '
                'status=%s error_code=%s error_type=%s',
                session_reference(session.session_hash),
                failed.account_id,
                failed.status,
                error_code,
                type(error).__name__,
            )
            raise AuthenticationFailedError(
                error_code,
                error_message,
                retryable=retryable,
                retry_delay_seconds=retry_delay_seconds,
            ) from error
        finally:
            heartbeat.stop()

    def _wait_for_refresh(
        self,
        internal_session_id: str,
        *,
        rejected_generation: int,
    ) -> TokenSnapshot:
        return self._wait_for_refresh_hash(
            hash_internal_session_id(internal_session_id),
            rejected_generation=rejected_generation,
        )

    def _wait_for_refresh_hash(
        self,
        session_hash: str,
        *,
        rejected_generation: int,
    ) -> TokenSnapshot:
        _validate_session_hash(session_hash)
        deadline = time.monotonic() + self.wait_timeout_seconds
        while time.monotonic() < deadline:
            self.repository.expire_sessions()
            record = self.repository.get_session(session_hash)
            _assert_available(record)
            if (
                record.status == SESSION_ACTIVE
                and record.token_generation > rejected_generation
                and record.source_token_envelope
            ):
                return self._token_from_record(record)
            if record.status == SESSION_AUTHENTICATION_FAILED:
                raise AuthenticationFailedError(
                    record.last_auth_error_code or 'authentication_failed',
                    record.last_auth_error_message or 'Source authentication failed',
                )
            time.sleep(self.wait_poll_seconds)
        raise AuthenticationWaitTimeoutError(
            'timed out waiting for the singleflight authentication owner'
        )

    def _get_available_session(self, internal_session_id: str) -> PersistedSession:
        return self._get_available_session_hash(
            hash_internal_session_id(internal_session_id)
        )

    def _get_available_session_hash(self, session_hash: str) -> PersistedSession:
        _validate_session_hash(session_hash)
        self.repository.expire_sessions()
        record = self.repository.get_session(session_hash)
        _assert_available(record)
        return record

    def _credentials_for_session(self, internal_session_id: str) -> CredentialBundle:
        return self._credentials_from_record(
            self._get_available_session(internal_session_id)
        )

    def _credentials_for_session_hash(self, session_hash: str) -> CredentialBundle:
        return self._credentials_from_record(
            self._get_available_session_hash(session_hash)
        )

    def _credentials_from_record(self, record: PersistedSession) -> CredentialBundle:
        if not record.credential_envelope or not record.credential_key_id:
            raise SessionUnavailableError('internal session has no credential envelope')
        credentials = self.cipher.decrypt_credentials(
            record.credential_envelope,
            subject=record.session_hash,
            key_id=record.credential_key_id,
        )
        if credentials.account_key.strip().casefold() != record.account_key:
            raise SessionUnavailableError('credential account identity does not match session')
        return credentials

    def _token_from_record(self, record: PersistedSession) -> TokenSnapshot:
        if not record.source_token_envelope or not record.source_token_key_id:
            raise SessionUnavailableError('internal session has no source token')
        token = self.cipher.decrypt_text(
            record.source_token_envelope,
            purpose='source-token',
            subject=record.session_hash,
            key_id=record.source_token_key_id,
        )
        self._mark_generation(record)
        return TokenSnapshot(token, record.token_generation)

    def _is_generation_known(self, record: PersistedSession) -> bool:
        with self._known_generations_lock:
            return self._known_generations.get(record.session_hash) == record.token_generation

    def _mark_generation(self, record: PersistedSession) -> None:
        with self._known_generations_lock:
            self._known_generations[record.session_hash] = record.token_generation


@dataclass(frozen=True)
class BoundSessionTokenProvider:
    manager: SessionTokenManager
    internal_session_id: str = field(repr=False)
    worker_id: str

    def get_token(self) -> TokenSnapshot:
        return self.manager.get_token(
            self.internal_session_id,
            worker_id=self.worker_id,
        )

    def refresh_after_unauthorized(self, rejected_generation: int) -> TokenSnapshot:
        return self.manager.refresh_after_unauthorized(
            self.internal_session_id,
            rejected_generation,
            worker_id=self.worker_id,
        )


@dataclass(frozen=True)
class BoundSessionHashTokenProvider:
    """Internal worker provider that never needs the opaque raw session ID."""

    manager: SessionTokenManager
    session_hash: str = field(repr=False)
    worker_id: str

    def get_token(self) -> TokenSnapshot:
        return self.manager.get_token_by_hash(
            self.session_hash, worker_id=self.worker_id
        )

    def refresh_after_unauthorized(self, rejected_generation: int) -> TokenSnapshot:
        return self.manager.refresh_hash_after_unauthorized(
            self.session_hash,
            rejected_generation,
            worker_id=self.worker_id,
        )


class _AuthenticationHeartbeat:
    def __init__(
        self,
        repository: SessionRepository,
        session_hash: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> None:
        self.repository = repository
        self.session_hash = session_hash
        self.worker_id = worker_id
        self.lease_token = lease_token
        self.lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f'auth-heartbeat-{session_reference(self.session_hash)}',
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        interval = max(0.5, self.lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                self.repository.renew_authentication_lease(
                    self.session_hash,
                    self.worker_id,
                    self.lease_token,
                    lease_seconds=self.lease_seconds,
                )
            except AuthenticationLeaseLostError:
                return
            except Exception as error:
                logger.warning(
                    'event=auth_heartbeat_error session_ref=%s worker_id=%s '
                    'error_type=%s',
                    session_reference(self.session_hash),
                    self.worker_id,
                    type(error).__name__,
                )


def hash_internal_session_id(internal_session_id: str) -> str:
    if not isinstance(internal_session_id, str) or len(internal_session_id) < 32:
        raise ValueError('internal_session_id is invalid')
    return hashlib.sha256(internal_session_id.encode('utf-8')).hexdigest()


def _validate_session_hash(session_hash: str) -> None:
    if not isinstance(session_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', session_hash):
        raise ValueError('session_hash must be a SHA-256 hex digest')


def session_reference(session_hash: str) -> str:
    return session_hash[:12]


def _handle(internal_session_id: str, record: PersistedSession) -> SessionHandle:
    return SessionHandle(
        internal_session_id=internal_session_id,
        account_id=record.account_id,
        account_key=record.account_key,
        status=record.status,
        token_generation=record.token_generation,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


def _assert_available(record: PersistedSession) -> None:
    if record.status in (SESSION_REVOKED, SESSION_EXPIRED):
        raise SessionUnavailableError(f'internal session is {record.status}')


def _safe_error_code(error: BaseException) -> str:
    candidate = getattr(error, 'error_code', None)
    if not isinstance(candidate, str) or not candidate:
        chain = tuple(_exception_chain(error))
        if any(isinstance(item, requests.exceptions.Timeout) for item in chain):
            candidate = 'source_timeout'
        elif any(isinstance(item, requests.exceptions.ConnectionError) for item in chain):
            candidate = 'source_connect_failure'
        else:
            status = next((
                getattr(getattr(item, 'response', None), 'status_code', None)
                for item in chain
                if isinstance(
                    getattr(getattr(item, 'response', None), 'status_code', None), int
                )
            ), None)
            if status == 429:
                candidate = 'source_rate_limited'
            elif isinstance(status, int) and status >= 500:
                candidate = f'source_http_{status}'
            else:
                candidate = 'source_authentication_failed'
    return _ERROR_CODE.sub('_', candidate.strip().casefold())[:80]


def _safe_error_message(error: BaseException) -> str:
    messages = {
        'invalid_source_credentials': 'Tên đăng nhập hoặc mật khẩu không đúng',
        'source_account_locked': (
            'Tài khoản đã bị khoá vì đã nhập sai thông tin quá số lần quy định'
        ),
        'source_login_rejected': 'Cổng hóa đơn từ chối đăng nhập',
    }
    if getattr(error, 'error_code', None) in messages:
        return messages[getattr(error, 'error_code')]
    return 'Source authentication failed'


def _authentication_retry_policy(error: BaseException) -> tuple[bool, int]:
    if hasattr(error, 'retryable'):
        return (
            bool(getattr(error, 'retryable')),
            int(getattr(error, 'retry_delay_seconds', 20)),
        )
    chain = tuple(_exception_chain(error))
    if any(isinstance(item, (
        requests.exceptions.Timeout, requests.exceptions.ConnectionError
    )) for item in chain):
        return True, 20
    statuses = {
        getattr(getattr(item, 'response', None), 'status_code', None)
        for item in chain
    }
    return (True, 20) if 429 in statuses or any(
        isinstance(status, int) and status >= 500 for status in statuses
    ) else (False, 0)


def _exception_chain(error: BaseException):
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _as_utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError('session timestamps must be timezone-aware')
    return value.astimezone(timezone.utc)
