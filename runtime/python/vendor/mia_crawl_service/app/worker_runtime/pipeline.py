from __future__ import annotations

import logging
import threading
import time
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal

from app.job_engine.interruptions import (
    PipelineInterruption,
    UserCancellationRequested,
    WorkerShutdownRequested,
)
from app.job_engine.models import JobRecord
from app.job_engine.progress import (
    HUNDRED,
    build_pipeline_plan,
    decimal4,
    initial_progress_state,
    month_percent,
)
from app.worker_runtime.coverage_planner import CoveragePlanner
from app.worker_runtime.metrics import WorkerMetrics
from app.crawlers.diagnostics import job_diagnostics


logger = logging.getLogger('mia.worker_runtime.pipeline')
PipelineCancelled = UserCancellationRequested


class ProgressPersistenceThrottle:
    """Persist item-granular calculation without one transaction per item."""

    def __init__(self, *, item_batch: int = 20, interval_seconds: float = 0.4):
        self.item_batch = item_batch
        self.interval_seconds = interval_seconds
        self.pending_items = 0
        self.last_persisted = 0.0
        self._lock = threading.Lock()

    def should_persist(self, *, item_delta=0, force=False) -> bool:
        with self._lock:
            self.pending_items += item_delta
            now = time.monotonic()
            due = (
                force or self.pending_items >= self.item_batch
                or now - self.last_persisted >= self.interval_seconds
            )
            if due:
                self.pending_items = 0
                self.last_persisted = now
            return due


