from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.account_connections.repository import (
    AccountConnectionNotFoundError,
    create_account_connection_repository,
)
from app.account_connections.service import AccountConnectionManager
from app.external_api.config import ExternalApiSettings
from app.external_api.factory import create_api_control_repository
from app.external_api.models import (
    AccountConnectionResponse,
    CreateAccountConnectionBody,
    CreateJobBody,
    CreateSessionBody,
    JobAcceptedResponse,
    JobStatusResponse,
    JobSummaryResponse,
    ReconnectAccountConnectionBody,
    RevokeSessionBody,
    SessionResponse,
)
from app.external_api.repository import ApiControlRepository
from app.external_api.results import InvalidResultCursorError
from app.external_api.security import (
    FixedWindowRateLimiter,
    ServiceAuthenticator,
    ServicePrincipal,
    build_service_dependency,
    hash_remote_address,
)
from app.external_api.service import (
    ExternalApiService,
    ResourceOwnershipError,
    ResultNotReadyError,
)
from app.job_engine.factory import create_job_engine_repository
from app.job_engine.models import (
    AccountBusyError,
    CapacityExhaustedError,
    IdempotencyConflictError,
    JobNotFoundError,
)
from app.session_manager.crypto import SessionCipher
from app.session_manager.factory import create_session_repository
from app.session_manager.models import (
    ActiveSessionExistsError,
    SessionNotFoundError,
    SessionUnavailableError,
)
from app.session_manager.service import SessionTokenManager


logger = logging.getLogger('mia.external_api')


