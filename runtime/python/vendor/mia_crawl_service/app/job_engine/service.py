from __future__ import annotations

import logging
import re
import threading
import time

from app.job_engine.models import (
    InvalidJobTransitionError,
    LeaseLostError,
    WorkerRunResult,
)
from app.job_engine.interruptions import (
    SourceRouteFailoverRequested,
    UserCancellationRequested,
    WorkerShutdownRequested,
)


logger = logging.getLogger('mia.job_engine')


class SequentialWorkerSupervisor:
    """Own exactly one pipeline-v2 job lease until it becomes terminal."""

    def __init__(
        self, repository, *, worker_id: str, pipeline,
        job_lease_seconds: int = 120, heartbeat_interval_seconds: float = 40,
        shutdown_grace_seconds: float = 15,
        heartbeat_retry_attempts: int = 3,
        heartbeat_retry_delay_seconds: float = 1.0,
    ) -> None:
        if heartbeat_retry_attempts < 1 or heartbeat_retry_delay_seconds < 0:
            raise ValueError('heartbeat retry policy is invalid')
        self.repository = repository
        self.worker_id = worker_id
        self.pipeline = pipeline
        self.job_lease_seconds = job_lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.heartbeat_retry_attempts = heartbeat_retry_attempts
        self.heartbeat_retry_delay_seconds = heartbeat_retry_delay_seconds
        self._stop_event = threading.Event()
        self._shutdown_started_at: float | None = None

    @property
    def shutdown_started_at(self):
        return self._shutdown_started_at

    def set_stop_event(self, event):
        self._stop_event = event

    def request_shutdown(self):
        """Request host-level shutdown (SIGTERM/SIGINT semantics)."""
        if self._shutdown_started_at is None:
            self._shutdown_started_at = time.monotonic()
            logger.info('job_engine event=shutdown_requested worker_id=%s', self.worker_id)
        self._stop_event.set()
        self.pipeline.request_shutdown()

    def run_once(self, *, now=None):
        if self._stop_event.is_set():
            return WorkerRunResult(self.worker_id, None)
        job = self.repository.claim_next_job(
            self.worker_id, lease_seconds=self.job_lease_seconds, now=now
        )
        if job is None:
            return WorkerRunResult(self.worker_id, None)
        if job.pipeline_version != 2 or not job.lease_token:
            raise InvalidJobTransitionError('worker v2 only accepts pipeline_version=2')
        token = job.lease_token
        heartbeat = _JobLeaseHeartbeat(
            self.repository, job.job_id, self.worker_id, token,
            self.job_lease_seconds, self.heartbeat_interval_seconds,
            retry_attempts=self.heartbeat_retry_attempts,
            retry_delay_seconds=self.heartbeat_retry_delay_seconds,
        )
        heartbeat.start()
        result = None
        error: BaseException | None = None
        done = threading.Event()
        heartbeat_failure_observed = False

        def invoke():
            nonlocal result, error
            try:
                result = self.pipeline.run(job, self.worker_id, token)
            except BaseException as exc:
                error = exc
            finally:
                done.set()

        thread = threading.Thread(target=invoke, name='job-pipeline', daemon=True)
        thread.start()
        while not done.wait(0.1):
            if heartbeat.failure_event.is_set() and not heartbeat_failure_observed:
                heartbeat_failure_observed = True
                logger.error(
                    'job_engine event=job_heartbeat_failed job_id=%s '
                    'worker_slot_id=%s error_code=%s host_shutdown=false',
                    job.job_id, self.worker_id, heartbeat.failure_code,
                )
                # A job lease is a slot-local fencing primitive. Losing one
                # lease must never set the process-wide stop_event shared by
                # every logical worker. Ask only this pipeline to stop at its
                # next interruption point; repository fencing remains the
                # final authority if an in-flight source request returns late.
                request_lease_abort = getattr(
                    self.pipeline, 'request_lease_abort', None
                )
                if callable(request_lease_abort):
                    request_lease_abort()
                else:
                    # Compatibility for older/custom pipelines that expose
                    # only request_shutdown(). Calling the pipeline method
                    # directly interrupts this one invocation without setting
                    # the supervisor's shared host stop_event.
                    request_pipeline_shutdown = getattr(
                        self.pipeline, 'request_shutdown', None
                    )
                    if callable(request_pipeline_shutdown):
                        request_pipeline_shutdown()
            if self._stop_event.is_set():
                self.request_shutdown()
                deadline = self._shutdown_started_at + self.shutdown_grace_seconds
                logger.info(
                    'job_engine event=task_shutdown_grace_started job_id=%s', job.job_id
                )
                if time.monotonic() >= deadline:
                    heartbeat.stop(deadline)
                    logger.info(
                        'job_engine event=task_abandoned_for_shutdown job_id=%s', job.job_id
                    )
                    return self._terminalize_abandoned_job(
                        job, token, heartbeat=heartbeat
                    )
        heartbeat.stop(time.monotonic() + 1)
        if (
            heartbeat.failure_event.is_set()
            and not (
                result is not None
                and result.status in ('completed', 'completed_with_warning')
            )
        ):
            return self._terminalize_heartbeat_failure(job, token, heartbeat)
        if error is not None:
            if isinstance(error, SourceRouteFailoverRequested):
                result = self.repository.failover_pipeline_job(
                    job.job_id, self.worker_id, token,
                    failed_proxy_id=error.proxy_id,
                    endpoint=error.endpoint,
                    source_error_code=error.source_error_code,
                    lease_seconds=self.job_lease_seconds,
                )
            elif isinstance(error, WorkerShutdownRequested):
                result = self.repository.requeue_pipeline_job(
                    job.job_id, self.worker_id, token,
                )
            elif isinstance(error, UserCancellationRequested):
                result = self.repository.terminalize_pipeline_job(
                    job.job_id, self.worker_id, token, status='cancelled'
                )
            else:
                result = self.repository.terminalize_pipeline_job(
                    job.job_id, self.worker_id, token, status='failed',
                    error_code=str(getattr(error, 'code', type(error).__name__)),
                    error_message=redact_error_message(str(error)),
                )
        return WorkerRunResult(self.worker_id, result)

    def _terminalize_abandoned_job(self, job, token, *, heartbeat):
        if heartbeat.failure_event.is_set():
            return self._terminalize_heartbeat_failure(job, token, heartbeat)
        result = self.repository.requeue_pipeline_job(
            job.job_id, self.worker_id, token,
        )
        return WorkerRunResult(self.worker_id, result, lease_lost=True)

    def _terminalize_heartbeat_failure(self, job, token, heartbeat):
        if heartbeat.failure_code == 'job_lease_lost':
            return WorkerRunResult(
                self.worker_id, self.repository.get_job(job.job_id), lease_lost=True
            )
        try:
            result = self.repository.terminalize_pipeline_job(
                job.job_id, self.worker_id, token, status='failed',
                error_code='job_lease_heartbeat_failed',
                error_message='Job lease heartbeat could not be renewed',
            )
        except LeaseLostError:
            return WorkerRunResult(
                self.worker_id, self.repository.get_job(job.job_id), lease_lost=True
            )
        return WorkerRunResult(self.worker_id, result, lease_lost=True)


