from __future__ import annotations

import tempfile
import threading
import time
import unittest
import sys
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor' / 'mia_crawl_service'))

from app.job_engine.models import CreateJobRequest, JobStageSpec, LeaseLostError
from app.job_engine.interruptions import WorkerShutdownRequested
from app.job_engine.repository import SQLiteJobEngineRepository
from app.job_engine.service import SequentialWorkerSupervisor
from app.worker_runtime.handler import InvoiceCrawlTaskHandler
from app.worker_runtime.coverage_planner import (
    CoverageDecision, CoveragePlan, DetailDecision,
)
from app.job_engine.progress import ProgressSnapshot, build_pipeline_plan
from app.job_engine.progress import initial_progress_state
from app.worker_runtime.pipeline import InvoiceCrawlPipeline


class FakePlanner:
    def __init__(self, decisions, details=()):
        self.decisions = tuple(decisions)
        self.details = tuple(details)
        self.storage = SimpleNamespace(
            verify_finalized_overview_range=lambda **_kwargs: True
        )

    def plan(self, **_kwargs):
        return CoveragePlan(date(2026, 7, 2), self.decisions)

    def plan_details(self, **_kwargs):
        return self.details

    def detail_work(self, **_kwargs):
        return tuple(item for item in self.details if item.action != 'skip_verified')


class FakeCore:
    def __init__(self):
        self.events = []
        self._shutdown_requested = False

    def authenticate_job(self, _job, progress_callback=None):
        self.events.append('auth')
        if progress_callback:
            for event in (
                'credentials_ready', 'captcha_fetched', 'captcha_solved',
                'login_token_received', 'token_persisted',
            ):
                progress_callback(event)

    def run_overview_unit(
        self, _job, payload, committed, interruption_check=None
    ):
        if interruption_check:
            interruption_check()
        self.events.append('overview')
        page = SimpleNamespace(
            total=10, page_size=10, fetched_count=10, page_number=1
        )
        committed(page, {}, {
            'direction': payload['direction'],
            'query_type': payload['query_type'], 'status_filter': 'all',
            'from_date': payload['date_from'], 'to_date': payload['date_to'],
        })

    def run_detail_unit(self, _job, _payload):
        self.events.append('detail')

    def request_shutdown(self):
        self.events.append('shutdown')
        self._shutdown_requested = True

    def is_shutdown_requested(self):
        return self._shutdown_requested


class TasklessPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repository = SQLiteJobEngineRepository(
            Path(self.temporary.name) / 'control.sqlite3'
        )
        self.repository.migrate()

    def tearDown(self):
        self.temporary.cleanup()

    def _job(self):
        return self.repository.create_job(
            CreateJobRequest(
                'account', '0312345678', 'invoice_crawl',
                parameters={
                    'session_hash': 'a' * 64, 'date_from': '2026-05-01',
                    'date_to': '2026-05-31', 'directions': ['purchase'],
                    'query_types': ['sco-query'], 'force_refresh': False,
                    'detail_limit': None,
                }, pipeline_version=2,
            ),
            (),
            stages=[JobStageSpec(name) for name in (
                'auth', 'overview', 'detail', 'finalize'
            )],
        )

    def _dynamic_job(self, scope, include_mvt):
        plan = build_pipeline_plan(
            scope, include_mvt,
            date_from=date(2026, 5, 1), date_to=date(2026, 5, 31),
        )
        return self.repository.create_job(
            CreateJobRequest(
                f'account-{scope}-{include_mvt}', '0312345678', 'invoice_crawl',
                parameters={
                    'session_hash': 'a' * 64, 'date_from': '2026-05-01',
                    'date_to': '2026-05-31', 'directions': ['purchase'],
                    'query_types': ['sco-query'], 'force_refresh': False,
                    'detail_limit': None, 'result_scope': scope,
                    'include_mvt': include_mvt, 'pipeline_plan': plan,
                }, pipeline_version=2,
            ), (), stages=[JobStageSpec(item['name']) for item in plan['stages']],
        )

    def test_pipeline_is_sequential_monotonic_and_creates_no_task_rows(self):
        for method_name in (
            'add_tasks', 'get_tasks', 'claim_task_batch', 'renew_task_lease',
            'complete_task', 'fail_task', 'recover_task_lease',
        ):
            if hasattr(self.repository, method_name):
                setattr(
                    self.repository, method_name,
                    lambda *_args, _name=method_name, **_kwargs: self.fail(
                        f'pipeline v2 called legacy task method {_name}'
                    ),
                )
        self._job()
        decision = CoverageDecision(
            'purchase', 'sco-query', 'all',
            date(2026, 5, 1), date(2026, 5, 31), 'missing',
        )
        core = FakeCore()
        pipeline = InvoiceCrawlPipeline(
            self.repository, core, FakePlanner([decision]),
            clock=lambda: datetime(2026, 8, 1, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')),
        )
        snapshots = []
        original_persist = self.repository.persist_pipeline_progress

        def record_progress(*args, **kwargs):
            snapshots.append(deepcopy(args[3]))
            return original_persist(*args, **kwargs)

        self.repository.persist_pipeline_progress = record_progress
        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker', pipeline=pipeline,
            heartbeat_interval_seconds=1,
        ).run_once()
        self.assertEqual(result.job.status, 'completed')
        self.assertEqual(result.job.warning_count, 0)
        self.assertEqual(core.events, ['auth', 'overview'])
        with self.repository._connect() as connection:
            task_count = connection.execute(
                'SELECT COUNT(*) FROM crawl_job_tasks WHERE job_id = ?',
                (result.job.job_id,),
            ).fetchone()[0]
        self.assertEqual(task_count, 0)
        self.assertEqual(result.job.progress_percent, 100)
        self.assertEqual(result.job.stage_progress_percent, 100)
        self.assertIsNotNone(result.job.finished_at)
        self.assertEqual(
            result.job.progress_state['modules']['detail']['completed_months'], 1
        )
        self.assertNotIn('events', result.job.progress_state)
        self.assertNotIn('items', result.job.progress_state)
        for snapshot in snapshots:
            if snapshot.get('current_stage'):
                self.assertLessEqual(
                    float(ProgressSnapshot.from_state(snapshot).overall_percent), 100
                )
        metrics = pipeline.metrics.snapshot()
        self.assertEqual(metrics.counters['pipeline_started'], 1)
        self.assertEqual(metrics.counters['pipeline_completed'], 1)
        for stage in ('auth', 'overview', 'detail', 'finalize'):
            self.assertEqual(
                metrics.counters[f'pipeline_stage_completed.{stage}'], 1
            )
            self.assertIn(f'pipeline_stage.{stage}', metrics.duration_ms)

    def test_compatibility_has_four_stages_not_internal_units(self):
        job = self._job()
        self.assertEqual(len(self.repository.get_stages(job.job_id)), 4)
        self.assertEqual(self.repository.get_tasks(job.job_id), [])

    def test_lease_loss_heartbeat_signals_long_pipeline_to_stop(self):
        self._job()
        original_renew = self.repository.renew_job_lease
        self.repository.renew_job_lease = lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(LeaseLostError('fenced'))
        )
        pipeline = _BlockingPipeline()
        started = time.monotonic()
        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker', pipeline=pipeline,
            heartbeat_interval_seconds=0.01,
            heartbeat_retry_delay_seconds=0.01,
            shutdown_grace_seconds=1,
        ).run_once()
        self.repository.renew_job_lease = original_renew
        self.assertTrue(result.lease_lost)
        self.assertTrue(pipeline.shutdown_requested.wait(0.1))
        self.assertTrue(pipeline.exited.wait(0.1))
        self.assertLess(time.monotonic() - started, 1)

    def test_transient_heartbeat_failure_retries_then_fails_job(self):
        self._job()
        attempts = 0

        def fail_renew(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise RuntimeError('database unavailable')

        self.repository.renew_job_lease = fail_renew
        pipeline = _BlockingPipeline()
        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker', pipeline=pipeline,
            heartbeat_interval_seconds=0.01,
            heartbeat_retry_attempts=3,
            heartbeat_retry_delay_seconds=0.01,
            shutdown_grace_seconds=1,
        ).run_once()
        self.assertEqual(attempts, 3)
        self.assertTrue(result.lease_lost)
        self.assertEqual(result.job.status, 'failed')
        self.assertEqual(
            result.job.last_error_code, 'job_lease_heartbeat_failed'
        )
        self.assertTrue(pipeline.exited.wait(0.1))

    def test_shutdown_between_overview_pages_prevents_later_side_effects(self):
        self._job()
        decision = CoverageDecision(
            'purchase', 'sco-query', 'all',
            date(2026, 5, 1), date(2026, 5, 31), 'missing',
        )
        core = _MultiPageShutdownCore()
        pipeline = InvoiceCrawlPipeline(
            self.repository, core, FakePlanner([decision]),
            clock=lambda: datetime(
                2026, 8, 1, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')
            ),
        )
        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker', pipeline=pipeline,
            heartbeat_interval_seconds=1,
        ).run_once()
        self.assertEqual(result.job.status, 'queued')
        self.assertIsNone(result.job.last_error_code)
        self.assertEqual(core.request_count, 1)
        self.assertEqual(core.committed_pages, [1])
        self.assertLess(result.job.progress_percent, 100)
        self.assertEqual(
            result.job.progress_state['current_month']['processed'], 10
        )
        time.sleep(0.05)
        self.assertEqual(core.committed_pages, [1])

        resumed_core = _MultiPageCore()
        resumed = SequentialWorkerSupervisor(
            self.repository, worker_id='replacement-worker',
            pipeline=InvoiceCrawlPipeline(
                self.repository, resumed_core, FakePlanner([decision]),
                clock=lambda: datetime(
                    2026, 8, 1, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')
                ),
            ), heartbeat_interval_seconds=1,
        ).run_once()
        self.assertEqual(resumed.job.job_id, result.job.job_id)
        self.assertEqual(resumed.job.status, 'completed')
        self.assertEqual(resumed.job.progress_percent, 100)
        self.assertEqual(resumed_core.committed_pages, [1, 2])

    def test_handler_shutdown_before_route_uses_worker_interruption(self):
        handler = InvoiceCrawlTaskHandler.__new__(InvoiceCrawlTaskHandler)
        handler._shutdown_requested = threading.Event()
        handler.request_shutdown()
        with self.assertRaises(WorkerShutdownRequested):
            handler._run_job_route(None, {}, 'overview', lambda _lease: None)
        with self.assertRaises(WorkerShutdownRequested):
            handler(object())

    def test_worker_shutdown_before_overview_requeues_same_job(self):
        self._job()
        decision = CoverageDecision(
            'purchase', 'sco-query', 'all',
            date(2026, 5, 1), date(2026, 5, 31), 'missing',
        )
        core = _ShutdownBeforeOverviewCore()
        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker',
            pipeline=InvoiceCrawlPipeline(
                self.repository, core, FakePlanner([decision]),
                clock=lambda: datetime(
                    2026, 8, 1, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')
                ),
            ), heartbeat_interval_seconds=1,
        ).run_once()
        self.assertEqual(result.job.status, 'queued')
        self.assertIsNone(result.job.last_error_code)
        self.assertIsNone(result.job.finished_at)
        claimed = self.repository.claim_next_job(
            'replacement-worker', lease_seconds=60
        )
        self.assertEqual(claimed.job_id, result.job.job_id)
        self.assertGreater(claimed.lease_generation, 1)

    def test_api_cancellation_is_not_worker_restart(self):
        job = self._job()
        decision = CoverageDecision(
            'purchase', 'sco-query', 'all',
            date(2026, 5, 1), date(2026, 5, 31), 'missing',
        )
        core = _ApiCancellationCore(self.repository, job.job_id)
        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker',
            pipeline=InvoiceCrawlPipeline(
                self.repository, core, FakePlanner([decision]),
                clock=lambda: datetime(
                    2026, 8, 1, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')
                ),
            ), heartbeat_interval_seconds=1,
        ).run_once()
        self.assertEqual(result.job.status, 'cancelled')
        self.assertNotEqual(result.job.last_error_code, 'worker_restarted')

    def test_business_exception_remains_failed_and_redacts_secrets(self):
        self._job()
        pipeline = _FailingPipeline(
            'credential:hunter2 token:abc proxy:' +
            'ht' + 'tp://user:pass@example.test'
        )
        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker', pipeline=pipeline,
            heartbeat_interval_seconds=1,
        ).run_once()
        self.assertEqual(result.job.status, 'failed')
        self.assertEqual(result.job.last_error_code, 'RuntimeError')
        for secret in ('hunter2', 'abc', 'user', 'pass'):
            self.assertNotIn(secret, result.job.last_error_message)

    def test_overview_only_pipeline_skips_detail_xml_and_mvt(self):
        self._dynamic_job('overview', False)
        core = FakeCore()
        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker',
            pipeline=InvoiceCrawlPipeline(
                self.repository, core, FakePlanner([]),
                clock=lambda: datetime(
                    2026, 8, 1, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')
                ),
            ), heartbeat_interval_seconds=1,
        ).run_once()
        self.assertEqual(result.job.status, 'completed')
        self.assertEqual(core.events, [])
        self.assertEqual([
            item['name'] for item in result.job.progress_state['pipeline_plan']['stages']
        ], ['auth', 'overview', 'finalize'])

    def test_duplicate_source_items_still_count_toward_month_progress(self):
        self._dynamic_job('overview', False)
        decision = CoverageDecision(
            'purchase', 'sco-query', 'all',
            date(2026, 5, 1), date(2026, 5, 31), 'missing',
        )

        class DuplicateCore(FakeCore):
            def run_overview_unit(
                self, _job, payload, committed, interruption_check=None,
                initial_payloads=None,
            ):
                self.events.append('overview')
                duplicate = {'id': 'same-source-item'}
                page = SimpleNamespace(
                    total=2, page_size=50, fetched_count=2, page_number=1,
                    records=(duplicate, duplicate),
                )
                committed(page, {'upserted_count': 1}, {
                    'direction': payload['direction'],
                    'query_type': payload['query_type'], 'status_filter': 'all',
                })

        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker',
            pipeline=InvoiceCrawlPipeline(
                self.repository, DuplicateCore(), FakePlanner([decision]),
                clock=lambda: datetime(
                    2026, 8, 1, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')
                ),
            ), heartbeat_interval_seconds=1,
        ).run_once()
        month = result.job.progress_state['modules']['overview']['months'][0]
        self.assertEqual((month['processed'], month['planned']), (2, 2))
        self.assertEqual(month['percent'], '100.0000')

    def test_resume_skips_completed_month_and_keeps_frozen_denominator(self):
        plan = build_pipeline_plan(
            'overview', False,
            date_from=date(2026, 5, 1), date_to=date(2026, 6, 30),
        )
        job = self.repository.create_job(
            CreateJobRequest(
                'resume-account', '0312345678', 'invoice_crawl',
                parameters={
                    'session_hash': 'a' * 64, 'date_from': '2026-05-01',
                    'date_to': '2026-06-30', 'directions': ['purchase'],
                    'query_types': ['sco-query'], 'force_refresh': False,
                    'detail_limit': None, 'result_scope': 'overview',
                    'include_xml': False, 'include_mvt': False,
                    'pipeline_plan': plan,
                }, pipeline_version=2,
            ), (), stages=[JobStageSpec(item['name']) for item in plan['stages']],
        )
        claimed = self.repository.claim_next_job('worker', lease_seconds=60)
        state = initial_progress_state(plan)
        state['auth_percent'] = '100.0000'
        state['current_stage'] = 'overview'
        overview = state['modules']['overview']
        overview['status'] = 'running'
        overview['completed_months'] = 1
        overview['months'][0].update({
            'status': 'completed', 'planned': 123, 'processed': 123,
            'percent': '100.0000',
        })
        self.repository.persist_pipeline_progress(
            job.job_id, 'worker', claimed.lease_token, state
        )
        decisions = [
            CoverageDecision(
                'purchase', 'sco-query', 'all', begin, end, 'missing'
            )
            for begin, end in (
                (date(2026, 5, 1), date(2026, 5, 31)),
                (date(2026, 6, 1), date(2026, 6, 30)),
            )
        ]
        core = FakeCore()
        result = InvoiceCrawlPipeline(
            self.repository, core, FakePlanner(decisions),
            clock=lambda: datetime(
                2026, 8, 1, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')
            ),
        ).run(self.repository.get_job(job.job_id), 'worker', claimed.lease_token)
        self.assertEqual(core.events.count('overview'), 1)
        first = result.progress_state['modules']['overview']['months'][0]
        self.assertEqual((first['planned'], first['processed']), (123, 123))

    def test_detail_with_mvt_runs_dynamic_stages_sequentially(self):
        self._dynamic_job('detail', True)
        decision = CoverageDecision(
            'purchase', 'sco-query', 'all',
            date(2026, 5, 1), date(2026, 5, 31), 'missing',
        )
        item = {
            'direction': 'purchase', 'query_type': 'sco-query',
            'nbmst': '0312345678', 'khhdon': 'C26', 'shdon': '1',
            'khmshdon': '1', 'nlap': '2026-05-01',
            'nlap_date': '2026-05-01',
        }
        core = _MvtCore()
        result = SequentialWorkerSupervisor(
            self.repository, worker_id='worker',
            pipeline=InvoiceCrawlPipeline(
                self.repository, core,
                FakePlanner([decision], [DetailDecision(item, False, 'fetch')]),
                clock=lambda: datetime(
                    2026, 8, 1, tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')
                ),
            ), heartbeat_interval_seconds=1,
        ).run_once()
        self.assertEqual(result.job.status, 'completed')
        self.assertEqual(core.events, ['auth', 'overview', 'detail', 'xml', 'mvt'])
        self.assertEqual([
            item['name'] for item in result.job.progress_state['pipeline_plan']['stages']
        ], [
            'auth', 'overview', 'detail', 'ensure_xml', 'mvt', 'finalize'
        ])


