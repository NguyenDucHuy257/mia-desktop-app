from __future__ import annotations

import hashlib
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

import requests

from app.config.crawl_config import parse_proxy_list


logger = logging.getLogger('mia.proxy_health')


class ProxyHealthMonitor:
    """Probe configured proxies and publish capacity to shared worker slots."""

    def __init__(self, repository) -> None:
        self.repository = repository
        self.interval_seconds = float(
            os.getenv('MIA_PROXY_HEALTHCHECK_INTERVAL_SECONDS', '60')
        )
        self.timeout_seconds = float(
            os.getenv('MIA_PROXY_HEALTHCHECK_TIMEOUT_SECONDS', '10')
        )
        self.failure_threshold = int(
            os.getenv('MIA_PROXY_FAILURE_THRESHOLD', '3')
        )
        self.cooldown_seconds = int(
            os.getenv('MIA_PROXY_COOLDOWN_SECONDS', '300')
        )
        self.health_url = os.getenv(
            'MIA_PROXY_HEALTHCHECK_URL',
            'https://hoadondientu.gdt.gov.vn',
        )
        if min(
            self.interval_seconds,
            self.timeout_seconds,
            self.failure_threshold,
            self.cooldown_seconds,
        ) <= 0:
            raise ValueError('proxy health settings must be positive')
        self._routes = {
            _proxy_id(value): value
            for value in parse_proxy_list(os.getenv('MIA_WORKER_PROXIES'))
        }
        self._thread: threading.Thread | None = None

    def start(self, stop_event: threading.Event) -> None:
        if not self._routes or not _env_bool('MIA_PROXY_HEALTHCHECK_ENABLED', True):
            return
        self.probe_once()
        self._thread = threading.Thread(
            target=self._run,
            args=(stop_event,),
            name='proxy-health-monitor',
            daemon=True,
        )
        self._thread.start()

    def join(self, timeout: float = 2.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def probe_once(self) -> None:
        for proxy_id, proxy_url in self._routes.items():
            success = self._probe(proxy_url)
            self._record(proxy_id, success)

    def _run(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.interval_seconds):
            self.probe_once()

    def _probe(self, proxy_url: str) -> bool:
        try:
            response = requests.get(
                self.health_url,
                proxies={'http': proxy_url, 'https': proxy_url},
                timeout=self.timeout_seconds,
                allow_redirects=True,
            )
            return response.status_code < 500
        except (requests.Timeout, requests.ConnectionError):
            return False

    def _record(self, proxy_id: str, success: bool) -> None:
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        with self.repository._transaction() as connection:
            rows = connection.execute(
                """SELECT slot_id, status, current_job_id,
                          consecutive_failures, cooldown_until,
                          source_timeout_failures, source_quarantine_until
                   FROM worker_slots
                   WHERE proxy_id = ? AND enabled = 1""",
                (proxy_id,),
            ).fetchall()
            for row in rows:
                source_quarantined = _deadline_in_future(
                    row['source_quarantine_until'], now
                )
                if success:
                    status = (
                        'busy' if row['current_job_id'] else
                        'quarantined' if source_quarantined else 'available'
                    )
                    if source_quarantined:
                        connection.execute(
                            """UPDATE worker_slots SET status = ?,
                               consecutive_failures = 0, last_checked_at = ?,
                               last_success_at = ?, cooldown_until = NULL,
                               updated_at = ? WHERE slot_id = ?""",
                            (
                                status, timestamp, timestamp, timestamp,
                                row['slot_id'],
                            ),
                        )
                    else:
                        connection.execute(
                            """UPDATE worker_slots SET status = ?,
                               consecutive_failures = 0, last_checked_at = ?,
                               last_success_at = ?, cooldown_until = NULL,
                               source_timeout_failures = 0,
                               source_quarantine_until = NULL,
                               updated_at = ? WHERE slot_id = ?""",
                            (
                                status, timestamp, timestamp, timestamp,
                                row['slot_id'],
                            ),
                        )
                    continue
                failures = int(row['consecutive_failures'] or 0) + 1
                cooldown_until = (
                    now + timedelta(seconds=self.cooldown_seconds)
                ).isoformat()
                status = (
                    row['status'] if row['current_job_id'] else
                    'quarantined' if source_quarantined else
                    'unhealthy' if failures >= self.failure_threshold else 'probing'
                )
                connection.execute(
                    """UPDATE worker_slots SET status = ?,
                       consecutive_failures = ?, last_checked_at = ?,
                       cooldown_until = ?, updated_at = ? WHERE slot_id = ?""",
                    (
                        status, failures, timestamp, cooldown_until,
                        timestamp, row['slot_id'],
                    ),
                )
                logger.warning(
                    'proxy_health event=probe_failed proxy_id=%s failures=%s',
                    proxy_id,
                    failures,
                )


def _proxy_id(proxy_url: str) -> str:
    return 'proxy-' + hashlib.sha256(proxy_url.encode()).hexdigest()[:12]


def _deadline_in_future(value, now: datetime) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) > now


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(f'{name} must be a boolean')
