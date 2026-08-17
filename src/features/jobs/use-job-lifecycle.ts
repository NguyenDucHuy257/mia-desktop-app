import { useCallback, useEffect, useReducer, useRef } from 'react';
import type { CreateJobRequest } from '../../lib/api/contracts';
import { backoffDelay, initialJobState, jobReducer, TERMINAL_JOB_STATUSES } from './job-state-machine';

const MAX_RETRIES = 5;
const POLL_MS = 3_000;

export function useJobLifecycle() {
  const [state, dispatch] = useReducer(jobReducer, initialJobState);
  const generation = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = useCallback(() => {
    generation.current += 1;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const poll = useCallback(async (jobId: string, attempt = 0, token = generation.current) => {
    const bridge = window.miaRuntime?.jobs;
    if (!bridge || token !== generation.current) return;
    try {
      const status = await bridge.status(jobId);
      if (token !== generation.current) return;
      dispatch({ type: 'status', value: status });
      if (TERMINAL_JOB_STATUSES.has(status.status)) {
        const summary = await bridge.summary(jobId).catch(() => null);
        if (summary && token === generation.current) dispatch({ type: 'summary', value: summary });
        return;
      }
      timer.current = setTimeout(() => void poll(jobId, 0, token), POLL_MS);
    } catch {
      if (token !== generation.current) return;
      const next = attempt + 1;
      if (next > MAX_RETRIES) {
        dispatch({ type: 'error', message: 'Không thể cập nhật tiến trình. Vui lòng thử lại.' });
        return;
      }
      dispatch({ type: 'retry', count: next });
      timer.current = setTimeout(() => void poll(jobId, next, token), backoffDelay(next));
    }
  }, []);

  useEffect(() => {
    const bridge = window.miaRuntime?.jobs;
    if (!bridge) return stop;
    const token = generation.current;
    void bridge.resume().then((record) => {
      if (!record || !record.job_id || token !== generation.current) return;
      dispatch({ type: 'resumed', record });
      void poll(record.job_id, 0, token);
    }).catch(() => undefined);
    return stop;
  }, [poll, stop]);

  const start = useCallback(async (intent: CreateJobRequest) => {
    const bridge = window.miaRuntime?.jobs;
    if (!bridge) { dispatch({ type: 'error', message: 'API desktop chưa được cấu hình.' }); return; }
    stop();
    const token = generation.current;
    dispatch({ type: 'starting' });
    try {
      const { record } = await bridge.start(intent);
      if (token !== generation.current || !record.job_id) return;
      dispatch({ type: 'accepted', record });
      void poll(record.job_id, 0, token);
    } catch { if (token === generation.current) dispatch({ type: 'error', message: 'Không thể tạo job. Vui lòng thử lại.' }); }
  }, [poll, stop]);

  const cancel = useCallback(async () => {
    const jobId = state.record?.job_id;
    if (!jobId || !window.miaRuntime?.jobs || state.status && TERMINAL_JOB_STATUSES.has(state.status.status)) return;
    const value = await window.miaRuntime.jobs.cancel(jobId);
    dispatch({ type: 'status', value });
  }, [state.record?.job_id, state.status]);

  const retry = useCallback(() => {
    const jobId = state.record?.job_id;
    if (!jobId) return;
    stop();
    void poll(jobId, 0, generation.current);
  }, [poll, state.record?.job_id, stop]);

  return { state, start, cancel, retry };
}
