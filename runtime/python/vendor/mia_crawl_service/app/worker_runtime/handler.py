from __future__ import annotations

import json
import logging
import threading
import time
import zipfile
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import requests

from app.config.crawl_config import CrawlConfig, query_type_to_category
from app.crawlers.diagnostics import job_diagnostics
from app.config.runtime import RuntimeCapabilities
from app.crawlers.invoice_crawler import (
    InvoiceCrawler,
    adaptive_paging_options_from_config,
)
from app.crawlers.invoice_detail_crawler import InvoiceDetailCrawler, build_detail_headers
from app.crawlers.invoice_package_crawler import (
    InvoicePackageCrawler,
    InvoicePackageUnavailableError,
    build_invoice_package_headers,
)
from app.job_engine.contracts import JobEngineRepository
from app.job_engine.interruptions import (
    PipelineInterruption,
    WorkerShutdownRequested,
)
from app.job_engine.models import (
    JobRecord,
    JobTaskSpec,
    StorageReconciliationReport,
    TaskRecord,
)
from app.repositories.invoice_detail_repository import InvoiceDetailRepository
from app.repositories.invoice_overview_repository import InvoiceOverviewRepository
from app.repositories.invoice_package_repository import InvoicePackageRepository
from app.services.invoice_detail_storage_service import InvoiceDetailStorageService
from app.services.invoice_overview_storage_service import InvoiceOverviewStorageService
from app.services.invoice_package_storage_service import InvoicePackageStorageService
from app.services.invoice_material_code_service import InvoiceMaterialCodeService
from app.services.overview_downloader import ELECTRONIC_STATUSES, OverviewDownloader
from app.utils.date_utils import split_by_calendar_month
from app.models.overview import OverviewDownloadRequest
from app.parsers.invoice_xml_parser import InvoiceXmlError, parse_invoice_xml
from app.session_manager.service import SessionTokenManager
from app.worker_runtime.errors import TaskExecutionError, classify_task_error
from app.worker_runtime.metrics import WorkerMetrics
from app.worker_runtime.proxy import EndpointCooldowns, ProxyLease, ProxyRegistry


logger = logging.getLogger('mia.worker_runtime')
_PORTAL_TASKS = frozenset({
    'fetch_invoice_overview', 'fetch_invoice_detail', 'fetch_invoice_package'
})


