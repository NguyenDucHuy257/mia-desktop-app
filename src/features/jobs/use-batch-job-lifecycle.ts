import { useCallback, useEffect, useRef, useState } from 'react';
import type { CreateJobRequest, JobStatusResponse } from '../../lib/api/contracts';
import type { PersistedJob } from '../../lib/runtime-bridge';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import { TERMINAL_JOB_STATUSES, backoffDelay } from './job-state-machine';
import { normalizeBatch } from './batch-scheduler';

const POLL_MS = 3_000;
const MAX_RETRIES = 5;

export type BatchPhase = 'queued' | 'starting' | 'stopping' | 'stopped';

export interface BatchItem {
  connectionId: string;
  record?: PersistedJob;
  status?: JobStatusResponse;
  error?: string;
  errorCode?: string;
  phase?: BatchPhase;
}

function isTerminalStatus(status?: string | null) {
  return Boolean(status && TERMINAL_JOB_STATUSES.has(status as JobStatusResponse['status']));
}

export function currentSessionJobRecords(records: PersistedJob[]) {
  return records.filter((record) => record.status && !isTerminalStatus(record.status));
}

export function jobFailureMessage(code?: string) {
  if (code === 'invalid_source_credentials') return 'Tên đăng nhập hoặc mật khẩu không đúng.';
  if (code === 'source_account_locked') return 'Tài khoản đã bị khóa vì nhập sai thông tin quá số lần quy định.';
  if (code === 'source_login_rejected') return 'Cổng hóa đơn từ chối đăng nhập.';
  if (code === 'source_token_missing') return 'Cổng hóa đơn không trả về phiên đăng nhập hợp lệ.';
  if (code === 'source_rate_limited') return 'Cổng hóa đơn đang giới hạn truy cập. Vui lòng thử lại sau.';
  if (code?.startsWith('source_http_')) return 'Dịch vụ cổng hóa đơn đang tạm thời không khả dụng.';
  if (code === 'portal_auth_failed') return 'Không thể xác thực lại tài khoản.';
  if (code === 'overview_failed') return 'Không thể tải dữ liệu tổng quan.';
  if (code === 'detail_failed') return 'Không thể tải dữ liệu chi tiết.';
  if (code === 'crawler_runtime_unavailable') return 'Bộ xử lý crawler không thể khởi tạo.';
  return 'Crawler không thể hoàn thành yêu cầu.';
}

function jobStartFailureMessage(code?: string) {
  if (code === 'empty_job_selection' || code === 'invalid_job_input' || code === 'invalid_params') {
    return 'Yêu cầu đồng bộ không hợp lệ.';
  }
  if (code === 'runtime_restart_exhausted' || code === 'runtime_not_running' || code === 'crawler_runtime_unavailable') {
    return 'Bộ xử lý đồng bộ chưa sẵn sàng.';
  }
  if (code && code !== 'internal_error' && code !== 'production_backend_failed') {
    return `Không thể tạo tác vụ đồng bộ (${code}).`;
  }
  return 'Không thể tạo tác vụ đồng bộ. Xem Nhật ký để biết chi tiết.';
}