def create_app(
    *,
    settings: ExternalApiSettings | None = None,
    service: ExternalApiService | None = None,
    audit_repository: ApiControlRepository | None = None,
) -> FastAPI:
    settings = settings or ExternalApiSettings.from_environment()
    if service is None:
        job_repository = create_job_engine_repository()
        session_repository = create_session_repository()
        cipher = SessionCipher.from_environment()
        session_manager = SessionTokenManager(
            session_repository,
            cipher,
            _api_must_not_authenticate,
        )
        connection_manager = AccountConnectionManager(
            create_account_connection_repository(),
            session_manager,
            cipher,
        )
        service = ExternalApiService(
            job_repository,
            session_manager,
            connection_manager,
        )
    audit_repository = audit_repository or create_api_control_repository()
    authenticator = ServiceAuthenticator(settings)
    limiter = FixedWindowRateLimiter(settings.rate_limit_per_minute)
    require_service = build_service_dependency(authenticator, limiter)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        service.initialize()
        audit_repository.migrate()
        yield

    app = FastAPI(
        title='MIA Server B Control API',
        version='2.0.0',
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware('http')
    async def transport_and_audit(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.monotonic()
        if (
            settings.require_https
            and request.url.path not in {'/health/live', '/health/ready'}
            and request.url.scheme != 'https'
        ):
            response = _error(400, 'https_required', 'HTTPS is required', request_id)
        else:
            try:
                response = await call_next(request)
            except Exception as exc:
                logger.error(
                    'external_api event=unhandled_error request_id=%s error_type=%s',
                    request_id,
                    type(exc).__name__,
                )
                response = _error(500, 'internal_error', 'Internal server error', request_id)
        response.headers['X-Request-ID'] = request_id
        principal = getattr(request.state, 'service_principal', None)
        route = getattr(request.scope.get('route'), 'path', request.url.path)
        duration_ms = int((time.monotonic() - started) * 1000)
        audit = dict(
            request_id=request_id,
            caller_id=getattr(principal, 'caller_id', None),
            key_id=getattr(principal, 'key_id', None),
            method=request.method,
            route=route,
            status_code=response.status_code,
            duration_ms=duration_ms,
            remote_address_hash=hash_remote_address(
                request.client.host if request.client else None
            ),
        )
        logger.info(
            'external_api event=request_completed request_id=%s caller_id=%s key_id=%s '
            'method=%s route=%s status=%s duration_ms=%s remote_hash=%s',
            *audit.values(),
        )
        try:
            audit_repository.record_audit(**audit)
        except Exception as exc:
            logger.error(
                'external_api event=audit_write_failed request_id=%s error_type=%s',
                request_id,
                type(exc).__name__,
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        issues = [
            {'location': [str(part) for part in error['loc']], 'type': error['type']}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={'error': {
                'code': 'invalid_job_options'
                if request.url.path == '/v1/jobs' else 'validation_error',
                'message': 'Request validation failed',
                'request_id': request.state.request_id,
                'issues': issues,
            }},
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = {
            401: 'unauthorized',
            404: 'not_found',
            409: 'conflict',
            422: 'invalid_job_options',
            429: 'rate_limited',
            503: 'capacity_exhausted',
        }.get(exc.status_code, 'request_error')
        response = _error(
            exc.status_code,
            code,
            str(exc.detail),
            request.state.request_id,
        )
        for name, value in (exc.headers or {}).items():
            response.headers[name] = value
        return response

    @app.exception_handler(AccountBusyError)
    async def account_busy(request: Request, exc: AccountBusyError):
        return JSONResponse(
            status_code=409,
            headers={'Retry-After': str(exc.retry_after_seconds)},
            content={'error': {
                'code': 'account_busy',
                'message': 'Tài khoản đang được xử lý bởi một job khác',
                'current_job_id': exc.current_job_id,
                'retry_after_seconds': exc.retry_after_seconds,
                'request_id': request.state.request_id,
            }},
        )

    @app.exception_handler(CapacityExhaustedError)
    async def capacity_exhausted(
        request: Request, exc: CapacityExhaustedError
    ):
        return JSONResponse(
            status_code=503,
            headers={'Retry-After': str(exc.retry_after_seconds)},
            content={'error': {
                'code': 'capacity_exhausted',
                'message': 'Hàng đợi xử lý đang đầy; vui lòng thử lại sau',
                'available_slots': exc.available_slots,
                'total_slots': exc.total_slots,
                'retry_after_seconds': exc.retry_after_seconds,
                'request_id': request.state.request_id,
            }},
        )

    @app.get('/health/live')
    def live():
        return {'status': 'ok'}

    @app.get('/health/ready')
    def ready():
        try:
            ready_value = (
                service.job_repository.ping()
                and service.account_connections.repository.ping()
                and audit_repository.ping()
            )
        except Exception:
            ready_value = False
        if not ready_value:
            return JSONResponse(status_code=503, content={'status': 'not_ready'})
        return {'status': 'ready'}

    @app.post(
        '/v1/account-connections',
        response_model=AccountConnectionResponse,
        status_code=201,
    )
    def create_account_connection(
        body: CreateAccountConnectionBody,
        response: Response,
        principal: ServicePrincipal = Depends(require_service),
    ):
        try:
            connection, reused = service.create_account_connection(
                body, owner_id=principal.caller_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if reused:
            response.status_code = 200
        return _connection_response(connection, reused=reused)

    @app.get(
        '/v1/account-connections/{connection_id}',
        response_model=AccountConnectionResponse,
    )
    def get_account_connection(
        connection_id: str,
        principal: ServicePrincipal = Depends(require_service),
    ):
        try:
            connection = service.get_account_connection(
                connection_id, owner_id=principal.caller_id
            )
        except AccountConnectionNotFoundError as exc:
            raise HTTPException(status_code=404, detail='account connection not found') from exc
        return _connection_response(connection)

    @app.post(
        '/v1/account-connections/{connection_id}/reconnect',
        response_model=AccountConnectionResponse,
    )
    def reconnect_account_connection(
        connection_id: str,
        body: ReconnectAccountConnectionBody,
        principal: ServicePrincipal = Depends(require_service),
    ):
        try:
            connection = service.reconnect_account_connection(
                connection_id, body, owner_id=principal.caller_id
            )
        except AccountConnectionNotFoundError as exc:
            raise HTTPException(status_code=404, detail='account connection not found') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _connection_response(connection)

    @app.delete('/v1/account-connections/{connection_id}', status_code=204)
    def revoke_account_connection(
        connection_id: str,
        principal: ServicePrincipal = Depends(require_service),
    ):
        try:
            service.revoke_account_connection(
                connection_id, owner_id=principal.caller_id
            )
        except AccountConnectionNotFoundError as exc:
            raise HTTPException(status_code=404, detail='account connection not found') from exc
        return Response(status_code=204)

    @app.post('/v1/sessions', response_model=SessionResponse, status_code=201)
    def create_session(
        body: CreateSessionBody,
        response: Response,
        principal: ServicePrincipal = Depends(require_service),
    ):
        try:
            handle = service.create_session(body, owner_id=principal.caller_id)
        except ActiveSessionExistsError as exc:
            raise HTTPException(status_code=409, detail='active session already exists') from exc
        response.headers['Deprecation'] = 'true'
        response.headers['Sunset'] = 'Tue, 01 Dec 2026 00:00:00 GMT'
        return SessionResponse(
            internal_session_id=handle.internal_session_id,
            account_id=handle.account_id,
            account_key=handle.account_key,
            status=handle.status,
            created_at=handle.created_at.isoformat(),
            expires_at=handle.expires_at.isoformat(),
        )

    @app.post('/v1/sessions/revoke')
    def revoke_session(
        body: RevokeSessionBody,
        response: Response,
        principal: ServicePrincipal = Depends(require_service),
    ):
        try:
            record = service.revoke_session(
                body.internal_session_id.get_secret_value(),
                owner_id=principal.caller_id,
            )
        except (SessionNotFoundError, SessionUnavailableError, ResourceOwnershipError) as exc:
            raise HTTPException(status_code=404, detail='session not found') from exc
        response.headers['Deprecation'] = 'true'
        return {'status': record.status}

    @app.post('/v1/jobs', response_model=JobAcceptedResponse, status_code=202)
    def create_job(
        body: CreateJobBody,
        principal: ServicePrincipal = Depends(require_service),
        idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'),
    ):
        if not idempotency_key or not (8 <= len(idempotency_key) <= 256):
            raise HTTPException(status_code=400, detail='valid Idempotency-Key is required')
        try:
            job = service.create_job(
                body,
                owner_id=principal.caller_id,
                idempotency_key=idempotency_key,
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail='idempotency key conflict') from exc
        except AccountConnectionNotFoundError as exc:
            raise HTTPException(status_code=404, detail='account connection not found') from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JobAcceptedResponse(
            job_id=job.job_id,
            status=job.status,
            current_stage=job.current_stage,
            worker_slot_id=(
                str(job.parameters.get('worker_slot_id'))
                if job.parameters.get('worker_slot_id') else None
            ),
        )

    @app.get('/v1/jobs/{job_id}', response_model=JobStatusResponse)
    def get_job(job_id: str, principal: ServicePrincipal = Depends(require_service)):
        try:
            job = service.get_job(job_id, owner_id=principal.caller_id)
        except (JobNotFoundError, ResourceOwnershipError) as exc:
            raise HTTPException(status_code=404, detail='job not found') from exc
        return _job_status(job)

    @app.get('/v1/jobs/{job_id}/summary', response_model=JobSummaryResponse)
    def get_summary(job_id: str, principal: ServicePrincipal = Depends(require_service)):
        try:
            return service.get_summary(job_id, owner_id=principal.caller_id)
        except (JobNotFoundError, ResourceOwnershipError) as exc:
            raise HTTPException(status_code=404, detail='job not found') from exc

    @app.get('/v1/jobs/{job_id}/results/overview')
    def get_overview_results(
        job_id: str,
        limit: int = Query(200, ge=1, le=1000),
        cursor: str | None = None,
        principal: ServicePrincipal = Depends(require_service),
    ):
        try:
            return service.get_overview_results(
                job_id, owner_id=principal.caller_id, limit=limit, cursor=cursor
            )
        except (JobNotFoundError, ResourceOwnershipError) as exc:
            raise HTTPException(status_code=404, detail='job not found') from exc
        except ResultNotReadyError as exc:
            return _error(409, 'result_not_ready', str(exc), '')
        except InvalidResultCursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get('/v1/jobs/{job_id}/results/details')
    def get_detail_results(
        job_id: str,
        limit: int = Query(200, ge=1, le=1000),
        cursor: str | None = None,
        principal: ServicePrincipal = Depends(require_service),
    ):
        try:
            return service.get_detail_results(
                job_id, owner_id=principal.caller_id, limit=limit, cursor=cursor
            )
        except (JobNotFoundError, ResourceOwnershipError) as exc:
            raise HTTPException(status_code=404, detail='job not found') from exc
        except ResultNotReadyError as exc:
            return _error(409, 'result_not_ready', str(exc), '')
        except InvalidResultCursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post('/v1/jobs/{job_id}/cancel', response_model=JobStatusResponse)
    def cancel_job(job_id: str, principal: ServicePrincipal = Depends(require_service)):
        try:
            return _job_status(
                service.cancel_job(job_id, owner_id=principal.caller_id)
            )
        except (JobNotFoundError, ResourceOwnershipError) as exc:
            raise HTTPException(status_code=404, detail='job not found') from exc

    return app


def _connection_response(connection, *, reused: bool = False):
    return AccountConnectionResponse(
        connection_id=connection.connection_id,
        username=connection.username,
        status=connection.status,
        token_generation=connection.token_generation,
        created_at=connection.created_at.isoformat(),
        updated_at=connection.updated_at.isoformat(),
        reused=reused,
    )


def _job_status(job):
    from app.job_engine.progress import current_month_public

    error = None
    if job.last_error_code:
        error = {
            'code': job.last_error_code,
            'message': _public_job_error_message(
                job.last_error_code, job.last_error_message
            ),
            'retryable': job.last_error_code in {
                'worker_restarted', 'worker_lease_expired',
                'auth_service_unavailable', 'source_auth_wait_timeout',
                'authentication_lease_lost', 'source_timeout',
                'source_connect_failure', 'proxy_connection_failure',
                'source_rate_limited',
            },
        }
    progress = dict(job.progress_state or {})
    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        stage=job.current_stage or progress.get('current_stage'),
        overall_percent=job.progress_percent,
        current_month=current_month_public(progress),
        updated_at=job.progress_updated_at or job.updated_at,
        error=error,
    )


_PUBLIC_JOB_ERROR_MESSAGES = {
    'invalid_source_credentials': 'Tên đăng nhập hoặc mật khẩu không đúng',
    'source_account_locked': (
        'Tài khoản đã bị khoá vì đã nhập sai thông tin quá số lần quy định'
    ),
    'source_login_rejected': 'Cổng hóa đơn từ chối đăng nhập',
    'worker_restarted': 'Worker đã khởi động lại; vui lòng tạo job mới',
    'worker_lease_expired': 'Worker mất lease; vui lòng tạo job mới',
}


def _public_job_error_message(error_code: str, error_message: str | None) -> str:
    message = error_message.strip() if isinstance(error_message, str) else ''
    mapped = _PUBLIC_JOB_ERROR_MESSAGES.get(error_code)
    if mapped is not None and (not message or message == error_code):
        return mapped
    return message or 'Job processing failed'


def _error(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={'error': {
            'code': code,
            'message': message,
            'request_id': request_id,
        }},
    )


def _api_must_not_authenticate(_):
    raise RuntimeError('source authentication must run in the auth service')
