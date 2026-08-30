from __future__ import annotations

from datetime import datetime
from typing import Protocol, Sequence

from app.job_engine.models import CreateJobRequest, JobRecord, JobStageSpec, StageRecord


class JobEngineRepository(Protocol):
    """Persistence contract for the job-only sequential pipeline."""

    def migrate(self) -> None: ...
    def ping(self) -> bool: ...
    def create_job(
        self, request: CreateJobRequest, tasks: Sequence = (), *,
        stages: Sequence[JobStageSpec] | None = None,
        now: datetime | None = None,
    ) -> JobRecord: ...
    def create_admitted_job(
        self, request: CreateJobRequest, tasks: Sequence = (), *,
        stages: Sequence[JobStageSpec] | None = None,
        lease_seconds: int | None = None,
        now: datetime | None = None,
    ) -> JobRecord: ...
    def get_job(self, job_id: str) -> JobRecord: ...
    def get_stages(self, job_id: str) -> list[StageRecord]: ...
    def claim_next_job(
        self, worker_id: str, *, lease_seconds: int,
        now: datetime | None = None,
    ) -> JobRecord | None: ...
    def renew_job_lease(
        self, job_id: str, worker_id: str, lease_token: str, *,
        lease_seconds: int, now: datetime | None = None,
    ) -> JobRecord: ...
    def request_cancellation(
        self, job_id: str, *, now: datetime | None = None,
    ) -> JobRecord: ...
    def persist_pipeline_progress(
        self, job_id: str, worker_id: str, lease_token: str, state: dict, *,
        now: datetime | None = None,
    ) -> JobRecord: ...
    def finish_pipeline_job(
        self, job_id: str, worker_id: str, lease_token: str, state: dict, *,
        warning_count: int = 0, now: datetime | None = None,
    ) -> JobRecord: ...
    def terminalize_pipeline_job(
        self, job_id: str, worker_id: str, lease_token: str, *, status: str,
        error_code: str | None = None, error_message: str | None = None,
        now: datetime | None = None,
    ) -> JobRecord: ...
    def requeue_pipeline_job(
        self, job_id: str, worker_id: str, lease_token: str, *,
        now: datetime | None = None,
    ) -> JobRecord: ...


ControlRepository = JobEngineRepository
