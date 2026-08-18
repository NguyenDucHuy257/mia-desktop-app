import { useCallback, useEffect, useRef, useState } from 'react';
import type { CreateJobRequest, JobStatusResponse } from '../../lib/api/contracts';
import type { PersistedJob } from '../../lib/runtime-bridge';
import { TERMINAL_JOB_STATUSES, backoffDelay } from './job-state-machine';
import { DEFAULT_BATCH_CONCURRENCY, normalizeBatch } from './batch-scheduler';

const POLL_MS = 3_000;
const MAX_RETRIES = 5;

export interface BatchItem { connectionId: string; record?: PersistedJob; status?: JobStatusResponse; error?: string }

export function useBatchJobLifecycle() {
  const [items, setItems] = useState<Record<string, BatchItem>>({});
  const [message, setMessage] = useState<{ kind: 'notice' | 'error'; text: string } | null>(null);
  const generation = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const pending = useRef<CreateJobRequest[]>([]);
  const retryLimit = useRef(MAX_RETRIES);

  const stopTimers = useCallback(() => {
    generation.current += 1;
    for (const timer of timers.current) clearTimeout(timer);
    timers.current.clear();
  }, []);

  const launchNext = useCallback(async (token: number) => {
    if (token !== generation.current || !window.miaRuntime?.jobs) return;
    const intent = pending.current.shift();
    if (!intent) return;
    try {
      const { record } = await window.miaRuntime.jobs.start(intent);
      if (token !== generation.current || !record.job_id) return;
      setItems((current) => ({ ...current, [intent.connection_id]: { connectionId: intent.connection_id, record } }));
      void poll(record.job_id, intent.connection_id, 0, token);
    } catch {
      setItems((current) => ({ ...current, [intent.connection_id]: { connectionId: intent.connection_id, error: 'Không thể tạo job.' } }));
      setMessage({ kind: 'error', text: 'Không thể tạo job. Vui lòng thử lại.' });
      void launchNext(token);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const poll = useCallback(async (jobId: string, connectionId: string, attempt: number, token: number) => {
    if (token !== generation.current || !window.miaRuntime?.jobs) return;
    try {
      const status = await window.miaRuntime.jobs.status(jobId);
      if (token !== generation.current) return;
      setItems((current) => ({ ...current, [connectionId]: { ...current[connectionId], connectionId, status } }));
      setMessage(null);
      if (TERMINAL_JOB_STATUSES.has(status.status)) { void launchNext(token); return; }
      const timer = setTimeout(() => { timers.current.delete(timer); void poll(jobId, connectionId, 0, token); }, POLL_MS);
      timers.current.add(timer);
    } catch {
      setMessage(attempt >= retryLimit.current
        ? { kind: 'error', text: 'Không thể cập nhật tiến trình. Vui lòng thử lại.' }
        : { kind: 'notice', text: 'Mất kết nối tạm thời, đang thử lại…' });
      if (attempt >= retryLimit.current) {
        setItems((current) => ({ ...current, [connectionId]: { ...current[connectionId], connectionId, error: 'Mất kết nối khi cập nhật tiến trình.' } }));
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
    const resume = jobs && typeof jobs.resumeAll === 'function'
      ? jobs.resumeAll()
      : jobs?.resume().then((record) => record ? [record] : []);
    void resume?.then((records) => {
      if (token !== generation.current) return;
      const resumed: Record<string, BatchItem> = {};
      for (const record of records) {
        resumed[record.connection_id] = { connectionId: record.connection_id, record };
        if (record.job_id) void poll(record.job_id, record.connection_id, 0, token);
      }
      setItems(resumed);
    }).catch(() => undefined);
    return stopTimers;
  }, [poll, stopTimers]);

  const startMany = useCallback((intents: CreateJobRequest[]) => {
    stopTimers();
    const token = generation.current;
    const normalized = normalizeBatch(intents);
    setMessage(null);
    pending.current = [...normalized];
    setItems(Object.fromEntries(normalized.map((intent) => [intent.connection_id, { connectionId: intent.connection_id }])));
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
    await Promise.all(Object.values(items).map(async (item) => {
      if (!item.record?.job_id || !window.miaRuntime?.jobs) return;
      const status = await window.miaRuntime.jobs.cancel(item.record.job_id);
      setItems((current) => ({ ...current, [item.connectionId]: { ...current[item.connectionId], connectionId: item.connectionId, status } }));
    }));
  }, [items]);

  return { items, startMany, cancelAll, message, dismissMessage: () => setMessage(null) };
}
