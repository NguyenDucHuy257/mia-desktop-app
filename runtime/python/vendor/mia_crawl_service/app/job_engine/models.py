from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit


# queued/waiting_account remain readable for legacy migrations only. New public
# jobs are admitted directly into running or rejected before insertion.
JOB_ACTIVE_STATES = ('queued', 'waiting_account', 'running', 'cancelling')
JOB_TERMINAL_STATES = (
    'cancelled', 'completed', 'completed_with_warning', 'failed', 'abandoned'
)
STAGE_TERMINAL_STATES = ('cancelled', 'completed', 'completed_with_warning', 'failed')
TASK_TERMINAL_STATES = ('completed', 'failed_terminal', 'cancelled')
FAILURE_POLICIES = ('fail_job', 'continue_with_warning')

_SECRET_KEYS = {
    'authorization', 'bearer', 'password', 'passwd', 'access_token',
    'refresh_token', 'api_key', 'apikey', 'secret', 'credential',
    'credentials', 'proxy_password',
}
_BEARER_VALUE = re.compile(r'^\s*bearer\s+\S+', re.IGNORECASE)


class JobEngineError(RuntimeError):
    """Base error for durable control-state operations."""


class JobNotFoundError(JobEngineError):
    pass


class LeaseLostError(JobEngineError):
    pass


class InvalidJobTransitionError(JobEngineError):
    pass


class IdempotencyConflictError(JobEngineError):
    pass


class AccountBusyError(JobEngineError):
    code = 'account_busy'

    def __init__(self, current_job_id: str, retry_after_seconds: int = 30) -> None:
        self.current_job_id = current_job_id
        self.retry_after_seconds = retry_after_seconds
        super().__init__('account is already running another job')


class CapacityExhaustedError(JobEngineError):
    code = 'capacity_exhausted'

    def __init__(
        self, *, available_slots: int, total_slots: int,
        retry_after_seconds: int = 15,
    ) -> None:
        self.available_slots = available_slots
        self.total_slots = total_slots
        self.retry_after_seconds = retry_after_seconds
        super().__init__('no worker slot is currently available')


@dataclass(frozen=True)
class JobStageSpec:
    stage_name: str
    task_generation_completed: bool = True

    def __post_init__(self) -> None:
        if not self.stage_name or not self.stage_name.strip():
            raise ValueError('stage_name must not be empty')


@dataclass(frozen=True)
class JobTaskSpec:
    task_key: str
    stage_name: str
    task_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    max_attempts: int = 3
    failure_policy: str = 'fail_job'

    def __post_init__(self) -> None:
        for field_name, value in (
            ('task_key', self.task_key), ('stage_name', self.stage_name),
            ('task_type', self.task_type),
        ):
            if not value or not value.strip():
                raise ValueError(f'{field_name} must not be empty')
        if self.max_attempts < 1:
            raise ValueError('max_attempts must be at least 1')
        if self.failure_policy not in FAILURE_POLICIES:
            raise ValueError(f'unsupported failure_policy: {self.failure_policy}')
        validate_safe_payload(self.payload)


