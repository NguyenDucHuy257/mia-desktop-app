from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import (
    BaseModel, ConfigDict, Field, SecretStr, StrictBool,
    field_validator, model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CreateAccountConnectionBody(StrictModel):
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr

    @field_validator('username')
    @classmethod
    def strip_username(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError('username must not be blank')
        return stripped


class ReconnectAccountConnectionBody(CreateAccountConnectionBody):
    pass


class AccountConnectionResponse(StrictModel):
    connection_id: str
    username: str
    status: str
    token_generation: int
    created_at: str
    updated_at: str
    reused: bool = False


# Legacy session contract retained as a controlled compatibility adapter.
class CreateSessionBody(StrictModel):
    account_key: str = Field(min_length=1, max_length=256)
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr
    proxy_url: SecretStr | None = None
    ttl_seconds: int = Field(default=28800, ge=60, le=86400)

    @field_validator('account_key', 'username')
    @classmethod
    def strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError('value must not be blank')
        return stripped


class RevokeSessionBody(StrictModel):
    internal_session_id: SecretStr


class CreateJobBody(StrictModel):
    connection_id: str = Field(min_length=6, max_length=128)
    date_from: date
    date_to: date
    directions: list[Literal['purchase', 'sold']] = Field(min_length=1, max_length=2)
    query_types: list[Literal['query', 'sco-query']] = Field(min_length=1, max_length=2)
    force_refresh: StrictBool = False
    refresh_latest_month: StrictBool = False
    result_scope: Literal['overview', 'detail'] = 'detail'
    include_xml: StrictBool = False
    include_mvt: StrictBool = False

    @field_validator('connection_id')
    @classmethod
    def validate_connection_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith('conn_'):
            raise ValueError('connection_id is invalid')
        return normalized

    @field_validator('directions', 'query_types')
    @classmethod
    def unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError('values must be unique')
        return value

    @model_validator(mode='after')
    def validate_options(self) -> 'CreateJobBody':
        if self.date_from > self.date_to:
            raise ValueError('date_from must be on or before date_to')
        if self.result_scope == 'overview' and (self.include_xml or self.include_mvt):
            raise ValueError(
                'include_xml and include_mvt require result_scope=detail'
            )
        return self


class SessionResponse(StrictModel):
    internal_session_id: str
    account_id: str
    account_key: str
    status: str
    created_at: str
    expires_at: str


class JobAcceptedResponse(StrictModel):
    job_id: str
    status: str
    current_stage: str | None
    worker_slot_id: str | None = None


class JobProgressError(StrictModel):
    code: str
    message: str
    retryable: bool


class CurrentMonthProgress(StrictModel):
    key: str
    index: int
    total: int
    processed: int
    planned: int
    percent: float


class JobStatusResponse(StrictModel):
    job_id: str
    status: str
    stage: str | None
    overall_percent: float
    current_month: CurrentMonthProgress | None
    updated_at: str
    error: JobProgressError | None = None


class StageSummary(StrictModel):
    stage: str
    status: str
    progress_percent: float


class JobSummaryResponse(StrictModel):
    job_id: str
    status: str
    warning_count: int
    stages: list[StageSummary]
    coverage_plan: dict = Field(default_factory=dict)
    work: dict = Field(default_factory=dict)
    post_processing: dict = Field(default_factory=dict)
