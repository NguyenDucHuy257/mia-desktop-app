from __future__ import annotations

import os
import uuid
from datetime import timedelta

from app.job_engine.admission import (
    ImmediateAdmissionJobRepository,
    _ADMISSION_SCHEMA,
    _env_int,
    _execute_script,
    _migration_parameters,
    _migration_sql,
    _timestamp,
)
from app.job_engine.models import InvalidJobTransitionError, LeaseLostError
from app.postgres_migration_lock import acquire_control_schema_migration_lock
from app.job_engine.repository import _json_dump


class SafeImmediateAdmissionJobRepository(ImmediateAdmissionJobRepository):
    """Backend-safe migration, proxy-health columns and stale cleanup."""

    def migrate(self) -> None:
        self.delegate.migrate()
        with self.delegate._transaction() as connection:
            postgres = getattr(connection, '_connection', None) is not None
            if postgres:
                acquire_control_schema_migration_lock(connection)
            _execute_script(connection, _ADMISSION_SCHEMA)
            if postgres:
                connection.execute(
                    'ALTER TABLE crawl_jobs '
                    'ADD COLUMN IF NOT EXISTS worker_claimed_at TIMESTAMPTZ'
                )
                for name, declaration in _HEALTH_COLUMNS:
                    connection.execute(
                        f'ALTER TABLE worker_slots ADD COLUMN IF NOT EXISTS '
                        f'{name} {declaration}'
                    )
            else:
                job_columns = {
                    row['name']
                    for row in connection.execute(
                        'PRAGMA table_info(crawl_jobs)'
                    ).fetchall()
                }
                if 'worker_claimed_at' not in job_columns:
                    connection.execute(
                        'ALTER TABLE crawl_jobs ADD COLUMN worker_claimed_at TEXT'
                    )
                slot_columns = {
                    row['name']
                    for row in connection.execute(
                        'PRAGMA table_info(worker_slots)'
                    ).fetchall()
                }
                for name, declaration in _HEALTH_COLUMNS:
                    if name not in slot_columns:
                        connection.execute(
                            f'ALTER TABLE worker_slots ADD COLUMN {name} {declaration}'
                        )
            timestamp = _timestamp(self.delegate._resolve_now(None))
            self._sync_slots(connection, timestamp)
            connection.execute(
                _migration_sql(connection),
                _migration_parameters(connection),
            )

    def create_admitted_job(self, *args, **kwargs):
        self._expire_stale_admissions(kwargs.get('now'))
        return super().create_admitted_job(*args, **kwargs)

    def failover_pipeline_job(
        self,
        job_id: str,
        worker_id: str,
        lease_token: str,
        *,
        failed_proxy_id: str,
        endpoint: str,
        source_error_code: str = 'source_timeout',
        lease_seconds: int = 120,
        now=None,
    ):
        """Quarantine one source-bad proxy and move the same job to another slot.

        The transfer is atomic with account fencing. Progress_state and job_id are
        preserved. A newly assigned slot receives a fresh lease/fence and must auth
        again before continuing. If no alternate slot is immediately free, the job
        returns to the durable queue while the failed route stays quarantined.
        """
        if not failed_proxy_id.startswith('proxy-'):
            raise ValueError('failed_proxy_id is invalid')
        if not endpoint.strip():
            raise ValueError('endpoint is required')
        if lease_seconds < 10:
            raise ValueError('lease_seconds must be at least 10')

        current = self.delegate._resolve_now(now)
        timestamp = _timestamp(current)
        expires_at = _timestamp(current + timedelta(seconds=lease_seconds))
        quarantine_seconds = _env_int(
            'MIA_PROXY_SOURCE_QUARANTINE_SECONDS', 300, minimum=1
        )
        max_failovers = _env_int(
            'MIA_PROXY_SOURCE_FAILOVER_MAX', 3, minimum=1
        )
        quarantine_until = _timestamp(
            current + timedelta(seconds=quarantine_seconds)
        )

        with self.delegate._transaction() as connection:
            job = self.delegate._get_job(connection, job_id)
            self.delegate._assert_job_lease(job, worker_id, lease_token)
            parameters = dict(job.parameters)
            slot_id = parameters.get('worker_slot_id') or worker_id
            if (
                parameters.get('route_type') != 'proxy'
                or parameters.get('proxy_id') != failed_proxy_id
                or slot_id != worker_id
            ):
                raise InvalidJobTransitionError(
                    'source timeout failover requires the currently assigned proxy slot'
                )

            slot = connection.execute(
                """SELECT source_timeout_failures FROM worker_slots
                   WHERE slot_id = ? AND proxy_id = ? AND enabled = 1
                     AND current_job_id = ?""",
                (slot_id, failed_proxy_id, job_id),
            ).fetchone()
            if slot is None:
                raise LeaseLostError('source timeout proxy slot ownership was lost')
            source_failures = int(slot['source_timeout_failures'] or 0) + 1
            updated = connection.execute(
                """
                UPDATE worker_slots
                SET status = 'quarantined', current_job_id = NULL,
                    heartbeat_at = NULL, lease_expires_at = NULL,
                    source_timeout_failures = ?, last_source_timeout_at = ?,
                    source_quarantine_until = ?, updated_at = ?
                WHERE slot_id = ? AND proxy_id = ? AND enabled = 1
                  AND current_job_id = ?
                """,
                (
                    source_failures, timestamp, quarantine_until, timestamp,
                    slot_id, failed_proxy_id, job_id,
                ),
            ).rowcount
            if updated != 1:
                raise LeaseLostError('source timeout proxy quarantine lost its slot')

            connection.execute(
                """DELETE FROM account_execution_leases
                   WHERE account_key = ? AND job_id = ?
                     AND worker_slot_id = ? AND lease_token = ?""",
                (job.account_key, job_id, worker_id, lease_token),
            )

            raw_history = parameters.get('route_failover_history')
            history = [
                dict(item) for item in raw_history
                if isinstance(item, dict)
            ] if isinstance(raw_history, list) else []
            tried_proxy_ids = {
                str(item.get('from_proxy_id')) for item in history
                if isinstance(item.get('from_proxy_id'), str)
            }
            tried_proxy_ids.add(failed_proxy_id)
            failover_count = int(parameters.get('route_failover_count', 0)) + 1
            parameters['route_failover_count'] = failover_count

            allow_direct = _env_bool(
                'MIA_PROXY_SOURCE_FAILOVER_ALLOW_DIRECT', True
            )
            proxy_budget_exhausted = failover_count > max_failovers
            target = self._select_failover_target(
                connection,
                tried_proxy_ids=tried_proxy_ids,
                allow_direct=allow_direct,
                proxy_budget_exhausted=proxy_budget_exhausted,
            )

            # The configured budget limits proxy-to-proxy transfers.  When that
            # budget is exhausted, allow one final direct route if explicitly
            # enabled and immediately available.  Previously the job was
            # terminalized before target selection, so allow_direct=true could
            # never take effect when enough proxies existed.
            if proxy_budget_exhausted and target is None:
                history.append({
                    'from_proxy_id': failed_proxy_id,
                    'endpoint': endpoint,
                    'source_error_code': source_error_code,
                    'at': timestamp,
                    'to_route_type': 'terminal',
                })
                parameters['route_failover_history'] = history[-8:]
                connection.execute(
                    """
                    UPDATE crawl_jobs
                    SET parameters_json = ?, status = 'failed',
                        current_stage = NULL, worker_id = NULL,
                        lease_token = NULL, lease_started_at = NULL,
                        lease_expires_at = NULL, worker_claimed_at = NULL,
                        finished_at = ?, updated_at = ?,
                        last_error_code = 'source_timeout_after_route_failover',
                        last_error_message = ?
                    WHERE job_id = ? AND status IN ('running', 'cancelling')
                    """,
                    (
                        _json_dump(parameters), timestamp, timestamp,
                        'Tax source remained unavailable after route failover',
                        job_id,
                    ),
                )
                logger = __import__('logging').getLogger('mia.job_engine')
                logger.error(
                    'job_engine event=source_route_failover_exhausted '
                    'job_id=%s proxy_id=%s endpoint=%s failover_count=%s',
                    job_id, failed_proxy_id, endpoint, failover_count,
                )
                return self.delegate._get_job(connection, job_id)
            if target is None:
                history.append({
                    'from_proxy_id': failed_proxy_id,
                    'endpoint': endpoint,
                    'source_error_code': source_error_code,
                    'at': timestamp,
                    'to_route_type': 'queued',
                })
                parameters['route_failover_history'] = history[-8:]
                for key in ('worker_slot_id', 'route_type', 'proxy_id', 'fence_token'):
                    parameters.pop(key, None)
                connection.execute(
                    """
                    UPDATE crawl_jobs
                    SET parameters_json = ?, status = 'queued', current_stage = NULL,
                        worker_id = NULL, lease_token = NULL,
                        lease_started_at = NULL, lease_expires_at = NULL,
                        worker_claimed_at = NULL, available_at = ?, updated_at = ?,
                        last_error_code = NULL, last_error_message = NULL
                    WHERE job_id = ? AND status IN ('running', 'cancelling')
                    """,
                    (_json_dump(parameters), timestamp, timestamp, job_id),
                )
                return self.delegate._get_job(connection, job_id)

            target_slot = str(target['slot_id'])
            target_route = str(target['route_type'])
            target_proxy = target['proxy_id']
            fence_row = connection.execute(
                'SELECT fence_token FROM account_execution_fences WHERE account_key = ?',
                (job.account_key,),
            ).fetchone()
            fence_token = int(fence_row['fence_token']) + 1 if fence_row else 1
            connection.execute(
                """
                INSERT INTO account_execution_fences (account_key, fence_token, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT (account_key) DO UPDATE SET
                    fence_token = excluded.fence_token,
                    updated_at = excluded.updated_at
                """,
                (job.account_key, fence_token, timestamp),
            )
            new_lease_token = str(uuid.uuid4())
            reserved = connection.execute(
                """
                UPDATE worker_slots
                SET status = 'busy', current_job_id = ?, heartbeat_at = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE slot_id = ? AND enabled = 1 AND status = 'available'
                  AND current_job_id IS NULL
                """,
                (
                    job_id, timestamp, expires_at, timestamp, target_slot,
                ),
            ).rowcount
            if reserved != 1:
                raise LeaseLostError('source timeout failover target reservation was lost')

            parameters.update({
                'worker_slot_id': target_slot,
                'route_type': target_route,
                'proxy_id': target_proxy,
                'fence_token': fence_token,
            })
            history.append({
                'from_proxy_id': failed_proxy_id,
                'endpoint': endpoint,
                'source_error_code': source_error_code,
                'at': timestamp,
                'to_route_type': target_route,
                'to_proxy_id': target_proxy,
                'to_worker_slot_id': target_slot,
            })
            parameters['route_failover_history'] = history[-8:]
            claimed = connection.execute(
                """
                UPDATE crawl_jobs
                SET parameters_json = ?, status = 'running', current_stage = 'auth',
                    worker_id = ?, lease_token = ?,
                    lease_generation = lease_generation + 1,
                    lease_started_at = ?, lease_expires_at = ?,
                    available_at = ?, updated_at = ?, worker_claimed_at = NULL,
                    last_error_code = NULL, last_error_message = NULL
                WHERE job_id = ? AND status IN ('running', 'cancelling')
                """,
                (
                    _json_dump(parameters), target_slot, new_lease_token,
                    timestamp, expires_at, timestamp, timestamp, job_id,
                ),
            ).rowcount
            if claimed != 1:
                raise LeaseLostError('source timeout failover lost the running job')
            connection.execute(
                """
                INSERT INTO account_execution_leases (
                    account_key, job_id, worker_slot_id, fence_token,
                    lease_token, acquired_at, heartbeat_at, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.account_key, job_id, target_slot, fence_token,
                    new_lease_token, timestamp, timestamp, expires_at,
                ),
            )
            __import__('logging').getLogger('mia.job_engine').warning(
                'job_engine event=source_route_failover_transferred '
                'job_id=%s from_proxy_id=%s to_slot_id=%s to_proxy_id=%s '
                'endpoint=%s failover_count=%s',
                job_id, failed_proxy_id, target_slot, target_proxy,
                endpoint, failover_count,
            )
            return self.delegate._get_job(connection, job_id)

    @staticmethod
    def _select_failover_target(
        connection, *, tried_proxy_ids, allow_direct,
        proxy_budget_exhausted=False,
    ):
        sql = """
            SELECT slot_id, route_type, proxy_id, last_success_at
            FROM worker_slots
            WHERE enabled = 1 AND status = 'available'
              AND current_job_id IS NULL
            ORDER BY CASE WHEN route_type = 'proxy' THEN 0 ELSE 1 END,
                     CASE WHEN last_success_at IS NULL THEN 1 ELSE 0 END,
                     last_success_at DESC, slot_id
        """
        if getattr(connection, '_connection', None) is not None:
            sql += ' FOR UPDATE SKIP LOCKED'
        rows = connection.execute(sql).fetchall()
        if not proxy_budget_exhausted:
            for row in rows:
                if (
                    row['route_type'] == 'proxy'
                    and row['proxy_id'] not in tried_proxy_ids
                ):
                    return row
        if allow_direct:
            for row in rows:
                if row['route_type'] == 'direct':
                    return row
        return None

    def _sync_slots(self, connection, timestamp: str) -> None:
        super()._sync_slots(connection, timestamp)
        # A slot that was removed is persisted as enabled=0/status=disabled.
        # When the same route is configured again, the base UPSERT re-enables
        # it but deliberately preserves runtime state. Heal the one impossible
        # combination here before admission or health monitoring sees it:
        # enabled=1 + status=disabled + no current job.
        connection.execute(
            """
            UPDATE worker_slots
            SET status = CASE
                    WHEN route_type = 'proxy'
                     AND source_quarantine_until IS NOT NULL
                     AND source_quarantine_until > ? THEN 'quarantined'
                    WHEN route_type = 'proxy' THEN 'probing'
                    ELSE 'available'
                END,
                consecutive_failures = CASE
                    WHEN route_type = 'proxy' THEN consecutive_failures
                    ELSE 0
                END,
                cooldown_until = CASE
                    WHEN route_type = 'proxy' THEN cooldown_until
                    ELSE NULL
                END,
                updated_at = ?
            WHERE enabled = 1
              AND current_job_id IS NULL
              AND status = 'disabled'
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            UPDATE worker_slots
            SET status = 'probing', updated_at = ?
            WHERE route_type = 'proxy'
              AND current_job_id IS NULL
              AND last_success_at IS NULL
              AND status = 'available'
              AND (source_quarantine_until IS NULL OR source_quarantine_until <= ?)
            """,
            (timestamp, timestamp),
        )

    def _expire_stale_admissions(self, now=None) -> None:
        current = self.delegate._resolve_now(now)
        timestamp = _timestamp(current)
        with self.delegate._transaction() as connection:
            rows = connection.execute(
                """
                SELECT job_id FROM crawl_jobs
                WHERE status IN ('running', 'cancelling')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                  AND parameters_json LIKE ?
                """,
                (timestamp, '%worker_slot_id%'),
            ).fetchall()
            for row in rows:
                job = self.delegate._get_job(connection, row['job_id'])
                connection.execute(
                    """
                    UPDATE crawl_jobs
                    SET status = 'abandoned', current_stage = NULL,
                        worker_id = NULL, lease_token = NULL,
                        lease_started_at = NULL, lease_expires_at = NULL,
                        finished_at = ?, updated_at = ?,
                        last_error_code = 'worker_lease_expired',
                        last_error_message = 'Worker lease expired; create a new job'
                    WHERE job_id = ? AND status IN ('running', 'cancelling')
                    """,
                    (timestamp, timestamp, job.job_id),
                )
                self._release_resources(connection, job, timestamp)


_HEALTH_COLUMNS = (
    ('consecutive_failures', 'INTEGER NOT NULL DEFAULT 0'),
    ('last_checked_at', 'TEXT'),
    ('last_success_at', 'TEXT'),
    ('cooldown_until', 'TEXT'),
    ('source_timeout_failures', 'INTEGER NOT NULL DEFAULT 0'),
    ('last_source_timeout_at', 'TEXT'),
    ('source_quarantine_until', 'TEXT'),
)


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