class InvoiceCrawlPipeline:
    """Sequential job pipeline with durable module/month progress."""

    def __init__(self, repository, core, planner: CoveragePlanner, *, clock) -> None:
        self.repository = repository
        self.core = core
        self.planner = planner
        self.clock = clock
        self.metrics = getattr(core, 'metrics', WorkerMetrics())
        self._stage_started_at: dict[str, float] = {}
        self._throttle = ProgressPersistenceThrottle()
        # This event is intentionally pipeline/job-local. It is reset for each
        # run and must never be shared between logical worker slots. The core's
        # own shutdown flag remains reserved for process-level SIGTERM/SIGINT.
        self._lease_abort_requested = threading.Event()

    @job_diagnostics('pipeline')
    def run(self, job: JobRecord, worker_id: str, lease_token: str) -> JobRecord:
        self._lease_abort_requested.clear()
        started = time.perf_counter()
        self.metrics.increment('pipeline_started')
        try:
            result = self._run_pipeline(job, worker_id, lease_token)
        except PipelineInterruption:
            self.metrics.increment('pipeline_cancelled')
            self._record_interrupted_stage('cancelled')
            self._flush_if_available(force=True)
            raise
        except Exception:
            self.metrics.increment('pipeline_failed')
            self._record_interrupted_stage('failed')
            self._flush_if_available(force=True)
            raise
        else:
            self.metrics.increment('pipeline_completed')
            return result
        finally:
            self.metrics.observe_duration('pipeline', time.perf_counter() - started)

    def _run_pipeline(self, job, worker_id, lease_token):
        self._job_id, self._worker_id, self._lease_token = (
            job.job_id, worker_id, lease_token
        )
        parameters = dict(job.parameters)
        frozen_plan = parameters.get('pipeline_plan')
        if (
            not frozen_plan or int(frozen_plan.get('version', 0)) < 3
            or not frozen_plan.get('months')
        ):
            frozen_plan = build_pipeline_plan(
                parameters.get('result_scope', 'detail'),
                bool(parameters.get('include_mvt')),
                bool(parameters.get('include_xml')),
                date_from=date.fromisoformat(parameters['date_from']),
                date_to=date.fromisoformat(parameters['date_to']),
            )
            parameters['pipeline_plan'] = frozen_plan
        existing_state = job.progress_state or {}
        self._state = deepcopy(
            existing_state
            if int(existing_state.get('version', 0)) >= 3
            and all(
                module.get('total_months') == len(frozen_plan['months'])
                for module in existing_state.get('modules', {}).values()
            )
            and existing_state.get('modules')
            else initial_progress_state(frozen_plan)
        )

        coverage = self.planner.plan(
            company_tax_code=job.company_tax_code,
            date_from=date.fromisoformat(parameters['date_from']),
            date_to=date.fromisoformat(parameters['date_to']),
            directions=list(parameters['directions']),
            query_types=list(parameters['query_types']),
            business_now=self.clock(),
            force_refresh=bool(parameters.get('force_refresh')),
            force_slices=self._latest_month_force_slices(parameters),
        )
        detail_existing = any(
            decision.action != 'skip_verified'
            for decision in self._iter_detail_plan(job, parameters, coverage)
        )
        needs_source = any(d.needs_refresh for d in coverage.decisions) or bool(
            detail_existing
        )

        self._run_auth(job, needs_source)
        overview_warnings = self._run_overview(job, parameters, coverage)
        if 'detail' not in self._state['modules']:
            return self._finalize_job(
                job, parameters, warning_count=overview_warnings
            )

        self._run_detail(job, parameters, coverage)
        warnings = overview_warnings
        if 'ensure_xml' in self._state['modules']:
            warnings += self._run_xml(job, parameters, coverage)
        if 'mvt' in self._state['modules']:
            warnings = self._run_mvt(job, parameters, coverage)
        return self._finalize_job(job, parameters, warning_count=warnings)

    def _iter_detail_plan(self, job, parameters, coverage):
        limit = parameters.get('detail_limit')
        emitted = 0
        arguments = {
            'company_tax_code': job.company_tax_code,
            'date_from': date.fromisoformat(parameters['date_from']),
            'date_to': date.fromisoformat(parameters['date_to']),
            'directions': list(parameters['directions']),
            'query_types': list(parameters['query_types']),
            'cutoff_date': coverage.cutoff_date,
            'force_refresh': bool(parameters.get('force_refresh')),
        }
        force_range = self._latest_month_range(parameters)
        if force_range is not None:
            arguments['force_range'] = force_range
        factory = getattr(
            self.planner, 'iter_detail_decisions', self.planner.plan_details
        )
        for decision in factory(**arguments):
            if limit is not None and emitted >= int(limit):
                return
            emitted += 1
            yield decision

    def _month_detail_plan(self, job, parameters, coverage, month):
        for decision in self._iter_detail_plan(job, parameters, coverage):
            if month['from_date'] <= decision.item['nlap_date'] <= month['to_date']:
                yield decision

    @staticmethod
    def _latest_month_range(parameters):
        if parameters.get('force_refresh') or not parameters.get('refresh_latest_month'):
            return None
        request_from = date.fromisoformat(parameters['date_from'])
        request_to = date.fromisoformat(parameters['date_to'])
        return max(request_from, request_to.replace(day=1)), request_to

    @classmethod
    def _latest_month_force_slices(cls, parameters):
        forced = cls._latest_month_range(parameters)
        if forced is None:
            return frozenset()
        begin, end = forced
        return frozenset(
            (direction, query_type, begin, end)
            for direction in parameters['directions']
            for query_type in parameters['query_types']
        )

    def _run_auth(self, job, needs_source):
        self._start_stage('auth')
        if needs_source:
            self._check_interrupted(job.job_id)
            milestones = {
                'session_loaded': '5.0000', 'auth_claim_started': '10.0000',
                'auth_claim_acquired': '15.0000', 'credentials_ready': '20.0000',
                'captcha_request_started': '25.0000', 'captcha_fetched': '35.0000',
                'captcha_payload_validated': '40.0000', 'captcha_solved': '55.0000',
                'login_request_started': '60.0000',
                'login_request_succeeded': '75.0000',
                'login_response_received': '80.0000',
                'login_response_validated': '85.0000',
                'login_token_received': '90.0000', 'token_persisted': '99.9999',
            }

            def progress(event):
                value = milestones.get(event)
                if value is not None:
                    self._state['auth_percent'] = value
                    self._state['message'] = f'auth:{event}'
                    self._persist(force=True)
                    self._check_interrupted(job.job_id)

            progress('session_loaded')
            progress('auth_claim_started')
            self.core.authenticate_job(job, progress)
        self._state['auth_percent'] = '100.0000'
        self._complete_stage('auth')

    def _run_overview(self, job, parameters, coverage):
        self._start_stage('overview')
        for month in self._module_months('overview'):
            if month.get('status') == 'completed':
                continue
            self._start_month('overview', month)
            decisions = [
                item for item in coverage.decisions
                if item.from_date.isoformat() == month['from_date']
                and item.to_date.isoformat() == month['to_date']
            ]
            stable = [item for item in decisions if not item.needs_refresh]
            routes = tuple(dict.fromkeys(
                (item.direction, item.query_type)
                for item in decisions if item.needs_refresh
            ))
            prepared = {}
            planned = month.get('planned')
            denominator_ready = planned is not None
            if planned is None:
                planned = sum(item.planned_items for item in stable)
                denominator_ready = not routes
                for direction, query_type in routes:
                    payload = self._overview_payload(
                        parameters, direction, query_type, month
                    )
                    if hasattr(self.core, 'prepare_overview_unit'):
                        result = self.core.prepare_overview_unit(job, payload)
                        prepared[(direction, query_type)] = result
                        planned += int(result.get('planned_items', 0))
                        denominator_ready = True
                    else:
                        denominator_ready = False
            if denominator_ready:
                self._freeze_month(planned)
            if stable:
                stable_total = sum(item.planned_items for item in stable)
                baseline = int(month.get('baseline_processed', 0))
                month['baseline_processed'] = stable_total
                self._advance_month(max(0, stable_total - baseline))
            resume_items = max(
                0,
                int(month.get('processed', 0))
                - int(month.get('baseline_processed', 0)),
            )
            for direction, query_type in routes:
                self._check_interrupted(job.job_id)
                payload = self._overview_payload(
                    parameters, direction, query_type, month
                )

                committed_by_status = {}

                stable_items = tuple(stable)

                def page_committed(
                    page, _result, unit,
                    _stable_items=stable_items,
                    _committed_by_status=committed_by_status,
                ):
                    nonlocal resume_items
                    if self._current_month()['planned'] is None:
                        self._freeze_month(int(
                            sum(item.planned_items for item in _stable_items)
                            + (page.total or len(getattr(page, 'records', ())))
                        ))
                    status = unit.get('status_filter', 'all')
                    previous = _committed_by_status.get(status, 0)
                    fetched = int(page.fetched_count)
                    amount = (
                        len(page.records) if hasattr(page, 'records')
                        else max(0, fetched - previous)
                    )
                    _committed_by_status[status] = max(previous, fetched)
                    already_counted = min(resume_items, amount)
                    resume_items -= already_counted
                    self._advance_month(amount - already_counted, force=True)
                    self.metrics.increment('pipeline_overview_pages_committed')
                    self._check_interrupted(job.job_id)

                kwargs = {
                    'interruption_check': lambda: self._check_interrupted(job.job_id),
                    'initial_payloads': prepared.get((direction, query_type), {}).get(
                        'initial_payloads'
                    ),
                }
                try:
                    unit_result = self.core.run_overview_unit(
                        job, payload, page_committed, **kwargs
                    )
                except TypeError as error:
                    if 'initial_payloads' not in str(error):
                        raise
                    kwargs.pop('initial_payloads')
                    unit_result = self.core.run_overview_unit(
                        job, payload, page_committed, **kwargs
                    )
                self._record_overview_warnings(unit_result)
                for decision in decisions:
                    if (
                        decision.needs_refresh and decision.direction == direction
                        and decision.query_type == query_type
                    ):
                        status = None if decision.status_filter == 'all' else int(
                            decision.status_filter
                        )
                        if not self.planner.storage.verify_finalized_overview_range(
                            company_tax_code=job.company_tax_code,
                            direction=direction, query_type=query_type,
                            from_date=month['from_date'], to_date=month['to_date'],
                            status=status,
                            allow_source_mismatch=(
                                self._has_matching_source_total_warning(unit_result, decision)
                            ),
                        ):
                            raise RuntimeError('overview finalized verification failed')
            self._complete_month()
        self._complete_stage('overview')
        return self._overview_warning_count()

    @staticmethod
    def _has_matching_source_total_warning(result, decision) -> bool:
        if not isinstance(result, dict): return False
        warnings = result.get('warnings')
        if not isinstance(warnings, (list, tuple)): return False
        def iso(v): return v.isoformat() if hasattr(v, 'isoformat') else str(v)
        expected={'direction':str(decision.direction),'query_type':str(decision.query_type),'from_date':iso(decision.from_date),'to_date':iso(decision.to_date),'status_filter':str(decision.status_filter)}
        for item in warnings:
            if not isinstance(item, dict) or item.get('code') != 'source_total_mismatch': continue
            actual={'direction':str(item.get('direction')),'query_type':str(item.get('query_type')),'from_date':str(item.get('from_date')),'to_date':str(item.get('to_date')),'status_filter':str(item.get('status_filter','all'))}
            if actual == expected: return True
        return False

    def _record_overview_warnings(self, result) -> None:
        if not isinstance(result, dict):
            return
        warnings = result.get('warnings')
        if not isinstance(warnings, (list, tuple)):
            return
        existing = self._state.setdefault('warnings', [])
        changed = False
        for item in warnings:
            if not isinstance(item, dict):
                continue
            warning = dict(item)
            warning.setdefault('stage', 'overview')
            if warning not in existing:
                existing.append(warning)
                changed = True
        if changed:
            self._persist(force=True)

    def _overview_warning_count(self) -> int:
        return sum(
            1 for item in self._state.get('warnings', [])
            if isinstance(item, dict) and item.get('stage') == 'overview'
        )

    def _run_detail(self, job, parameters, coverage):
        self._start_stage('detail')
        for month in self._module_months('detail'):
            if month.get('status') == 'completed':
                continue
            self._start_month('detail', month)
            planned = sum(1 for _ in self._month_detail_plan(
                job, parameters, coverage, month
            ))
            self._freeze_month(planned)
            skipped = int(month.get('processed', 0))
            for index, decision in enumerate(self._month_detail_plan(
                job, parameters, coverage, month
            )):
                if index < skipped:
                    continue
                self._check_interrupted(job.job_id)
                if decision.action != 'skip_verified':
                    payload = dict(decision.item)
                    payload.update({
                        'session_hash': parameters['session_hash'],
                        'date_from': parameters['date_from'],
                        'date_to': parameters['date_to'],
                        'force_refresh_detail': decision.force_refresh,
                    })
                    self.core.run_detail_unit(job, payload)
                self._advance_month(1)
            self._complete_month()
        self._complete_stage('detail')

    def _run_xml(self, job, parameters, coverage):
        self._start_stage('ensure_xml')
        warning_count = 0
        for month in self._module_months('ensure_xml'):
            if month.get('status') == 'completed':
                continue
            self._start_month('ensure_xml', month)
            planned = sum(1 for _ in self._month_detail_plan(
                job, parameters, coverage, month
            ))
            self._freeze_month(planned)
            skipped = int(month.get('processed', 0))
            for index, decision in enumerate(self._month_detail_plan(
                job, parameters, coverage, month
            )):
                if index < skipped:
                    continue
                self._check_interrupted(job.job_id)
                payload = dict(decision.item)
                payload.update({
                    'session_hash': parameters['session_hash'],
                    'date_from': parameters['date_from'],
                    'date_to': parameters['date_to'],
                    'export_xml': True, 'export_html': False,
                })
                outcome = self.core.run_xml_unit(job, payload)
                if isinstance(outcome, dict) and outcome.get('outcome') == 'unavailable':
                    warning_count += 1
                self._advance_month(1)
            self._complete_month()
        self._complete_stage('ensure_xml')
        return warning_count

    def _run_mvt(self, job, parameters, coverage):
        self._start_stage('mvt')
        warning_count = 0
        scopes = tuple(
            (direction, query_type)
            for direction in parameters['directions']
            for query_type in parameters['query_types']
        )
        for month in self._module_months('mvt'):
            if month.get('status') == 'completed':
                continue
            self._start_month('mvt', month)
            planned = sum(1 for _ in self._month_detail_plan(
                job, parameters, coverage, month
            ))
            self._freeze_month(planned)
            terminal: set[str] = set()
            resume_items = int(month.get('processed', 0))
            for direction, query_type in scopes:
                self._check_interrupted(job.job_id)

                def progress(
                    event, record, _counters,
                    _direction=direction,
                    _query_type=query_type,
                    _terminal=terminal,
                ):
                    nonlocal warning_count, resume_items
                    if event not in {'completed', 'failed'}:
                        return
                    key = ':'.join((
                        _direction, _query_type,
                        *(str(record.get(name, '')) for name in (
                            'nbmst', 'khhdon', 'shdon', 'khmshdon'
                        )),
                    ))
                    if key in _terminal:
                        return
                    _terminal.add(key)
                    warning_count += int(event == 'failed')
                    if resume_items:
                        resume_items -= 1
                    else:
                        self._advance_month(1)

                payload = {
                    'direction': direction, 'query_type': query_type,
                    'date_from': month['from_date'], 'date_to': month['to_date'],
                }
                try:
                    summary = self.core.run_mvt_scope(
                        job, payload, progress_callback=progress,
                        interruption_check=lambda: self._check_interrupted(job.job_id),
                    )
                except TypeError as error:
                    if 'progress_callback' not in str(error):
                        raise
                    summary = self.core.run_mvt_scope(job, payload)
                reported = (
                    int(summary.get('invoices_processed', 0))
                    + int(summary.get('already_completed', 0))
                    + int(summary.get('failed', 0))
                )
                emitted = sum(
                    key.startswith(f'{direction}:{query_type}:') for key in terminal
                )
                if reported > emitted:
                    unreported = reported - emitted
                    already_counted = min(resume_items, unreported)
                    resume_items -= already_counted
                    self._advance_month(unreported - already_counted)
                warning_count += max(0, int(summary.get('failed', 0)) - emitted)
            self._complete_month()
        self._complete_stage('mvt')
        return warning_count

    def _finalize_job(self, job, parameters, warning_count=0):
        self._start_stage('finalize')
        for operation, percent in (
            ('validate_modules', '25.0000'), ('validate_results', '55.0000'),
            ('persist_warnings', '80.0000'), ('commit_terminal_state', '99.9999'),
        ):
            if operation == 'validate_modules' and any(
                module['status'] != 'completed'
                for module in self._state['modules'].values()
            ):
                raise RuntimeError('pipeline module completeness validation failed')
            self._state['finalize_percent'] = percent
            self._state['message'] = f'finalize:{operation}'
            self._persist(force=True)
        self._state['finalize_percent'] = '100.0000'
        self._state['current_stage'] = None
        self._state['current_month'] = None
        self._state['message'] = (
            'completed_with_warning' if warning_count else 'completed'
        )
        result = self.repository.finish_pipeline_job(
            job.job_id, self._worker_id, self._lease_token, self._state,
            warning_count=warning_count,
        )
        self._complete_metric('finalize')
        return result

    @staticmethod
    def _overview_payload(parameters, direction, query_type, month):
        return {
            'session_hash': parameters['session_hash'], 'direction': direction,
            'query_type': query_type, 'date_from': month['from_date'],
            'date_to': month['to_date'], 'restart_coverage': True,
        }

    def _module_months(self, module):
        return self._state['modules'][module]['months']

    def _start_stage(self, stage):
        self.metrics.increment(f'pipeline_stage_started.{stage}')
        self._stage_started_at[stage] = time.perf_counter()
        self._state['current_stage'] = stage
        self._state['current_month'] = None
        if stage in self._state['modules']:
            self._state['modules'][stage]['status'] = 'running'
        self._state['message'] = f'running:{stage}'
        self._persist(force=True)

    def _complete_stage(self, stage):
        if stage in self._state['modules']:
            self._state['modules'][stage]['status'] = 'completed'
        self._state['current_month'] = None
        self._persist(force=True)
        self._complete_metric(stage)

    def _complete_metric(self, stage):
        self.metrics.increment(f'pipeline_stage_completed.{stage}')
        started = self._stage_started_at.pop(stage, None)
        if started is not None:
            self.metrics.observe_duration(
                f'pipeline_stage.{stage}', time.perf_counter() - started
            )

    def _start_month(self, module_name, month):
        month['status'] = 'running'
        month['processed'] = max(0, int(month.get('processed', 0)))
        month['percent'] = str(month_percent(
            month['processed'], month.get('planned')
        ))
        self._sync_month(module_name, month)
        self._persist(force=True)

    def _freeze_month(self, planned):
        month = self._current_month()
        if month.get('planned') is None:
            month['planned'] = max(0, int(planned))
        self._sync_month(self._state['current_stage'], month)
        self._persist(force=True)

    def _advance_month(self, amount, *, force=False):
        if amount <= 0:
            return
        month = self._current_month()
        planned = month.get('planned')
        if planned is None:
            month['planned'] = int(amount)
            planned = int(amount)
        month['processed'] = min(
            int(planned), max(int(month['processed']), int(month['processed']) + amount)
        )
        previous = Decimal(str(month.get('percent', 0)))
        month['percent'] = str(max(
            previous, month_percent(month['processed'], int(planned))
        ))
        self._sync_month(self._state['current_stage'], month)
        self._persist(item_delta=amount, force=force)

    def _complete_month(self):
        month = self._current_month()
        month['status'] = 'completed'
        month['processed'] = int(month.get('planned') or month.get('processed') or 0)
        month['percent'] = '100.0000'
        module = self._state['modules'][self._state['current_stage']]
        module['completed_months'] = max(
            int(module.get('completed_months', 0)), int(month['index'])
        )
        self._sync_month(self._state['current_stage'], month)
        self._persist(force=True)

    def _current_month(self):
        stage = self._state['current_stage']
        index = int(self._state['current_month']['index']) - 1
        return self._state['modules'][stage]['months'][index]

    def _sync_month(self, module_name, month):
        self._state['current_month'] = {
            'key': month['key'], 'index': month['index'],
            'total': self._state['modules'][module_name]['total_months'],
            'processed': month['processed'], 'planned': month.get('planned'),
            'percent': month['percent'],
        }

    def _persist(self, *, item_delta=0, force=False):
        if not self._throttle.should_persist(item_delta=item_delta, force=force):
            return
        self._state['last_progress_at'] = datetime.now(timezone.utc).isoformat()
        self.repository.persist_pipeline_progress(
            self._job_id, self._worker_id, self._lease_token, self._state
        )

    def _flush_if_available(self, *, force):
        if hasattr(self, '_state') and hasattr(self, '_job_id'):
            try:
                self._persist(force=force)
            except Exception:
                logger.exception('pipeline_progress_final_flush_failed job_id=%s', self._job_id)

    def _check_cancel(self, job_id):
        if self.repository.get_job(job_id).status == 'cancelling':
            raise UserCancellationRequested('job cancellation requested')

    def _check_interrupted(self, job_id):
        if self._lease_abort_requested.is_set():
            raise WorkerShutdownRequested('job lease no longer owned by this worker')
        if hasattr(self.core, 'is_shutdown_requested') and self.core.is_shutdown_requested():
            raise WorkerShutdownRequested('worker shutdown requested')
        self._check_cancel(job_id)

    def request_lease_abort(self):
        """Interrupt only the currently running job after its lease is lost."""
        self._lease_abort_requested.set()

    def request_shutdown(self):
        """Interrupt the process-hosted pipeline during SIGTERM/SIGINT."""
        self.core.request_shutdown()

    def _record_interrupted_stage(self, outcome):
        for stage, started in tuple(self._stage_started_at.items()):
            self.metrics.increment(f'pipeline_stage_{outcome}.{stage}')
            self.metrics.observe_duration(
                f'pipeline_stage.{stage}', time.perf_counter() - started
            )
            self._stage_started_at.pop(stage, None)
