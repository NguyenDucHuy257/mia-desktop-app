import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ArtifactBatchRequest, ArtifactBatchStatus } from '../../lib/runtime-bridge';
import { diagnosticLog } from '../../lib/diagnostic-logger';

const TERMINAL = new Set(['completed', 'failed', 'stopped']);
const FATAL_MONITOR_ERRORS = new Set(['runtime_exited', 'runtime_not_running', 'runtime_spawn_failed']);

export function useArtifactDownloadLifecycle() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<ArtifactBatchStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const generation = useRef(0);

  const poll = useCallback(async (id: string, token: number) => {
    const next = await window.miaRuntime!.artifacts.batchStatus({ task_id: id });
    if (token !== generation.current) return false;
    setStatus(next);
    if (TERMINAL.has(next.status)) {
      diagnosticLog(next.status === 'failed' ? 'artifact_download_failed' : 'artifact_download_completed', {
        task_id: id, status: next.status, warning_count: next.warning_count, error: next.error,
      }, next.status === 'failed' ? 'error' : 'info');
      setTaskId(null);
      setMessage(next.status === 'completed'
        ? `Đã hoàn thành tải XML/HTML/PDF${next.warning_count ? `; ${next.warning_count} hóa đơn gặp lỗi cần kiểm tra.` : '.'}`
        : next.status === 'stopped' ? 'Đã dừng tải XML/HTML/PDF.' : 'Không thể hoàn thành tác vụ tải XML/HTML/PDF.');
      return false;
    }
    return true;
  }, []);

  useEffect(() => {
    if (!taskId) return;
    const token = generation.current;
    let cancelled = false;
    let timer: number | undefined;
    const schedule = (delay: number) => {
      timer = window.setTimeout(async () => {
        if (cancelled || token !== generation.current) return;
        let keepPolling = true;
        try {
          keepPolling = await poll(taskId, token);
        } catch (error) {
          // A status timeout is only a transient monitoring failure. Keep the
          // last authoritative snapshot and retry without changing task state.
          const code = (error as { code?: string })?.code;
          if (code && FATAL_MONITOR_ERRORS.has(code)) {
            keepPolling = false;
            setTaskId(null);
            setMessage('Runtime cục bộ đã dừng khi đang tải XML/HTML/PDF.');
            diagnosticLog('artifact_download_failed', { phase: 'status', code }, 'error');
          }
        }
        if (!cancelled && keepPolling && token === generation.current) schedule(750);
      }, delay);
    };
    schedule(0);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [poll, taskId]);

  const start = useCallback(async (request: ArtifactBatchRequest) => {
    if (taskId) return false;
    const token = ++generation.current;
    setMessage(null);
    setStatus(null);
    try {
      diagnosticLog('artifact_download_requested', {
        account_count: request.connection_ids.length, directions: request.directions,
        date_from: request.date_from, date_to: request.date_to, kinds: request.kinds,
      });
      const started = await window.miaRuntime!.artifacts.startBatch(request);
      if (token !== generation.current) return false;
      setTaskId(started.task_id);
      return true;
    } catch (error) {
      diagnosticLog('artifact_download_failed', { phase: 'start', code: (error as { code?: string })?.code }, 'error');
      if (token === generation.current) setMessage('Không thể bắt đầu tải XML/HTML/PDF.');
      return false;
    }
  }, [taskId]);

  const stop = useCallback(async () => {
    if (!taskId) return;
    await window.miaRuntime?.artifacts.cancelBatch();
  }, [taskId]);

  return useMemo(() => ({
    active: Boolean(taskId), status, message, start, stop,
    clearMessage: () => setMessage(null),
  }), [message, start, status, stop, taskId]);
}

export type ArtifactDownloadLifecycle = ReturnType<typeof useArtifactDownloadLifecycle>;
