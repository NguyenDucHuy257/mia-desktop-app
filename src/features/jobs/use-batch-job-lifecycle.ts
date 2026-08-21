import { useCallback, useEffect, useRef, useState } from 'react';
import type { CreateJobRequest, JobStatusResponse, JobSummaryResponse } from '../../lib/api/contracts';
import type { PersistedJob } from '../../lib/runtime-bridge';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import { TERMINAL_JOB_STATUSES, backoffDelay } from './job-state-machine';
import { DEFAULT_BATCH_CONCURRENCY, normalizeBatch } from './batch-scheduler';

const POLL_MS = 3_000;
const MAX_RETRIES = 5;

export interface BatchItem {
  connectionId: string;
  record?: PersistedJob;
  status?: JobStatusResponse;
  summary?: JobSummaryResponse;
  error?: string;
  errorCode?: string;
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

export function useBatchJobLifecycle() {
  const [items, setItems] = useState<Record<string, BatchItem>>({});
  const [message, setMessage] = useState<{ kind: 'notice' | 'error' | 'success'; text: string } | null>(null);
  const generation = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const pending = useRef<CreateJobRequest[]>([]);
  const retryLimit = useRef(MAX_RETRIES);
  const lastLoggedStatus = useRef(new Map<string, string>());

  const stopTimers = useCallback(() => {
    generation.current += 1;
    for (const timer of timers.current) clearTimeout(timer);
    timers.current.clear();
  }, []);

  const launchNext = useCallback(async (token: number) => {
    if (token !== generation.current || !window.miaRuntime?.jobs) return;
    const intent = pending.current.shift();
    if (!intent) return;
    diagnosticLog('job_start_requested', {
      date_from: intent.date_from,
      date_to: intent.date_to,
      force_refresh: Boolean(intent.force_refresh),
      directions: intent.directions,
      scopes: intent.scopes,
    });
    try {
      const { record } = await window.miaRuntime.jobs.start(intent);
      if (token !== generation.current || !record.job_id) return;
      diagnosticLog('job_started', { job_id: record.job_id, connection_id: intent.connection_id, status: record.status });
      setItems((current) => ({ ...current, [intent.connection_id]: { connectionId: intent.connection_id, record } }));
      void poll(record.job_id, intent.connection_id, 0, token);
    } catch (error) {
      const code = (error as { code?: string })?.code;
      diagnosticLog('job_start_failed', { connection_id: intent.connection_id, code, name: (error as Error)?.name }, 'error');
      setItems((current) => ({
        ...current,
        [intent.connection_id]: {
          connectionId: intent.connection_id,
          error: jobStartFailureMessage(code),
          errorCode: code,
        },
      }));
      void launchNext(token);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const poll = useCallback(async (jobId: string, connectionId: string, attempt: number, token: number) => {
    if (token !== generation.current || !window.miaRuntime?.jobs) return;
    try {
      const status = await window.miaRuntime.jobs.status(jobId);
      let summary: JobSummaryResponse | undefined;
      try {
        summary = await window.miaRuntime.jobs.summary(jobId);
      } catch (summaryError) {
        diagnosticLog('job_summary_poll_failed', {
          job_id: jobId,
          connection_id: connectionId,
          code: (summaryError as { code?: string })?.code,
        }, 'warn');
      }
      if (token !== generation.current) return;
      setItems((current) => ({
        ...current,
        [connectionId]: {
          ...current[connectionId],
          connectionId,
          status,
          summary: summary ?? current[connectionId]?.summary,
          error: undefined,
          errorCode: undefined,
        },
      }));
      const workMessage = typeof summary?.work?.message === 'string' ? summary.work.message : null;
      const fingerprint = JSON.stringify([
        status.status,
        status.stage,
        status.overall_percent,
        status.current_month?.key,
        status.current_month?.processed,
        status.current_month?.planned,
        status.current_month?.percent,
        status.error?.code,
        status.error?.message,
        workMessage,
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
          source_message: workMessage,
          error_code: status.error?.code,
        }, status.status === 'failed' ? 'error' : 'info');
      }
      if (TERMINAL_JOB_STATUSES.has(status.status)) {
        void launchNext(token);
        return;
      }
      const timer = setTimeout(() => { timers.current.delete(timer); void poll(jobId, connectionId, 0, token); }, POLL_MS);
      timers.current.add(timer);
    } catch (error) {
      const code = (error as { code?: string })?.code;
      diagnosticLog('job_poll_failed', { job_id: jobId, connection_id: connectionId, attempt, code }, 'warn');
      if (attempt >= retryLimit.current) {
        setItems((current) => ({
          ...current,
          [connectionId]: {
            ...current[connectionId],
            connectionId,
            error: 'Mất kết nối khi cập nhật tiến trình.',
            errorCode: code,
          },
        }));
        void launchNext(token);
        return;
      }
      const timer = setTimeout(() => { timers.current.delete(timer); void poll(jobId, connectionId, attempt + 1, token); }, backoffDelay(attempt + 1));
      timers.current.add(timer);
    }
  }, [launchNext]);

  useEffect(() => {
    const token = generation.current;
    const jobs = window.miaRuntime?.jobs;
    if (!jobs) return stopTimers;
    const hydrate = typeof jobs.latestAll === 'function'
      ? jobs.latestAll()
      : typeof jobs.resumeAll === 'function'
        ? jobs.resumeAll()
        : jobs.resume().then((record) => record ? [record] : []);
    void hydrate.then((records) => {
      if (token !== generation.current) return;
      const restored: Record<string, BatchItem> = {};
      for (const record of records) {
        restored[record.connection_id] = { connectionId: record.connection_id, record };
        if (record.job_id && record.status && !TERMINAL_JOB_STATUSES.has(record.status as JobStatusResponse['status'])) {
          void poll(record.job_id, record.connection_id, 0, token);
        }
      }
      setItems((current) => ({ ...restored, ...current }));
      diagnosticLog('job_state_hydrated', { account_count: records.length, active_count: records.filter((record) => record.status && !TERMINAL_JOB_STATUSES.has(record.status as JobStatusResponse['status'])).length });
    }).catch((error) => diagnosticLog('job_state_hydrate_failed', { code: (error as { code?: string })?.code }, 'warn'));
    return stopTimers;
  }, [poll, stopTimers]);

  const startMany = useCallback((intents: CreateJobRequest[]) => {
    stopTimers();
    const token = generation.current;
    const normalized = normalizeBatch(intents);
    diagnosticLog('job_batch_started', {
      count: normalized.length,
      date_from: normalized[0]?.date_from,
      date_to: normalized[0]?.date_to,
      force_refresh: normalized.some((intent) => Boolean(intent.force_refresh)),
    });
    setMessage(null);
    pending.current = [...normalized];
    setItems((current) => ({
      ...current,
      ...Object.fromEntries(normalized.map((intent) => [intent.connection_id, { connectionId: intent.connection_id }])),
    }));
    void (async () => {
      const preferences = await window.miaRuntime?.preferences?.get().catch(() => null);
      if (token !== generation.current) return;
      const concurrency = preferences?.concurrency ?? DEFAULT_BATCH_CONCURRENCY;
      retryLimit.current = preferences?.retries ?? MAX_RETRIES;
      for (let index = 0; index < Math.min(concurrency, normalized.length); index += 1) void launchNext(token);
    })();
  }, [launchNext, stopTimers]);

  const cancelAll = useCallback(async () => {
    pending.current = [];
    diagnosticLog('job_batch_cancel_requested', { active_count: Object.values(items).filter((item) => item.record?.job_id).length }, 'warn');
    await Promise.all(Object.values(items).map(async (item) => {
      if (!item.record?.job_id || !window.miaRuntime?.jobs) return;
      const status = await window.miaRuntime.jobs.cancel(item.record.job_id);
      setItems((current) => ({ ...current, [item.connectionId]: { ...current[item.connectionId], connectionId: item.connectionId, status } }));
    }));
    setMessage({ kind: 'notice', text: 'Đã yêu cầu dừng các tác vụ đang chạy.' });
  }, [items]);

  return { items, startMany, cancelAll, message, dismissMessage: () => setMessage(null) };
}

export type BatchJobLifecycle = ReturnType<typeof useBatchJobLifecycle>;