class InvoiceCrawlTaskHandler:
    """Execute Phase 05 portal tasks and generate bounded durable detail work."""

    concurrent_task_types = frozenset({'fetch_invoice_detail', 'fetch_invoice_package'})

    def __init__(
        self,
        job_repository: JobEngineRepository,
        session_manager: SessionTokenManager,
        *,
        worker_id: str,
        data_root: Path | str = Path('data'),
        crawl_config: CrawlConfig | None = None,
        proxy_registry: ProxyRegistry | None = None,
        endpoint_cooldowns: EndpointCooldowns | None = None,
        metrics: WorkerMetrics | None = None,
        detail_generation_batch_size: int = 500,
        runtime_proxies: Sequence[str] = (),
        allow_cross_route_token: bool = False,
        runtime_capabilities: RuntimeCapabilities | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError('worker_id is required')
        if detail_generation_batch_size < 1:
            raise ValueError('detail_generation_batch_size must be positive')
        self.job_repository = job_repository
        self.session_manager = session_manager
        self.worker_id = worker_id
        self.data_root = Path(data_root)
        self.crawl_config = crawl_config or CrawlConfig.from_env()
        self.crawl_config.validate()
        self.proxy_registry = proxy_registry or ProxyRegistry()
        self.endpoint_cooldowns = endpoint_cooldowns or EndpointCooldowns()
        self.metrics = metrics or WorkerMetrics()
        self.detail_generation_batch_size = detail_generation_batch_size
        self.runtime_proxies = tuple(proxy.strip() for proxy in runtime_proxies if proxy.strip())
        self.allow_cross_route_token = allow_cross_route_token
        self.runtime_capabilities = (
            runtime_capabilities or RuntimeCapabilities.from_environment()
        )
        self._shutdown_requested = threading.Event()

    def request_shutdown(self) -> None:
        self._shutdown_requested.set()

    def is_shutdown_requested(self) -> bool:
        return self._shutdown_requested.is_set()

    @job_diagnostics('auth')
    def authenticate_job(self, job: JobRecord, progress_callback=None) -> None:
        session_hash = str(job.parameters['session_hash'])
        self.session_manager.authenticate_session_hash(
            session_hash, worker_id=self.worker_id,
            progress_callback=progress_callback,
        )

    @job_diagnostics('overview')
    def run_overview_unit(
        self, job: JobRecord, payload: dict, page_committed,
        interruption_check=None, progress_callback=None, initial_payloads=None,
    ) -> dict[str, object] | None:
        return self._run_job_route(
            job, payload, 'overview',
            lambda lease: self._fetch_overview_core(
                job, payload, lease, page_committed=page_committed,
                interruption_check=interruption_check,
                progress_callback=progress_callback,
                initial_payloads=initial_payloads,
            ),
        )

    @job_diagnostics('overview_preflight')
    def prepare_overview_unit(self, job: JobRecord, payload: dict) -> dict:
        return self._run_job_route(
            job, payload, 'overview',
            lambda lease: self._prepare_overview_core(job, payload, lease),
        )

    @job_diagnostics('detail')
    def run_detail_unit(self, job: JobRecord, payload: dict, progress_callback=None) -> None:
        self._run_job_route(
            job, payload, 'detail',
            lambda lease: self._fetch_detail_core(
                job, payload, lease, progress_callback=progress_callback
            ),
        )

    @job_diagnostics('package')
    def run_xml_unit(self, job: JobRecord, payload: dict, progress_callback=None):
        return self._run_job_route(
            job, payload, 'package',
            lambda lease: self._fetch_package_core(
                job, payload, lease, progress_callback=progress_callback
            ),
        )

    @job_diagnostics('material')
    def run_mvt_scope(
        self, job: JobRecord, payload: dict, progress_callback=None,
        interruption_check=None,
    ) -> dict[str, Any]:
        return InvoiceMaterialCodeService(
            self._database_path(job.company_tax_code)
        ).extract_material_codes(
            company_tax_code=job.company_tax_code,
            direction=payload['direction'], query_type=payload['query_type'],
            from_date=payload['date_from'], to_date=payload['date_to'],
            only_pending=True,
            progress_callback=progress_callback,
            interruption_check=interruption_check,
        )

    def _run_job_route(self, job: JobRecord, payload: dict, endpoint: str, operation):
        if self._shutdown_requested.is_set():
            raise WorkerShutdownRequested('worker shutdown requested')
        session_hash = str(job.parameters['session_hash'])
        default_route = self.session_manager.get_worker_proxy_url(session_hash)
        routes: tuple[str | None, ...] = (default_route,)
        if self.allow_cross_route_token:
            routes += self.runtime_proxies
        self.proxy_registry.register(session_hash, routes)
        self.endpoint_cooldowns.wait(session_hash, endpoint)
        lease = self.proxy_registry.acquire(session_hash, f'{job.job_id}:{endpoint}')
        try:
            result = operation(lease)
        except PipelineInterruption:
            self.proxy_registry.release(session_hash, lease, outcome='neutral_failure')
            raise
        except Exception as error:
            classification = self._release_route_error(
                session_hash=session_hash, endpoint=endpoint, lease=lease,
                error=error, attempt_count=1, job_id=job.job_id,
            )
            raise classification from error
        else:
            self.proxy_registry.release(session_hash, lease, outcome='success')
            return result

    def __call__(self, task: TaskRecord) -> None:
        if self._shutdown_requested.is_set():
            raise WorkerShutdownRequested('worker shutdown requested')
        started = time.perf_counter()
        outcome = 'failed'
        self.metrics.increment(f'task_started.{task.task_type}')
        try:
            if task.task_type == 'authenticate_session':
                self._authenticate(task)
            elif task.task_type == 'fetch_invoice_overview':
                self._with_portal_route(task, 'overview', self._fetch_overview)
            elif task.task_type == 'fetch_invoice_detail':
                self._with_portal_route(task, 'detail', self._fetch_detail)
            elif task.task_type == 'fetch_invoice_package':
                self._with_portal_route(task, 'package', self._fetch_package)
            elif task.task_type == 'finalize_invoice_crawl':
                self._finalize(task)
            else:
                raise TaskExecutionError(classify_task_error(
                    RuntimeError('unsupported durable task type'), uses_proxy=False
                ))
        except TaskExecutionError:
            self.metrics.increment(f'task_failed.{task.task_type}')
            raise
        except Exception as error:
            self.metrics.increment(f'task_failed.{task.task_type}')
            raise TaskExecutionError(classify_task_error(
                error, uses_proxy=False, attempt_count=task.attempt_count
            )) from error
        else:
            outcome = 'completed'
            self.metrics.increment(f'task_completed.{task.task_type}')
        finally:
            duration = time.perf_counter() - started
            self.metrics.observe_duration(task.task_type, duration)
            logger.info(
                'worker_runtime event=task_metric job_id=%s task_id=%s task_type=%s '
                'outcome=%s duration_ms=%d attempt_count=%d',
                task.job_id, task.task_id, task.task_type, outcome,
                round(duration * 1000), task.attempt_count,
            )

    def reconcile_job(self, job: JobRecord) -> None:
        """Close detail generation only after the overview stage is durable."""
        if job.job_type != 'invoice_crawl' or job.status not in {'running', 'queued'}:
            return
        stages = {stage.stage_name: stage for stage in self.job_repository.get_stages(job.job_id)}
        overview = stages.get('overview')
        detail = stages.get('detail')
        if (
            overview is None
            or detail is None
            or overview.status not in {'completed', 'completed_with_warning'}
            or detail.task_generation_completed
        ):
            return
        parameters = self._job_parameters(job)
        database_path = self._database_path(job.company_tax_code)
        repository = InvoiceOverviewRepository(database_path)
        generated = 0
        detail_limit = parameters.get('detail_limit')
        if detail_limit is not None and (
            not isinstance(detail_limit, int) or isinstance(detail_limit, bool)
            or detail_limit < 1
        ):
            raise ValueError('detail_limit must be a positive integer')
        remaining = detail_limit
        for direction in parameters['directions']:
            if remaining == 0:
                break
            for query_type in parameters['query_types']:
                if remaining == 0:
                    break
                detail_refresh_from = date.fromisoformat(
                    parameters.get('detail_refresh_from', '9999-12-31')
                )
                for items in repository.iter_items_for_detail_task_generation(
                    job.company_tax_code,
                    direction,
                    query_type,
                    parameters['date_from'],
                    parameters['date_to'],
                    batch_size=self.detail_generation_batch_size,
                    include_completed=True,
                ):
                    candidates = [
                        item for item in items
                        if self._detail_needs_work(
                            job.company_tax_code,
                            item,
                            detail_refresh_from,
                        )
                    ]
                    selected = (
                        candidates if remaining is None else candidates[:remaining]
                    )
                    specs = [
                        self._detail_task_spec(
                            parameters['session_hash'],
                            item,
                            force_refresh=(
                                date.fromisoformat(item['nlap_date'])
                                >= detail_refresh_from
                            ),
                        )
                        for item in selected
                    ]
                    result = self.job_repository.add_tasks(
                        job.job_id, 'detail', specs, generation_completed=False
                    )
                    generated += result.inserted_count
                    if remaining is not None:
                        # Count deterministic selections rather than inserts. On a retry,
                        # duplicate task keys still consume their original position in the
                        # global limit, so the accepted set cannot grow past the limit.
                        remaining -= len(selected)
                        if remaining == 0:
                            break
        self.job_repository.add_tasks(
            job.job_id, 'detail', (), generation_completed=True
        )
        self.metrics.increment('detail_tasks_generated', generated)
        logger.info(
            'worker_runtime event=detail_generation_closed job_id=%s count=%d',
            job.job_id,
            generated,
        )

    def reconcile_startup(self, *, limit: int = 1000) -> StorageReconciliationReport:
        """Repair durable invoice/control state before a worker claims new work."""
        scanned_jobs = scanned_tasks = completed = requeued = repaired = errors = 0
        for job in self.job_repository.list_jobs_for_reconciliation(limit=limit):
            # A non-expired lease may belong to another live worker. Startup
            # recovery never steals it; normal fenced lease expiry handles it.
            if job.status == 'running' and job.lease_token:
                continue
            scanned_jobs += 1
            for task in self.job_repository.get_tasks(job.job_id):
                if task.task_type not in {
                    'fetch_invoice_overview',
                    'fetch_invoice_detail',
                    'fetch_invoice_package',
                }:
                    continue
                if task.status not in {'pending', 'retry_wait', 'completed'}:
                    continue
                scanned_tasks += 1
                try:
                    artifact_state, was_repaired = self._reconcile_task_artifact(job, task)
                    repaired += int(was_repaired)
                    if artifact_state == 'ready' and task.status in {'pending', 'retry_wait'}:
                        self.job_repository.complete_task_from_reconciliation(task.task_id)
                        completed += 1
                    elif artifact_state == 'missing' and task.status == 'completed':
                        self.job_repository.requeue_completed_task(
                            task.task_id,
                            error_code='artifact_missing_after_completion',
                            error_message='Durable invoice artifact is missing or invalid',
                        )
                        requeued += 1
                except Exception as error:
                    errors += 1
                    logger.error(
                        'worker_runtime event=storage_reconciliation_failed '
                        'job_id=%s task_id=%s task_type=%s error_type=%s',
                        job.job_id,
                        task.task_id,
                        task.task_type,
                        type(error).__name__,
                    )
        report = StorageReconciliationReport(
            scanned_jobs=scanned_jobs,
            scanned_tasks=scanned_tasks,
            completed_tasks=completed,
            requeued_tasks=requeued,
            repaired_artifacts=repaired,
            errors=errors,
        )
        logger.info(
            'worker_runtime event=startup_reconciliation_completed '
            'jobs=%d tasks=%d completed=%d requeued=%d repaired=%d errors=%d',
            scanned_jobs, scanned_tasks, completed, requeued, repaired, errors,
        )
        return report

    def _reconcile_task_artifact(
        self,
        job: JobRecord,
        task: TaskRecord,
    ) -> tuple[str, bool]:
        if task.task_type == 'fetch_invoice_overview':
            return self._reconcile_overview_artifact(job, task), False
        if task.task_type == 'fetch_invoice_detail':
            return self._reconcile_detail_artifact(job, task)
        if task.task_type == 'fetch_invoice_package':
            return self._reconcile_package_artifact(job, task)
        return 'unknown', False

    def _reconcile_overview_artifact(self, job: JobRecord, task: TaskRecord) -> str:
        payload = self._scope_payload(task, job)
        storage = InvoiceOverviewStorageService(self.data_root)
        statuses = ELECTRONIC_STATUSES if payload['query_type'] == 'query' else (None,)
        for begin, end in split_by_calendar_month(
            date.fromisoformat(payload['date_from']),
            date.fromisoformat(payload['date_to']),
        ):
            for status in statuses:
                if not storage.verify_finalized_overview_range(
                    company_tax_code=job.company_tax_code,
                    direction=payload['direction'],
                    query_type=payload['query_type'],
                    from_date=begin.isoformat(),
                    to_date=end.isoformat(),
                    status=status,
                ):
                    return 'missing'
        return 'ready'

    def _reconcile_detail_artifact(
        self,
        job: JobRecord,
        task: TaskRecord,
    ) -> tuple[str, bool]:
        payload = self._scope_payload(task, job, require_invoice_key=True)
        database = self._database_path(job.company_tax_code)
        repository = InvoiceDetailRepository(database)
        storage = InvoiceDetailStorageService(self.data_root, repository)
        existing = repository.get_detail_by_invoice_key(
            job.company_tax_code,
            payload['direction'], payload['query_type'], payload['nbmst'],
            payload['khhdon'], payload['shdon'], payload['khmshdon'],
        )
        if existing and not existing.get('error_message'):
            document = self._load_detail_document(
                existing.get('raw_detail_path'), job, payload
            )
            if document is not None:
                repository.mark_overview_detail_fetched(
                    job.company_tax_code,
                    payload['direction'], payload['query_type'], payload['nbmst'],
                    payload['khhdon'], payload['shdon'], payload['khmshdon'],
                    Path(str(existing['raw_detail_path'])).resolve(),
                    datetime.now(timezone.utc).isoformat(),
                )
                return 'ready', False

        expected_path = storage.raw_detail_path(
            company_tax_code=job.company_tax_code,
            direction=payload['direction'], query_type=payload['query_type'],
            from_date=payload['date_from'], to_date=payload['date_to'],
            nbmst=payload['nbmst'], khhdon=payload['khhdon'],
            shdon=payload['shdon'], khmshdon=payload['khmshdon'],
        )
        document = self._load_detail_document(expected_path, job, payload)
        if document is not None:
            storage.save_invoice_detail(
                company_tax_code=job.company_tax_code,
                direction=payload['direction'], query_type=payload['query_type'],
                invoice_category=query_type_to_category(payload['query_type']),
                overview_item=payload,
                detail=document['detail'],
                from_date=payload['date_from'], to_date=payload['date_to'],
                http_status=document.get('http_status', 200),
            )
            return 'ready', True

        if existing and not existing.get('error_message'):
            repository.mark_missing_detail_file(
                job.company_tax_code,
                payload['direction'], payload['query_type'], payload['nbmst'],
                payload['khhdon'], payload['shdon'], payload['khmshdon'],
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        return 'missing', False

    def _load_detail_document(
        self,
        raw_path: object,
        job: JobRecord,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not raw_path:
            return None
        path = Path(str(raw_path))
        if not self._is_safe_company_artifact(path, job.company_tax_code):
            return None
        try:
            # Keep JSON decimal lexemes exact until the row builder converts them
            # to Decimal; the default json decoder would introduce binary floats.
            document = json.loads(path.read_text(encoding='utf-8'), parse_float=str)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(document, dict) or not isinstance(document.get('detail'), dict):
            return None
        expected_key = {
            name: str(payload[name])
            for name in ('nbmst', 'khhdon', 'shdon', 'khmshdon')
        }
        if (
            document.get('company_tax_code') != job.company_tax_code
            or document.get('direction') != payload['direction']
            or document.get('query_type') != payload['query_type']
            or document.get('invoice_key') != expected_key
        ):
            return None
        return document

    def _reconcile_package_artifact(
        self,
        job: JobRecord,
        task: TaskRecord,
    ) -> tuple[str, bool]:
        payload = self._scope_payload(task, job, require_invoice_key=True)
        export_xml = bool(task.payload.get('export_xml', True))
        export_html = bool(task.payload.get('export_html', False))
        repository = InvoicePackageRepository(self._database_path(job.company_tax_code))
        storage = InvoicePackageStorageService(self.data_root, repository)
        existing = repository.get_package_by_invoice_key(
            job.company_tax_code,
            payload['direction'], payload['query_type'], payload['nbmst'],
            payload['khhdon'], payload['shdon'], payload['khmshdon'],
        )
        if existing and existing.get('unavailable'):
            return 'ready', False
        if existing and self._package_row_is_ready(
            existing, job.company_tax_code,
            export_xml=export_xml, export_html=export_html,
        ):
            return 'ready', False

        paths = storage.artifact_paths(
            company_tax_code=job.company_tax_code,
            direction=payload['direction'], query_type=payload['query_type'],
            from_date=payload['date_from'], to_date=payload['date_to'],
            invoice_item=payload,
        )
        candidates = []
        if existing and existing.get('raw_zip_path'):
            candidates.append(Path(str(existing['raw_zip_path'])))
        candidates.append(Path(paths['raw_zip_path']))
        raw_zip = next((
            path for path in candidates
            if self._is_safe_company_artifact(path, job.company_tax_code)
            and path.is_file() and zipfile.is_zipfile(path)
        ), None)
        if raw_zip is not None:
            storage.save_invoice_package(
                company_tax_code=job.company_tax_code,
                direction=payload['direction'], query_type=payload['query_type'],
                invoice_category=query_type_to_category(payload['query_type']),
                invoice_item=payload,
                zip_bytes=raw_zip.read_bytes(),
                from_date=payload['date_from'], to_date=payload['date_to'],
                export_xml=export_xml, export_html=export_html,
            )
            return 'ready', True

        if existing:
            repository.mark_missing_package_files(
                job.company_tax_code,
                payload['direction'], payload['query_type'], payload['nbmst'],
                payload['khhdon'], payload['shdon'], payload['khmshdon'],
                export_xml=export_xml, export_html=export_html,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        return 'missing', False

    def _package_row_is_ready(
        self,
        row: dict[str, Any],
        company_tax_code: str,
        *,
        export_xml: bool,
        export_html: bool,
    ) -> bool:
        raw = Path(str(row.get('raw_zip_path') or ''))
        if (
            not self._is_safe_company_artifact(raw, company_tax_code)
            or not raw.is_file()
            or not zipfile.is_zipfile(raw)
        ):
            return False
        for requested, flag, field in (
            (export_xml, 'xml_fetched', 'xml_path'),
            (export_html, 'html_fetched', 'html_path'),
        ):
            if not requested:
                continue
            path = Path(str(row.get(field) or ''))
            if (
                not row.get(flag)
                or not self._is_safe_company_artifact(path, company_tax_code)
                or not path.is_file()
            ):
                return False
        return True

    def _is_safe_company_artifact(self, path: Path, company_tax_code: str) -> bool:
        try:
            path.resolve().relative_to((self.data_root / company_tax_code).resolve())
        except (OSError, ValueError):
            return False
        return True

    def _authenticate(self, task: TaskRecord) -> None:
        session_hash = self._session_hash(task)
        try:
            self.session_manager.authenticate_session_hash(
                session_hash, worker_id=self.worker_id
            )
        except Exception as error:
            raise TaskExecutionError(
                classify_task_error(
                    error, uses_proxy=False, attempt_count=task.attempt_count
                ),
                message=str(error),
            ) from error

    def _with_portal_route(self, task: TaskRecord, endpoint: str, operation) -> None:
        session_hash = self._session_hash(task)
        default_route = self.session_manager.get_worker_proxy_url(session_hash)
        routes: tuple[str | None, ...] = (default_route,)
        if self.allow_cross_route_token:
            routes += self.runtime_proxies
        self.proxy_registry.register(session_hash, routes)
        self.endpoint_cooldowns.wait(session_hash, endpoint)
        lease = self.proxy_registry.acquire(session_hash, task.task_id)
        try:
            operation(task, lease)
        except Exception as error:
            classification = self._release_route_error(
                session_hash=session_hash, endpoint=endpoint, lease=lease,
                error=error, attempt_count=task.attempt_count,
                job_id=task.job_id, task_id=task.task_id,
            )
            raise classification from error
        else:
            self.proxy_registry.release(session_hash, lease, outcome='success')

    def _release_route_error(
        self, *, session_hash: str, endpoint: str, lease: ProxyLease,
        error: Exception, attempt_count: int, job_id: str,
        task_id: str | None = None,
    ) -> TaskExecutionError:
        classification = (
            error if isinstance(error, TaskExecutionError)
            else TaskExecutionError(classify_task_error(
                error, uses_proxy=lease.uses_proxy,
                attempt_count=attempt_count,
            ))
        )
        logger.warning(
            'worker_runtime event=route_error_classified job_id=%s task_id=%s '
            'endpoint=%s error_code=%s error_type=%s',
            job_id, task_id, endpoint, classification.code,
            type(error).__name__,
        )
        self.metrics.increment(f'route_failed.{endpoint}')
        if classification.applies_endpoint_cooldown:
            self.endpoint_cooldowns.defer(
                session_hash, endpoint, classification.retry_delay_seconds
            )
            self.metrics.increment(f'rate_limited.{endpoint}')
            outcome = 'rate_limited'
        elif classification.affects_proxy_health:
            self.metrics.increment('proxy_connection_failure')
            outcome = 'proxy_failure'
        else:
            outcome = 'neutral_failure'
        self.proxy_registry.release(session_hash, lease, outcome=outcome)
        return classification

    def _fetch_overview(self, task: TaskRecord, lease: ProxyLease) -> None:
        job = self.job_repository.get_job(task.job_id)
        payload = self._scope_payload(task, job)
        self._fetch_overview_core(job, payload, lease)

    def _fetch_overview_core(
        self, job: JobRecord, payload: dict, lease: ProxyLease, *,
        page_committed=None, interruption_check=None, progress_callback=None,
        initial_payloads=None,
    ) -> None:
        refresh_run_id = (
            f'{job.job_id}:{payload["direction"]}:{payload["query_type"]}:'
            f'{payload["date_from"]}:{payload["date_to"]}'
        )
        overview_repository = InvoiceOverviewRepository(
            self._database_path(job.company_tax_code)
        )
        if (
            payload.get('restart_coverage')
            and not (
                not self.runtime_capabilities.retain_raw_artifacts
                and overview_repository.has_overview_refresh_run(refresh_run_id)
            )
        ):
            overview_repository.invalidate_overview_range(
                company_tax_code=job.company_tax_code,
                direction=payload['direction'],
                query_type=payload['query_type'],
                from_date=payload['date_from'],
                to_date=payload['date_to'],
            )
        portal = self._build_portal(payload['session_hash'], lease, endpoint='overview')
        overview_settings = replace(
            self.crawl_config.overview,
            rate_limit_attempts=1,
            continue_on_rate_limited=False,
            continue_on_incomplete=False,
        )
        crawler = InvoiceCrawler(
            portal.client,
            request_get=portal.get,
            reauthenticate=lambda: self._refresh_managed_portal(portal),
            paging_options=adaptive_paging_options_from_config(
                overview_settings,
                authentication_attempts=2,
                profile=self.crawl_config.profile,
            ),
        )
        output_dir = self.data_root / job.company_tax_code / 'exports' / 'overview' / job.job_id
        downloader = OverviewDownloader(
            crawler=crawler,
            headers_provider=lambda: portal.headers,
            storage_service=InvoiceOverviewStorageService(
                self.data_root, progress_callback=progress_callback,
                capabilities=self.runtime_capabilities,
                refresh_run_id=(
                    refresh_run_id
                    if not self.runtime_capabilities.retain_raw_artifacts else None
                ),
            ),
            company_tax_code=job.company_tax_code,
            page_committed=page_committed,
            interruption_check=interruption_check,
            initial_payloads=initial_payloads,
        )
        request = OverviewDownloadRequest(
            begin_date=date.fromisoformat(payload['date_from']),
            end_date=date.fromisoformat(payload['date_to']),
            output_dir=output_dir,
            directions=(payload['direction'],),
            categories=(query_type_to_category(payload['query_type']),),
            overwrite=True,
        )
        if self.runtime_capabilities.enable_excel_export:
            downloader.download_request(request)
            return {'warning_count': 0, 'warnings': []}
        return downloader.download_normalized_request(request)

    def _prepare_overview_core(self, job, payload, lease):
        portal = self._build_portal(
            payload['session_hash'], lease, endpoint='overview'
        )
        overview_settings = replace(
            self.crawl_config.overview,
            rate_limit_attempts=1,
            continue_on_rate_limited=False,
            continue_on_incomplete=False,
        )
        crawler = InvoiceCrawler(
            portal.client, request_get=portal.get,
            reauthenticate=lambda: self._refresh_managed_portal(portal),
            paging_options=adaptive_paging_options_from_config(
                overview_settings, authentication_attempts=2,
                profile=self.crawl_config.profile,
            ),
        )
        category = query_type_to_category(payload['query_type'])
        statuses = ELECTRONIC_STATUSES if payload['query_type'] == 'query' else (None,)
        pages = {}
        total = 0
        begin_date = date.fromisoformat(payload['date_from'])
        end_date = date.fromisoformat(payload['date_to'])
        for status in statuses:
            if self._shutdown_requested.is_set():
                raise WorkerShutdownRequested('worker shutdown requested')
            response, reusable = self._fetch_overview_preflight_response(
                crawler=crawler,
                headers=portal.headers,
                direction=payload['direction'],
                category=category,
                begin_date=begin_date,
                end_date=end_date,
                status=status,
            )
            if not isinstance(response.get('datas', []), list):
                raise RuntimeError('Invalid invoice-list preflight response')
            key = 'all' if status is None else str(status)
            if reusable:
                pages[key] = response
            total += crawler._parse_total(
                response.get('total'), len(response.get('datas', []))
            )
        return {'planned_items': total, 'initial_payloads': pages}

    @staticmethod
    def _fetch_overview_preflight_response(
        *, crawler, headers, direction: str, category: str,
        begin_date: date, end_date: date, status: int | None,
    ) -> tuple[dict[str, Any], bool]:
        """Probe a range total without making one slow request fatal.

        The real adaptive crawler reduces page size after a timeout. Preflight
        used to issue exactly one size=50 request with retry_attempts=1, so a
        transient proxy/source timeout killed the job before adaptive paging
        could run. Mirror the adaptive size schedule here. Keep the probe
        bounded to at most three attempts at a given size; the full crawler
        still owns the longer retry budget and cursor persistence.

        Only a payload fetched with the first configured page size is reusable
        as ``initial_payload``. A smaller successful probe is used solely for
        its total so the adaptive crawler can fetch page 1 with its own audited
        page-size state.
        """
        options = crawler.paging_options
        last_error: Exception | None = None
        schedules = tuple(options.page_schedule)
        for index, (page_size, read_timeout) in enumerate(schedules):
            configured_attempts = options.attempts_for_size(
                page_size, is_smallest=index == len(schedules) - 1
            )
            attempt_limit = min(max(int(configured_attempts), 1), 3)
            for attempt in range(1, attempt_limit + 1):
                try:
                    response = crawler.fetch_page(
                        headers=headers,
                        direction=direction,
                        category=category,
                        begin_date=begin_date,
                        end_date=end_date,
                        status=status,
                        page_size=page_size,
                        timeout=(options.connect_timeout, read_timeout),
                        retry_attempts=1,
                    )
                    return response, index == 0
                except (RuntimeError, requests.RequestException) as error:
                    if not InvoiceCrawlTaskHandler._is_transient_preflight_error(error):
                        raise
                    last_error = error
                    logger.warning(
                        'overview preflight transient failure direction=%s category=%s '
                        'status=%s size=%d attempt=%d/%d error_type=%s',
                        direction, category, status, page_size, attempt,
                        attempt_limit, type(error).__name__,
                    )
                    if attempt < attempt_limit:
                        wait_seconds = min(
                            options.same_size_delay_step * attempt,
                            options.same_size_delay_cap,
                        )
                        if wait_seconds > 0:
                            time.sleep(wait_seconds)
            if index + 1 < len(schedules):
                logger.warning(
                    'overview preflight reducing page size direction=%s category=%s '
                    'status=%s from=%d to=%d',
                    direction, category, status, page_size,
                    schedules[index + 1][0],
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError('Overview preflight had no configured page schedule')

    @staticmethod
    def _is_transient_preflight_error(error: Exception) -> bool:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, (requests.Timeout, requests.ConnectionError)):
                return True
            current = current.__cause__ or current.__context__
        message = str(error)
        return (
            message.startswith('Tax API failed after ')
            and any(name in message for name in (
                'ReadTimeout', 'ConnectTimeout', 'ConnectionError',
            ))
        )

    def _fetch_detail(self, task: TaskRecord, lease: ProxyLease) -> None:
        job = self.job_repository.get_job(task.job_id)
        payload = self._scope_payload(task, job, require_invoice_key=True)
        self._fetch_detail_core(job, payload, lease)

    def _fetch_detail_core(
        self, job: JobRecord, payload: dict, lease: ProxyLease, *,
        progress_callback=None,
    ) -> None:
        database_path = self._database_path(job.company_tax_code)
        detail_repository = InvoiceDetailRepository(database_path)
        existing = detail_repository.get_detail_by_invoice_key(
            job.company_tax_code,
            payload['direction'],
            payload['query_type'],
            payload['nbmst'],
            payload['khhdon'],
            payload['shdon'],
            payload['khmshdon'],
        )
        if (
            not payload.get('force_refresh_detail')
            and existing
            and not existing.get('error_message')
        ):
            if (
                not self.runtime_capabilities.retain_raw_artifacts
                and existing.get('normalized_ready')
                and existing.get('detail_outcome') in {'with_lines', 'valid_empty'}
            ):
                self.metrics.increment('detail_reconciled_without_http')
                return
            raw_path = existing.get('raw_detail_path')
            if raw_path and Path(raw_path).is_file():
                try:
                    with Path(raw_path).open('r', encoding='utf-8') as stream:
                        persisted = json.load(stream, parse_float=str)
                    persisted_detail = persisted.get('detail', persisted)
                    InvoiceDetailStorageService(
                        self.data_root, detail_repository,
                        progress_callback=progress_callback,
                        capabilities=self.runtime_capabilities,
                    ).save_invoice_detail(
                        company_tax_code=job.company_tax_code,
                        direction=payload['direction'], query_type=payload['query_type'],
                        invoice_category=query_type_to_category(payload['query_type']),
                        overview_item=payload, detail=persisted_detail,
                        from_date=payload['date_from'], to_date=payload['date_to'],
                        http_status=existing.get('http_status') or 200,
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                    # Invalid legacy raw data is not a successful result; fall
                    # through to the portal fetch for this one invoice.
                    pass
                else:
                    self.metrics.increment('detail_reconciled_without_http')
                    return

        portal = self._build_portal(payload['session_hash'], lease, endpoint='detail')
        crawler = InvoiceDetailCrawler(
            portal.client,
            request_get=portal.get,
            request_timeout=(
                self.crawl_config.detail.connect_timeout_seconds,
                self.crawl_config.detail.read_timeout_seconds,
            ),
            retry_attempts=self.crawl_config.detail.retry_attempts,
            rate_limit_attempts=self.crawl_config.detail.rate_limit_attempts,
        )
        if progress_callback:
            progress_callback('request_started')
        detail = crawler.get_invoice_detail(
            headers=build_detail_headers(portal.headers, payload['direction']),
            query_type=payload['query_type'],
            nbmst=payload['nbmst'],
            khhdon=payload['khhdon'],
            shdon=payload['shdon'],
            khmshdon=payload['khmshdon'],
        )
        if progress_callback:
            progress_callback('response_received')
            progress_callback('response_validated')
        InvoiceDetailStorageService(
            self.data_root, detail_repository,
            progress_callback=progress_callback,
            capabilities=self.runtime_capabilities,
        ).save_invoice_detail(
            company_tax_code=job.company_tax_code,
            direction=payload['direction'],
            query_type=payload['query_type'],
            invoice_category=query_type_to_category(payload['query_type']),
            overview_item=payload,
            detail=detail,
            from_date=payload['date_from'],
            to_date=payload['date_to'],
        )

    def _fetch_package(self, task: TaskRecord, lease: ProxyLease) -> None:
        job = self.job_repository.get_job(task.job_id)
        payload = self._scope_payload(task, job, require_invoice_key=True)
        payload['export_xml'] = bool(task.payload.get('export_xml', True))
        payload['export_html'] = bool(task.payload.get('export_html', False))
        self._fetch_package_core(job, payload, lease)

    def _fetch_package_core(
        self, job: JobRecord, payload: dict, lease: ProxyLease, *,
        progress_callback=None,
    ):
        export_xml = bool(payload.get('export_xml', True))
        export_html = bool(payload.get('export_html', False))
        repository = InvoicePackageRepository(self._database_path(job.company_tax_code))
        existing = repository.get_package_by_invoice_key(
            job.company_tax_code, payload['direction'], payload['query_type'],
            payload['nbmst'], payload['khhdon'], payload['shdon'], payload['khmshdon'],
        )
        if progress_callback:
            progress_callback('package_row_loaded')
        if existing and existing.get('unavailable'):
            self.metrics.increment('package_reconciled_without_http')
            return {'outcome': 'unavailable'}
        if existing:
            if progress_callback:
                progress_callback('xml_path_checked')
            zip_ready = bool(
                existing.get('raw_zip_path') and Path(existing['raw_zip_path']).is_file()
            )
            xml_ready = not export_xml or bool(
                existing.get('xml_fetched')
                and existing.get('xml_path')
                and Path(existing['xml_path']).is_file()
            )
            if xml_ready and export_xml:
                try:
                    if progress_callback:
                        progress_callback('xml_file_readable')
                    parse_invoice_xml(Path(existing['xml_path']))
                    if progress_callback:
                        progress_callback('xml_parse_verified')
                except (OSError, InvoiceXmlError):
                    xml_ready = False
            html_ready = not export_html or bool(
                existing.get('html_fetched')
                and existing.get('html_path')
                and Path(existing['html_path']).is_file()
            )
            if zip_ready and xml_ready and html_ready:
                self.metrics.increment('package_reconciled_without_http')
                if progress_callback:
                    progress_callback('reused_existing_xml')
                return {'outcome': 'reused_verified'}
        portal = self._build_portal(payload['session_hash'], lease, endpoint='package')
        crawler = InvoicePackageCrawler(
            portal.client,
            request_get=portal.get,
            request_timeout=(
                self.crawl_config.package.connect_timeout_seconds,
                self.crawl_config.package.read_timeout_seconds,
            ),
            retry_attempts=2,
            rate_limit_attempts=1,
        )
        try:
            if progress_callback:
                progress_callback('request_started')
            content = crawler.download_invoice_package(
                headers=build_invoice_package_headers(
                    portal.headers,
                    payload['direction'],
                    'xml' if export_xml else 'html',
                ),
                query_type=payload['query_type'],
                nbmst=payload['nbmst'],
                khhdon=payload['khhdon'],
                shdon=payload['shdon'],
                khmshdon=payload['khmshdon'],
            )
            if progress_callback:
                progress_callback('response_received')
        except InvoicePackageUnavailableError as error:
            repository.upsert_package_error(
                company_tax_code=job.company_tax_code,
                direction=payload['direction'],
                query_type=payload['query_type'],
                invoice_category=query_type_to_category(payload['query_type']),
                nbmst=payload['nbmst'], khhdon=payload['khhdon'],
                shdon=payload['shdon'], khmshdon=payload['khmshdon'],
                nlap=payload.get('nlap'), nlap_date=payload.get('nlap_date'),
                error_message='Source package is unavailable', unavailable=True,
            )
            return {'outcome': 'unavailable'}
        saved = InvoicePackageStorageService(
            self.data_root, repository, progress_callback=progress_callback
        ).save_invoice_package(
            company_tax_code=job.company_tax_code,
            direction=payload['direction'],
            query_type=payload['query_type'],
            invoice_category=query_type_to_category(payload['query_type']),
            invoice_item=payload,
            zip_bytes=content,
            from_date=payload['date_from'],
            to_date=payload['date_to'],
            export_xml=export_xml,
            export_html=export_html,
        )
        if export_xml:
            xml_path = Path(str(saved.get('xml_path') or ''))
            if not xml_path.is_file():
                raise RuntimeError('Persisted XML verification failed')
            parse_invoice_xml(xml_path)
            if progress_callback:
                progress_callback('persisted_xml_verified')
        if progress_callback:
            progress_callback('completed')
        return {'outcome': 'downloaded'}

    def _finalize(self, task: TaskRecord) -> None:
        job = self.job_repository.get_job(task.job_id)
        parameters = self._job_parameters(job)
        # A limited acceptance job intentionally leaves the remaining overview
        # rows pending. The job engine has already required every generated
        # detail task to finish before the finalize stage can run.
        if parameters.get('detail_limit') is not None:
            return
        repository = InvoiceOverviewRepository(self._database_path(job.company_tax_code))
        missing = 0
        for direction in parameters['directions']:
            for query_type in parameters['query_types']:
                missing += len(repository.get_items_for_detail_download(
                    job.company_tax_code,
                    direction,
                    query_type,
                    parameters['date_from'],
                    parameters['date_to'],
                    only_pending=True,
                    limit=1,
                ))
        if missing:
            raise TaskExecutionError(classify_task_error(
                RuntimeError('detail persistence is incomplete'),
                uses_proxy=False,
                attempt_count=task.attempt_count,
            ))

    def _build_portal(self, session_hash: str, lease: ProxyLease, *, endpoint: str):
        kwargs: dict[str, Any] = {
            'max_retries': 2,
            'rate_limit_attempts': 1,
            'crawl_profile': self.crawl_config.profile,
            'interruption_check': self._raise_if_shutdown_requested,
        }
        if endpoint == 'detail':
            detail_settings = self.crawl_config.detail
            kwargs.update(
                backoff_seconds=detail_settings.retry_backoff_seconds,
                backoff_max_seconds=detail_settings.retry_backoff_max_seconds,
            )
        portal = self.session_manager.build_worker_portal_session(
            session_hash,
            worker_id=self.worker_id,
            proxy=lease.proxy_url,
            **kwargs,
        )
        # Each durable task creates a fresh HTTP session. Load the encrypted
        # managed token before a crawler asks for ``portal.headers``; source
        # reauthentication is still singleflight through the token provider.
        portal.login()
        return portal

    def _raise_if_shutdown_requested(self) -> None:
        if self._shutdown_requested.is_set():
            raise WorkerShutdownRequested('worker shutdown requested')

    @staticmethod
    def _refresh_managed_portal(portal) -> str:
        if portal.token_provider is None:
            return portal.login()
        snapshot = portal.token_provider.refresh_after_unauthorized(
            portal.token_generation
        )
        portal.token = snapshot.token
        portal.token_generation = snapshot.generation
        return snapshot.token

    def _scope_payload(
        self,
        task: TaskRecord,
        job: JobRecord,
        *,
        require_invoice_key: bool = False,
    ) -> dict[str, Any]:
        parameters = self._job_parameters(job)
        payload = dict(task.payload)
        if payload.get('session_hash') != parameters['session_hash']:
            raise ValueError('task session scope does not match its job')
        for field in ('direction', 'query_type', 'date_from', 'date_to'):
            if field not in payload:
                payload[field] = parameters[field + 's'][0] if field in {'direction', 'query_type'} else parameters[field]
        if payload['direction'] not in parameters['directions']:
            raise ValueError('task direction is outside job scope')
        if payload['query_type'] not in parameters['query_types']:
            raise ValueError('task query type is outside job scope')
        task_from = date.fromisoformat(payload['date_from'])
        task_to = date.fromisoformat(payload['date_to'])
        request_from = date.fromisoformat(parameters['date_from'])
        request_to = date.fromisoformat(parameters['date_to'])
        if not (request_from <= task_from <= task_to <= request_to):
            raise ValueError('task date range is outside job scope')
        if require_invoice_key:
            for field in ('nbmst', 'khhdon', 'shdon', 'khmshdon'):
                if not str(payload.get(field, '')).strip():
                    raise ValueError(f'task invoice key is missing {field}')
        return payload

    @staticmethod
    def _job_parameters(job: JobRecord) -> dict[str, Any]:
        parameters = dict(job.parameters)
        required = ('session_hash', 'date_from', 'date_to', 'directions', 'query_types')
        if any(name not in parameters for name in required):
            raise ValueError('invoice crawl job parameters are incomplete')
        return parameters

    @staticmethod
    def _session_hash(task: TaskRecord) -> str:
        value = task.payload.get('session_hash')
        if not isinstance(value, str):
            raise ValueError('task session_hash is required')
        return value

    def _database_path(self, company_tax_code: str) -> Path:
        return self.data_root / company_tax_code / 'db' / 'invoices.sqlite3'

    @staticmethod
    def _detail_task_spec(
        session_hash: str,
        item: dict[str, Any],
        *,
        force_refresh: bool = False,
    ) -> JobTaskSpec:
        key = ':'.join(str(item[name]) for name in (
            'direction', 'query_type', 'nbmst', 'khhdon', 'shdon', 'khmshdon'
        ))
        payload = {
            'session_hash': session_hash,
            'force_refresh_detail': force_refresh,
            **{name: item.get(name) for name in (
                'direction', 'query_type', 'invoice_category', 'nbmst', 'khhdon',
                'shdon', 'khmshdon', 'nlap', 'nlap_date',
            )},
        }
        return JobTaskSpec(
            task_key=f'detail:{key}',
            stage_name='detail',
            task_type='fetch_invoice_detail',
            payload=payload,
            max_attempts=10,
            failure_policy='fail_job',
        )

    def _detail_needs_work(
        self,
        company_tax_code: str,
        item: dict[str, Any],
        detail_refresh_from: date,
    ) -> bool:
        if date.fromisoformat(item['nlap_date']) >= detail_refresh_from:
            return True
        detail = InvoiceDetailRepository(
            self._database_path(company_tax_code)
        ).get_detail_by_invoice_key(
            company_tax_code,
            item['direction'], item['query_type'], item['nbmst'], item['khhdon'],
            item['shdon'], item['khmshdon'],
        )
        if not detail or detail.get('error_message'):
            return True
        path_value = detail.get('raw_detail_path')
        if not path_value:
            return True
        try:
            with Path(path_value).open('r', encoding='utf-8') as stream:
                document = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        return not isinstance(document, (dict, list))