class _JobLeaseHeartbeat:
    def __init__(
        self, repository, job_id, worker_id, token, lease_seconds, interval, *,
        retry_attempts=3, retry_delay_seconds=1.0,
    ):
        self.repository = repository
        self.job_id = job_id
        self.worker_id = worker_id
        self.token = token
        self.lease_seconds = lease_seconds
        self.interval = interval
        self.retry_attempts = retry_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self._stop = threading.Event()
        self.failure_event = threading.Event()
        self.failure_code: str | None = None
        self._thread = threading.Thread(
            target=self._run, name='job-lease-heartbeat', daemon=True
        )

    def start(self):
        self._thread.start()

    def stop(self, deadline):
        self._stop.set()
        self._thread.join(max(0, deadline - time.monotonic()))
        if self._thread.is_alive():
            logger.warning('job_engine event=heartbeat_stop_timeout job_id=%s', self.job_id)

    def _run(self):
        while not self._stop.wait(self.interval):
            for attempt in range(1, self.retry_attempts + 1):
                try:
                    self.repository.renew_job_lease(
                        self.job_id, self.worker_id, self.token,
                        lease_seconds=self.lease_seconds,
                    )
                    break
                except LeaseLostError:
                    self.failure_code = 'job_lease_lost'
                    self.failure_event.set()
                    return
                except Exception as error:
                    if attempt >= self.retry_attempts:
                        self.failure_code = 'job_lease_heartbeat_failed'
                        self.failure_event.set()
                        logger.error(
                            'job_engine event=job_heartbeat_renew_failed job_id=%s '
                            'error_type=%s attempts=%s',
                            self.job_id, type(error).__name__, attempt,
                        )
                        return
                    if self._stop.wait(self.retry_delay_seconds):
                        return


_SECRET_PATTERNS = (
    re.compile(r'(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?([^\s,;]+)'),
    re.compile(r'(?i)(bearer\s+)([^\s,;]+)'),
    re.compile(
        r'(?i)((?:password|passwd|api[_-]?key|apikey|[a-z_-]*token|credential|secret)'
        r'\s*[:=]\s*)([^\s,;]+)'
    ),
    re.compile(r'(?i)(https?://)([^/@\s:]+):([^/@\s]+)@'),
)


def redact_error_message(message: str) -> str:
    redacted = message.replace('\r', ' ').replace('\n', ' ')
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith('(?i)(https?'):
            redacted = pattern.sub(r'\1***:***@', redacted)
        else:
            redacted = pattern.sub(r'\1***', redacted)
    return redacted[:2000]