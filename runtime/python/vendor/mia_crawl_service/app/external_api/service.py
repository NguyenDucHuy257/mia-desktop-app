from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from app.account_connections.models import AccountConnection
from app.account_connections.service import AccountConnectionManager
from app.external_api.models import (
    CreateAccountConnectionBody,
    CreateJobBody,
    CreateSessionBody,
    ReconnectAccountConnectionBody,
)
from app.external_api.results import JobResultReader
from app.job_engine.contracts import JobEngineRepository
from app.job_engine.models import CreateJobRequest, JobRecord, JobStageSpec
from app.job_engine.progress import build_pipeline_plan, module_percent
from app.session_manager.models import CredentialBundle, PersistedSession
from app.session_manager.service import SessionTokenManager
from app.utils.date_utils import BUSINESS_TIMEZONE
from app.worker_runtime.coverage_planner import CoveragePlanner


class ResourceOwnershipError(RuntimeError):
    pass


class ResultNotReadyError(RuntimeError):
    pass


class ExternalApiService:
    def __init__(
        self,
        job_repository: JobEngineRepository,
        session_manager: SessionTokenManager,
        account_connections: AccountConnectionManager,
        coverage_planner: CoveragePlanner | None = None,
        business_clock=None,
    ) -> None:
        self.job_repository = job_repository
        self.session_manager = session_manager
        self.account_connections = account_connections
        self.coverage_planner = coverage_planner or CoveragePlanner(
            Path(os.getenv('MIA_DATA_ROOT', 'data'))
        )
        self.business_clock = business_clock or (
            lambda: datetime.now(BUSINESS_TIMEZONE)
        )

    def initialize(self) -> None:
        self.account_connections.initialize()
        self.job_repository.migrate()

    def create_account_connection(
        self, body: CreateAccountConnectionBody, *, owner_id: str
    ) -> tuple[AccountConnection, bool]:
        return self.account_connections.create(
            username=body.username,
            password=body.password.get_secret_value(),
            owner_id=owner_id,
        )

    def get_account_connection(
        self, connection_id: str, *, owner_id: str
    ) -> AccountConnection:
        return self.account_connections.get(connection_id, owner_id=owner_id)

    def reconnect_account_connection(
        self, connection_id: str, body: ReconnectAccountConnectionBody, *, owner_id: str
    ) -> AccountConnection:
        return self.account_connections.reconnect(
            connection_id,
            username=body.username,
            password=body.password.get_secret_value(),
            owner_id=owner_id,
        )

    def revoke_account_connection(
        self, connection_id: str, *, owner_id: str
    ) -> AccountConnection:
        return self.account_connections.revoke(connection_id, owner_id=owner_id)

    # Compatibility adapter. New callers must use account-connections.
    def create_session(self, body: CreateSessionBody, *, owner_id: str):
        proxy = body.proxy_url.get_secret_value() if body.proxy_url else None
        return self.session_manager.create_session(
            CredentialBundle(
                account_key=body.account_key,
                username=body.username,
                password=body.password.get_secret_value(),
                proxy_url=proxy,
            ),
            ttl_seconds=body.ttl_seconds,
            owner_id=owner_id,
        )

    def revoke_session(self, internal_session_id: str, *, owner_id: str) -> PersistedSession:
        session = self.session_manager.get_session(internal_session_id)
        self._assert_owner(session.owner_id, owner_id)
        return self.session_manager.revoke_session(internal_session_id)

    def create_job(
        self, body: CreateJobBody, *, owner_id: str, idempotency_key: str
    ) -> JobRecord:
        connection, session_hash = self.account_connections.session_hash(
            body.connection_id, owner_id=owner_id
        )
        directions = sorted(body.directions)
        query_types = sorted(body.query_types)
        normalized = {
            'connection_id': connection.connection_id,
            'session_hash': session_hash,
            'company_tax_code': connection.username,
            'date_from': body.date_from.isoformat(),
            'date_to': body.date_to.isoformat(),
            'directions': directions,
            'query_types': query_types,
            'force_refresh': body.force_refresh,
            'refresh_latest_month': body.refresh_latest_month,
            'result_scope': body.result_scope,
            'include_xml': body.include_xml,
            'include_mvt': body.include_mvt,
        }
        fingerprint = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(',', ':')).encode()
        ).hexdigest()
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        parameters = dict(normalized)
        pipeline_plan = build_pipeline_plan(
            body.result_scope,
            body.include_mvt,
            body.include_xml,
            date_from=body.date_from,
            date_to=body.date_to,
        )
        parameters['pipeline_plan'] = pipeline_plan
        coverage_plan = self.coverage_planner.plan(
            company_tax_code=connection.username,
            date_from=body.date_from,
            date_to=body.date_to,
            directions=directions,
            query_types=query_types,
            business_now=self.business_clock(),
            force_refresh=body.force_refresh,
            force_slices=self._latest_month_force_slices(
                body.date_from, body.date_to, directions, query_types
            ) if body.refresh_latest_month and not body.force_refresh else frozenset(),
        )
        parameters['coverage_plan'] = coverage_plan.summary()
        parameters['detail_refresh_from'] = max(
            body.date_from, coverage_plan.cutoff_date
        ).isoformat()
        stages = [JobStageSpec(item['name']) for item in pipeline_plan['stages']]
        return self.job_repository.create_admitted_job(
            CreateJobRequest(
                account_key=connection.connection_id,
                company_tax_code=connection.username,
                job_type='invoice_crawl',
                parameters=parameters,
                owner_id=owner_id,
                idempotency_key_hash=key_hash,
                request_fingerprint=fingerprint,
                pipeline_version=2,
            ),
            (),
            stages=stages,
        )

    @staticmethod
    def _latest_month_force_slices(date_from, date_to, directions, query_types):
        month_from = date_to.replace(day=1)
        begin = max(date_from, month_from)
        end = date_to
        return frozenset(
            (direction, query_type, begin, end)
            for direction in directions for query_type in query_types
        )

    def get_job(self, job_id: str, *, owner_id: str) -> JobRecord:
        job = self.job_repository.get_job(job_id)
        self._assert_owner(job.owner_id, owner_id)
        return job

    def get_summary(self, job_id: str, *, owner_id: str) -> dict[str, object]:
        job = self.get_job(job_id, owner_id=owner_id)
        progress = job.progress_state or {}
        stage_results = progress.get('stage_results', {})
        modules = progress.get('modules', {})
        plan = progress.get('pipeline_plan') or job.parameters.get('pipeline_plan')
        stage_names = [item['name'] for item in plan.get('stages', [])] if plan else [
            'auth', 'overview', 'detail', 'finalize'
        ]
        summaries = [{
            'stage': name,
            'status': (
                modules.get(name, {}).get('status') if name in modules else
                'completed' if name == 'auth' and float(progress.get('auth_percent', 0)) >= 100 else
                'completed' if name == 'finalize' and float(progress.get('finalize_percent', 0)) >= 100 else
                stage_results.get(name, {}).get('status', 'pending')
            ),
            'progress_percent': float(
                module_percent(modules[name]) if name in modules else
                progress.get(
                    'auth_percent' if name == 'auth' else 'finalize_percent',
                    stage_results.get(name, {}).get('progress_percent', 0),
                )
            ),
        } for name in stage_names]
        return {
            'job_id': job.job_id,
            'status': job.status,
            'warning_count': job.warning_count,
            'coverage_plan': job.parameters.get('coverage_plan', {}),
            'work': progress,
            'stages': summaries,
            'post_processing': {
                'material_code_extraction': (
                    'included_in_job' if job.parameters.get('include_mvt')
                    else 'offline_not_run_by_job'
                ),
                'invoice_excel_export': 'offline_not_run_by_job',
                'invoice_pdf_export': 'offline_not_run_by_job',
            },
        }

    def cancel_job(self, job_id: str, *, owner_id: str) -> JobRecord:
        self.get_job(job_id, owner_id=owner_id)
        return self.job_repository.request_cancellation(job_id)

    def get_overview_results(self, job_id, *, owner_id, limit, cursor=None):
        job = self.get_job(job_id, owner_id=owner_id)
        self._require_stage_ready(job, 'overview')
        page = self._result_reader(job).overview_page(job, limit=limit, cursor=cursor)
        return self._result_envelope(job, 'overview', page)

    def get_detail_results(self, job_id, *, owner_id, limit, cursor=None):
        job = self.get_job(job_id, owner_id=owner_id)
        if job.parameters.get('result_scope', 'detail') != 'detail':
            raise ResultNotReadyError('detail results were not requested')
        self._require_stage_ready(job, 'detail')
        if job.parameters.get('include_mvt'):
            self._require_stage_ready(job, 'mvt')
        page = self._result_reader(job).detail_page(job, limit=limit, cursor=cursor)
        page['mvt'] = {
            'requested': bool(job.parameters.get('include_mvt')),
            'guaranteed_complete': bool(
                job.parameters.get('include_mvt') and job.status == 'completed'
            ),
        }
        return self._result_envelope(job, 'details', page)

    def _result_reader(self, job):
        return JobResultReader(
            self.coverage_planner.data_root / job.company_tax_code
            / 'db' / 'invoices.sqlite3'
        )

    @staticmethod
    def _require_stage_ready(job, stage):
        progress = job.progress_state or {}
        status = progress.get('modules', {}).get(stage, {}).get('status')
        if status is None:
            status = progress.get('stage_results', {}).get(stage, {}).get('status')
        if status not in {
            'completed', 'completed_with_warning', 'skipped_no_work',
            'skipped_no_source_work',
        }:
            raise ResultNotReadyError(f'{stage} result is not ready')

    @staticmethod
    def _result_envelope(job, result_type, page):
        return {
            'job_id': job.job_id,
            'result_type': result_type,
            'company_tax_code': job.company_tax_code,
            'date_from': job.parameters['date_from'],
            'date_to': job.parameters['date_to'],
            'directions': job.parameters['directions'],
            'query_types': job.parameters['query_types'],
            'count': len(page['items']),
            **page,
        }

    @staticmethod
    def _assert_owner(actual: str | None, expected: str) -> None:
        if actual != expected:
            raise ResourceOwnershipError('resource not found')