@dataclass(frozen=True)
class CreateJobRequest:
    account_key: str
    company_tax_code: str
    job_type: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    owner_id: str | None = None
    idempotency_key_hash: str | None = field(default=None, repr=False)
    request_fingerprint: str | None = field(default=None, repr=False)
    pipeline_version: int = 2

    def __post_init__(self) -> None:
        for field_name, value in (
            ('account_key', self.account_key),
            ('company_tax_code', self.company_tax_code),
            ('job_type', self.job_type),
        ):
            if not value or not value.strip():
                raise ValueError(f'{field_name} must not be empty')
        validate_safe_payload(self.parameters)
        if self.pipeline_version not in (1, 2):
            raise ValueError('pipeline_version must be 1 or 2')
        idempotency_values = (
            self.owner_id, self.idempotency_key_hash, self.request_fingerprint,
        )
        if any(value is not None for value in idempotency_values):
            if not all(value and value.strip() for value in idempotency_values):
                raise ValueError('owner and idempotency metadata must be supplied together')
            if len(self.idempotency_key_hash or '') != 64:
                raise ValueError('idempotency_key_hash must be a SHA-256 hex digest')
            if len(self.request_fingerprint or '') != 64:
                raise ValueError('request_fingerprint must be a SHA-256 hex digest')


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    account_key: str
    company_tax_code: str
    job_type: str
    queue_order: int
    parameters: Mapping[str, Any]
    status: str
    current_stage: str | None
    worker_id: str | None
    lease_token: str | None
    lease_generation: int
    lease_expires_at: str | None
    available_at: str
    cancel_requested_at: str | None
    warning_count: int
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    last_error_code: str | None
    last_error_message: str | None
    progress_percent: float = 0.0
    total_tasks: int = 0
    pending_tasks: int = 0
    running_tasks: int = 0
    succeeded_tasks: int = 0
    failed_tasks: int = 0
    skipped_tasks: int = 0
    progress_message: str | None = None
    owner_id: str | None = None
    pipeline_version: int = 1
    stage_progress_percent: float = 0.0
    progress_state: Mapping[str, Any] | None = None
    progress_updated_at: str | None = None


@dataclass(frozen=True)
class StageRecord:
    stage_name: str
    sequence_number: int
    status: str
    task_generation_completed: bool
    started_at: str | None
    finished_at: str | None
    last_error_code: str | None
    last_error_message: str | None


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    job_id: str
    task_key: str
    stage_name: str
    task_type: str
    payload: Mapping[str, Any]
    status: str
    failure_policy: str
    worker_id: str | None
    lease_token: str | None
    lease_generation: int
    lease_expires_at: str | None
    attempt_count: int
    max_attempts: int
    next_retry_at: str | None
    last_error_code: str | None
    last_error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskInsertResult:
    job_id: str
    stage_name: str
    requested_count: int
    inserted_count: int
    task_generation_completed: bool


@dataclass(frozen=True)
class RecoveryReport:
    recovered_jobs: int = 0
    recovered_tasks: int = 0
    failed_tasks: int = 0
    cancelled_jobs: int = 0
    promoted_jobs: int = 0


@dataclass(frozen=True)
class StorageReconciliationReport:
    scanned_jobs: int = 0
    scanned_tasks: int = 0
    completed_tasks: int = 0
    requeued_tasks: int = 0
    repaired_artifacts: int = 0
    errors: int = 0


@dataclass(frozen=True)
class WorkerRunResult:
    worker_id: str
    job: JobRecord | None
    processed_tasks: int = 0
    lease_lost: bool = False


def validate_safe_payload(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping):
        raise TypeError('job payload must be a mapping')
    _check_secret_fields(payload)
    try:
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError('job payload must be JSON serializable') from exc


def _check_secret_fields(value: Any, path: str = 'payload') -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = _normalize_key(raw_key)
            if key in _SECRET_KEYS:
                raise ValueError(f'secret field is not allowed in durable {path}')
            if key in {'proxy', 'proxy_url'} and _contains_url_credentials(nested):
                raise ValueError(f'credentialed proxy URL is not allowed in durable {path}')
            _check_secret_fields(nested, f'{path}.{key or "field"}')
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _check_secret_fields(nested, f'{path}[{index}]')
    elif isinstance(value, str):
        if _BEARER_VALUE.match(value):
            raise ValueError(f'bearer credential is not allowed in durable {path}')
        if _contains_url_credentials(value):
            raise ValueError(f'credentialed URL is not allowed in durable {path}')


def _normalize_key(value: object) -> str:
    return re.sub(
        r'[^a-z0-9]+', '_', str(value).strip().casefold()
    ).strip('_')


def _contains_url_credentials(value: object) -> bool:
    if not isinstance(value, str) or '://' not in value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.username is not None or parsed.password is not None


def mask_lease_token(token: str | None) -> str | None:
    if not token:
        return None
    return f'{token[:8]}…'