export function useBatchJobLifecycle({ hydrateExisting = true }: { hydrateExisting?: boolean } = {}) {
  const [items, setItems] = useState<Record<string, BatchItem>>({});
  const [message, setMessage] = useState<{ kind: 'notice' | 'error' | 'success'; text: string } | null>(null);
  const [active, setActive] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [coverageRevision, setCoverageRevision] = useState(0);
  const itemsRef = useRef<Record<string, BatchItem>>({});
  const activeRef = useRef(false);
  const stoppingRef = useRef(false);
  const generation = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const pending = useRef<CreateJobRequest[]>([]);
  const batchConnectionIds = useRef(new Set<string>());
  const startingConnectionId = useRef<string | null>(null);
  const cancellingJobs = useRef(new Set<string>());
  const retryLimit = useRef(MAX_RETRIES);
  const lastLoggedStatus = useRef(new Map<string, string>());

  const updateItems = useCallback((updater: (current: Record<string, BatchItem>) => Record<string, BatchItem>) => {
    setItems((current) => {
      const next = updater(current);
      itemsRef.current = next;
      return next;
    });
  }, []);

  const stopTimers = useCallback(() => {
    generation.current += 1;
    for (const timer of timers.current) clearTimeout(timer);
    timers.current.clear();
  }, []);

  const finishStoppingIfDone = useCallback(() => {
    if (!stoppingRef.current) return;
    if (startingConnectionId.current !== null || cancellingJobs.current.size > 0) return;
    stoppingRef.current = false;
    activeRef.current = false;
    setStopping(false);
    setActive(false);
    setMessage({ kind: 'notice', text: 'Đã dừng phiên đồng bộ.' });
  }, []);

  const launchNext = useCallback(async (token: number) => {
    const jobs = window.miaRuntime?.jobs;
    if (token !== generation.current || !jobs || stoppingRef.current) return;
    const intent = pending.current.shift();
    if (!intent) {
      activeRef.current = false;
      setActive(false);
      return;
    }

    startingConnectionId.current = intent.connection_id;
    updateItems((current) => ({
      ...current,
      [intent.connection_id]: {
        ...current[intent.connection_id],
        connectionId: intent.connection_id,
        phase: 'starting',
        error: undefined,
        errorCode: undefined,
      },
    }));
    diagnosticLog('job_start_requested', {
      date_from: intent.date_from,
      date_to: intent.date_to,
      force_refresh: Boolean(intent.force_refresh),
      directions: intent.directions,
      scopes: intent.scopes,
    });

    try {
      const { record } = await jobs.start(intent);
      if (startingConnectionId.current === intent.connection_id) startingConnectionId.current = null;

      // Stop may be pressed while jobs.start() is still in flight. If that
      // happens, never leave the newly-created source job orphaned: cancel it
      // as soon as the RPC returns and keep the batch locked until cancellation
      // reaches a terminal state.
      if (token !== generation.current || stoppingRef.current) {
        if (!record.job_id) {
          finishStoppingIfDone();
          return;
        }
        cancellingJobs.current.add(record.job_id);
        updateItems((current) => ({
          ...current,
          [intent.connection_id]: {
            ...current[intent.connection_id],
            connectionId: intent.connection_id,
            record,
            phase: 'stopping',
          },
        }));
        try {
          const status = await jobs.cancel(record.job_id);
          updateItems((current) => ({
            ...current,
            [intent.connection_id]: {
              ...current[intent.connection_id],
              connectionId: intent.connection_id,
              record,
              status,
              phase: isTerminalStatus(status.status) ? 'stopped' : 'stopping',
            },
          }));
          if (isTerminalStatus(status.status)) {
            cancellingJobs.current.delete(record.job_id);
            finishStoppingIfDone();
          } else {
            void poll(record.job_id, intent.connection_id, 0, generation.current);
          }
        } catch (error) {
          diagnosticLog('job_cancel_after_start_failed', {
            job_id: record.job_id,
            connection_id: intent.connection_id,
            code: (error as { code?: string })?.code,
          }, 'warn');
          void poll(record.job_id, intent.connection_id, 0, generation.current);
        }
        return;
      }

      const jobId = record.job_id;
      if (!jobId) {
        diagnosticLog('job_start_missing_id', { connection_id: intent.connection_id }, 'error');
        updateItems((current) => ({
          ...current,
          [intent.connection_id]: {
            connectionId: intent.connection_id,
            error: 'Bộ xử lý không trả về mã tác vụ đồng bộ.',
            errorCode: 'missing_job_id',
          },
        }));
        void launchNext(token);
        return;
      }

      diagnosticLog('job_started', { job_id: jobId, connection_id: intent.connection_id, status: record.status });
      updateItems((current) => ({
        ...current,
        [intent.connection_id]: {
          connectionId: intent.connection_id,
          record,
        },
      }));
      void poll(jobId, intent.connection_id, 0, token);
    } catch (error) {
      if (startingConnectionId.current === intent.connection_id) startingConnectionId.current = null;
      if (token !== generation.current || stoppingRef.current) {
        finishStoppingIfDone();
        return;
      }
      const code = (error as { code?: string })?.code;
      diagnosticLog('job_start_failed', { connection_id: intent.connection_id, code, name: (error as Error)?.name }, 'error');
      updateItems((current) => ({
        ...current,
        [intent.connection_id]: {
          connectionId: intent.connection_id,
          error: jobStartFailureMessage(code),
          errorCode: code,
        },
      }));
      // One local worker, one sequential batch. Only advance after this account
      // has definitively failed to start.
      void launchNext(token);
    }
  // poll is intentionally resolved from the current render, as in the previous
  // implementation; launchNext is only invoked after hook initialization.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishStoppingIfDone, updateItems]);

  const poll = useCallback(async (jobId: string, connectionId: string, attempt: number, token: number) => {
    if (token !== generation.current || !window.miaRuntime?.jobs) return;
    try {
      const status = await window.miaRuntime.jobs.status(jobId);
      if (token !== generation.current) return;
      // The source may finalize one monthly Overview checkpoint while the
      // overall multi-month job is still active. Consumers can re-read the
      // persisted coverage on this existing lifecycle cadence.
      setCoverageRevision((current) => current + 1);
      updateItems((current) => ({
        ...current,
        [connectionId]: {
          ...current[connectionId],
          connectionId,
          status,
          phase: stoppingRef.current && !isTerminalStatus(status.status)
            ? 'stopping'
            : status.status === 'cancelled'
              ? 'stopped'
              : undefined,
          error: undefined,
          errorCode: undefined,
        },
      }));
      const fingerprint = JSON.stringify([
        status.status,
        status.stage,
        status.message,
        status.overall_percent,
        status.current_month?.key,
        status.current_month?.processed,
        status.current_month?.planned,
        status.current_month?.percent,
        status.artifact_progress?.processed,
        status.artifact_progress?.completed_xml,
        status.artifact_progress?.completed_html,
        status.error?.code,
      ]);
      if (lastLoggedStatus.current.get(jobId) !== fingerprint) {
        lastLoggedStatus.current.set(jobId, fingerprint);
        diagnosticLog('job_progress', {
          job_id: jobId,
          connection_id: connectionId,
          status: status.status,
          stage: status.stage,
          overall_percent: status.overall_percent,
          current_month: status.current_month,
          artifact_processed: status.artifact_progress?.processed,
          artifact_completed_xml: status.artifact_progress?.completed_xml,
          artifact_completed_html: status.artifact_progress?.completed_html,
          error_code: status.error?.code,
        }, status.status === 'failed' ? 'error' : 'info');
      }
      if (isTerminalStatus(status.status)) {
        cancellingJobs.current.delete(jobId);
        if (stoppingRef.current) {
          finishStoppingIfDone();
          return;
        }
        void launchNext(token);
        return;
      }
      const timer = setTimeout(() => { timers.current.delete(timer); void poll(jobId, connectionId, 0, token); }, POLL_MS);
      timers.current.add(timer);
    } catch (error) {
      const code = (error as { code?: string })?.code;
      diagnosticLog('job_poll_failed', { job_id: jobId, connection_id: connectionId, attempt, code }, 'warn');
      if (attempt >= retryLimit.current) {
        updateItems((current) => ({
          ...current,
          [connectionId]: {
            ...current[connectionId],
            connectionId,
            error: stoppingRef.current
              ? 'Không thể xác nhận tác vụ đã dừng. Đồng bộ mới vẫn được khóa để tránh chạy chồng job.'
              : 'Mất kết nối khi cập nhật tiến trình. Đồng bộ mới được khóa để tránh chạy chồng job.',
            errorCode: code,
          },
        }));
        // Never start the next account merely because polling failed. The
        // current source job may still be running, so advancing would violate
        // the single-worker desktop invariant.
        return;
      }
      const timer = setTimeout(() => { timers.current.delete(timer); void poll(jobId, connectionId, attempt + 1, token); }, backoffDelay(attempt + 1));
      timers.current.add(timer);
    }
  }, [finishStoppingIfDone, launchNext, updateItems]);

  useEffect(() => {
    if (!hydrateExisting) return stopTimers;
    const token = generation.current;
    const jobs = window.miaRuntime?.jobs;
    if (!jobs) return stopTimers;
    const hydrate = typeof jobs.resumeAll === 'function'
      ? jobs.resumeAll()
      : jobs.resume().then((record) => record ? [record] : []);
    const preferences = window.miaRuntime?.preferences?.get().catch(() => null) ?? Promise.resolve(null);
    void Promise.all([hydrate, preferences]).then(([records, restoredPreferences]) => {
      if (token !== generation.current) return;
      retryLimit.current = restoredPreferences?.retries ?? MAX_RETRIES;
      const restored: Record<string, BatchItem> = {};
      const activeRecords = currentSessionJobRecords(records);
      for (const record of activeRecords) {
        restored[record.connection_id] = { connectionId: record.connection_id, record };
        if (record.job_id) {
          void poll(record.job_id, record.connection_id, 0, token);
        }
      }
      updateItems((current) => ({ ...restored, ...current }));
      if (activeRecords.length > 0) {
        activeRef.current = true;
        setActive(true);
        batchConnectionIds.current = new Set(activeRecords.map((record) => record.connection_id));
      }
      diagnosticLog('job_state_hydrated', { account_count: activeRecords.length, active_count: activeRecords.length });
    }).catch((error) => diagnosticLog('job_state_hydrate_failed', { code: (error as { code?: string })?.code }, 'warn'));
    return stopTimers;
  }, [hydrateExisting, poll, stopTimers, updateItems]);

  const startMany = useCallback((intents: CreateJobRequest[]) => {
    // Lock in the hook itself, not only in the button. Two clicks can arrive in
    // the same render frame before React has painted disabled=true.
    if (activeRef.current) {
      diagnosticLog('job_batch_start_ignored', { reason: 'batch_already_active' }, 'warn');
      setMessage({ kind: 'notice', text: 'Đang có một phiên đồng bộ. Hãy dừng hoặc chờ phiên hiện tại hoàn tất.' });
      return;
    }

    const normalized = normalizeBatch(intents);
    if (normalized.length === 0) return;
    stopTimers();
    const token = generation.current;
    activeRef.current = true;
    stoppingRef.current = false;
    startingConnectionId.current = null;
    cancellingJobs.current.clear();
    batchConnectionIds.current = new Set(normalized.map((intent) => intent.connection_id));
    setActive(true);
    setStopping(false);
    diagnosticLog('job_batch_started', {
      count: normalized.length,
      execution_mode: 'local-sequential',
      date_from: normalized[0]?.date_from,
      date_to: normalized[0]?.date_to,
      force_refresh: normalized.some((intent) => Boolean(intent.force_refresh)),
    });
    setMessage(null);
    pending.current = [...normalized];
    updateItems((current) => ({
      ...current,
      ...Object.fromEntries(normalized.map((intent) => [intent.connection_id, {
        connectionId: intent.connection_id,
        phase: 'queued' as const,
      }])),
    }));
    void (async () => {
      const preferences = await window.miaRuntime?.preferences?.get().catch(() => null);
      if (token !== generation.current) return;
      retryLimit.current = preferences?.retries ?? MAX_RETRIES;
      // One desktop runtime owns one source worker. Start exactly one account;
      // launchNext advances only after that account reaches a terminal state.
      void launchNext(token);
    })();
  }, [launchNext, stopTimers, updateItems]);

  const cancelAll = useCallback(async () => {
    if (!activeRef.current || stoppingRef.current) return;
    stoppingRef.current = true;
    setStopping(true);
    setMessage({ kind: 'notice', text: 'Đang dừng phiên đồng bộ…' });

    const ids = new Set(batchConnectionIds.current);
    const snapshot = itemsRef.current;
    const cancellable = Object.values(snapshot).filter((item) => {
      if (!ids.has(item.connectionId) || !item.record?.job_id) return false;
      const status = item.status?.status ?? item.record.status;
      return !isTerminalStatus(status);
    });

    diagnosticLog('job_batch_cancel_requested', {
      active_count: cancellable.length,
      queued_count: pending.current.length,
      start_in_flight: Boolean(startingConnectionId.current),
    }, 'warn');

    // Invalidate all outstanding poll/start continuations and remove every
    // not-yet-started account from the local sequential queue immediately.
    pending.current = [];
    stopTimers();
    const stopToken = generation.current;

    updateItems((current) => {
      const next = { ...current };
      for (const connectionId of ids) {
        const item = next[connectionId];
        if (!item) continue;
        const status = item.status?.status ?? item.record?.status;
        if (isTerminalStatus(status)) continue;
        next[connectionId] = {
          ...item,
          phase: item.record?.job_id || item.phase === 'starting' ? 'stopping' : 'stopped',
        };
      }
      return next;
    });

    await Promise.all(cancellable.map(async (item) => {
      const jobId = item.record?.job_id;
      if (!jobId || !window.miaRuntime?.jobs) return;
      cancellingJobs.current.add(jobId);
      try {
        const status = await window.miaRuntime.jobs.cancel(jobId);
        updateItems((current) => ({
          ...current,
          [item.connectionId]: {
            ...current[item.connectionId],
            connectionId: item.connectionId,
            status,
            phase: isTerminalStatus(status.status) ? 'stopped' : 'stopping',
          },
        }));
        if (isTerminalStatus(status.status)) {
          cancellingJobs.current.delete(jobId);
        } else {
          void poll(jobId, item.connectionId, 0, stopToken);
        }
      } catch (error) {
        diagnosticLog('job_cancel_failed', {
          job_id: jobId,
          connection_id: item.connectionId,
          code: (error as { code?: string })?.code,
        }, 'warn');
        // Keep the batch locked and poll the authoritative source status. This
        // prevents a failed cancel RPC from opening a second sync on top.
        void poll(jobId, item.connectionId, 0, stopToken);
      }
    }));

    finishStoppingIfDone();
  }, [finishStoppingIfDone, poll, stopTimers, updateItems]);

  return {
    items,
    active,
    stopping,
    coverageRevision,
    startMany,
    cancelAll,
    message,
    dismissMessage: () => setMessage(null),
  };
}

export type BatchJobLifecycle = ReturnType<typeof useBatchJobLifecycle>;
