from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

from app.config.crawl_config import parse_proxy_list
from app.job_engine.factory import create_job_engine_repository
from app.job_engine.service import SequentialWorkerSupervisor


logger = logging.getLogger('mia.job_engine')


class WorkerLoop:
    """One logical worker-slot loop hosted outside FastAPI/Uvicorn."""

    def __init__(
        self,
        supervisor: SequentialWorkerSupervisor,
        *,
        idle_backoff_seconds: float = 2.0,
        error_backoff_seconds: float = 5.0,
        orphan_scan_seconds: float = 30.0,
    ) -> None:
        if min(idle_backoff_seconds, error_backoff_seconds, orphan_scan_seconds) <= 0:
            raise ValueError('worker backoff values must be positive')
        self.supervisor = supervisor
        self.idle_backoff_seconds = idle_backoff_seconds
        self.error_backoff_seconds = error_backoff_seconds
        self.orphan_scan_seconds = orphan_scan_seconds

    def run(self, stop_event: threading.Event, *, max_iterations=None) -> int:
        self.supervisor.set_stop_event(stop_event)
        logger.info(
            'job_engine event=worker_slot_started worker_slot_id=%s',
            self.supervisor.worker_id,
        )
        iterations = 0
        next_orphan_scan = time.monotonic()
        while not stop_event.is_set():
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1
            try:
                if time.monotonic() >= next_orphan_scan:
                    self.supervisor.repository.recover_expired_leases()
                    next_orphan_scan = time.monotonic() + self.orphan_scan_seconds
                result = self.supervisor.run_once()
            except Exception as exc:
                logger.exception(
                    'job_engine event=worker_iteration_failed worker_slot_id=%s '
                    'error_code=%s',
                    self.supervisor.worker_id,
                    exc.__class__.__name__,
                )
                if stop_event.wait(self.error_backoff_seconds):
                    break
                continue
            if result.job is None and stop_event.wait(self.idle_backoff_seconds):
                break
        logger.info(
            'job_engine event=worker_slot_stopped worker_slot_id=%s iterations=%s',
            self.supervisor.worker_id,
            iterations,
        )
        return iterations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='MIA immediate-admission logical worker host'
    )
    parser.add_argument('--database', help='SQLite path when no database URL is set')
    parser.add_argument('--worker-id', default='mia-worker')
    parser.add_argument(
        '--job-lease-seconds',
        type=int,
        default=int(os.getenv('MIA_JOB_LEASE_SECONDS', '120')),
    )
    parser.add_argument(
        '--heartbeat-seconds',
        type=float,
        default=float(os.getenv('MIA_JOB_HEARTBEAT_SECONDS', '10')),
    )
    parser.add_argument('--idle-backoff-seconds', type=float, default=2.0)
    parser.add_argument('--error-backoff-seconds', type=float, default=5.0)
    parser.add_argument('--orphan-scan-seconds', type=float, default=30.0)
    parser.add_argument('--shutdown-grace-seconds', type=float, default=15.0)
    parser.add_argument('--data-root', type=Path, default=Path('data'))
    parser.add_argument('--detail-concurrency', type=int, choices=(1, 2), default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = create_job_engine_repository(sqlite_path=args.database)
    repository.migrate()
    slot_ids = configured_worker_slot_ids()
    if not slot_ids:
        raise RuntimeError('no worker slot is configured')

    from app.auth_service.client import AuthServiceClient
    from app.utils.date_utils import BUSINESS_TIMEZONE
    from app.worker_runtime.coverage_planner import CoveragePlanner
    from app.worker_runtime.pipeline import InvoiceCrawlPipeline
    from app.worker_runtime.proxy_health import ProxyHealthMonitor

    stop_event = threading.Event()
    auth_client = AuthServiceClient(stop_event=stop_event)
    health_monitor = ProxyHealthMonitor(repository)
    health_monitor.start(stop_event)
    supervisors: list[SequentialWorkerSupervisor] = []
    threads: list[threading.Thread] = []

    for slot_id in slot_ids:
        core = _build_portal_task_handler(
            repository,
            worker_id=slot_id,
            data_root=args.data_root,
            detail_concurrency=args.detail_concurrency,
            auth_client=auth_client,
        )
        pipeline = InvoiceCrawlPipeline(
            repository,
            core,
            CoveragePlanner(args.data_root),
            clock=lambda: datetime.now(BUSINESS_TIMEZONE),
        )
        supervisor = SequentialWorkerSupervisor(
            repository,
            worker_id=slot_id,
            pipeline=pipeline,
            job_lease_seconds=args.job_lease_seconds,
            heartbeat_interval_seconds=args.heartbeat_seconds,
            shutdown_grace_seconds=args.shutdown_grace_seconds,
        )
        supervisors.append(supervisor)
        loop = WorkerLoop(
            supervisor,
            idle_backoff_seconds=args.idle_backoff_seconds,
            error_backoff_seconds=args.error_backoff_seconds,
            orphan_scan_seconds=args.orphan_scan_seconds,
        )
        threads.append(threading.Thread(
            target=loop.run,
            args=(stop_event,),
            name=f'worker-{slot_id}',
            daemon=False,
        ))

    def request_shutdown(signum, _frame) -> None:
        logger.info('job_engine event=shutdown_requested signal=%s', signum)
        stop_event.set()
        for supervisor in supervisors:
            supervisor.request_shutdown()

    signal.signal(signal.SIGINT, request_shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, request_shutdown)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    health_monitor.join()
    return 0


def configured_worker_slot_ids() -> tuple[str, ...]:
    max_slots = _env_int('MIA_MAX_WORKER_SLOTS', 4, minimum=1)
    direct_count = _env_int('MIA_BASE_DIRECT_WORKER_SLOTS', 1, minimum=1)
    per_proxy = _env_int('MIA_WORKER_PER_HEALTHY_PROXY', 1, minimum=1)
    result: list[str] = []
    for index in range(min(direct_count, max_slots)):
        suffix = '' if index == 0 else f'-{index + 1}'
        result.append(f'slot-direct{suffix}')
    for proxy in parse_proxy_list(os.getenv('MIA_WORKER_PROXIES')):
        proxy_id = 'proxy-' + hashlib.sha256(proxy.encode()).hexdigest()[:12]
        for index in range(per_proxy):
            suffix = '' if index == 0 else f'-{index + 1}'
            result.append(f'slot-{proxy_id}{suffix}')
            if len(result) >= max_slots:
                return tuple(result)
    return tuple(result[:max_slots])


def _build_portal_task_handler(
    repository,
    *,
    worker_id: str,
    data_root: Path,
    detail_concurrency: int,
    auth_client,
):
    from app.config.crawl_config import CrawlConfig
    from app.worker_runtime.proxy import ProxyRegistry
    from app.worker_runtime.remote_auth_handler import (
        RemoteAuthInvoiceCrawlTaskHandler,
    )

    config = CrawlConfig.from_env()
    return RemoteAuthInvoiceCrawlTaskHandler(
        repository,
        worker_id=worker_id,
        data_root=data_root,
        crawl_config=config,
        proxy_registry=ProxyRegistry(direct_capacity=detail_concurrency),
        runtime_proxies=(),
        allow_cross_route_token=False,
        auth_client=auth_client,
    )


def _env_int(name: str, default: int, *, minimum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f'{name} must be at least {minimum}')
    return value


if __name__ == '__main__':
    raise SystemExit(main())