class _BlockingPipeline:
    def __init__(self):
        self.shutdown_requested = threading.Event()
        self.exited = threading.Event()

    def run(self, _job, _worker_id, _token):
        try:
            self.shutdown_requested.wait(5)
            from app.worker_runtime.pipeline import PipelineCancelled
            raise PipelineCancelled('stopped after heartbeat failure')
        finally:
            self.exited.set()

    def request_shutdown(self):
        self.shutdown_requested.set()


class _MultiPageShutdownCore(FakeCore):
    def __init__(self):
        super().__init__()
        self.request_count = 0
        self.committed_pages = []

    def run_overview_unit(
        self, _job, payload, committed, interruption_check=None
    ):
        for page_number in (1, 2):
            if interruption_check:
                interruption_check()
            self.request_count += 1
            page = SimpleNamespace(
                total=20, page_size=10,
                fetched_count=page_number * 10, page_number=page_number,
            )
            self.committed_pages.append(page_number)
            committed(page, {}, {
                'direction': payload['direction'],
                'query_type': payload['query_type'], 'status_filter': 'all',
                'from_date': payload['date_from'], 'to_date': payload['date_to'],
            })
            if page_number == 1:
                self.request_shutdown()


class _MultiPageCore(FakeCore):
    def __init__(self):
        super().__init__()
        self.committed_pages = []

    def run_overview_unit(
        self, _job, payload, committed, interruption_check=None
    ):
        for page_number in (1, 2):
            if interruption_check:
                interruption_check()
            page = SimpleNamespace(
                total=20, page_size=10,
                fetched_count=page_number * 10, page_number=page_number,
            )
            self.committed_pages.append(page_number)
            committed(page, {}, {
                'direction': payload['direction'],
                'query_type': payload['query_type'], 'status_filter': 'all',
                'from_date': payload['date_from'], 'to_date': payload['date_to'],
            })


class _ShutdownBeforeOverviewCore(FakeCore):
    def run_overview_unit(self, *_args, **_kwargs):
        self.request_shutdown()
        raise WorkerShutdownRequested('worker shutdown requested')


class _ApiCancellationCore(FakeCore):
    def __init__(self, repository, job_id):
        super().__init__()
        self.repository = repository
        self.job_id = job_id

    def run_overview_unit(
        self, _job, _payload, _committed, interruption_check=None
    ):
        self.repository.request_cancellation(self.job_id)
        interruption_check()


class _FailingPipeline:
    def __init__(self, message):
        self.message = message

    def run(self, *_args):
        raise RuntimeError(self.message)

    def request_shutdown(self):
        pass


class _MvtCore(FakeCore):
    def run_xml_unit(self, _job, _payload):
        self.events.append('xml')

    def run_mvt_scope(self, _job, _payload):
        self.events.append('mvt')
        return {
            'matched': 1, 'unmatched': 0, 'ambiguous': 0,
            'without_mhhdvu': 0,
        }


if __name__ == '__main__':
    unittest.main()
