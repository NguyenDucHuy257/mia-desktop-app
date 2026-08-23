import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ArtifactBatchRequest, ArtifactBatchStatus, InvoiceArtifactKind } from '../../lib/runtime-bridge';

const TERMINAL = new Set(['completed', 'failed', 'stopped']);

export function useArtifactDownloadLifecycle() {
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<ArtifactBatchStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const generation = useRef(0);

  const poll = useCallback(async (id: string, token: number) => {
    const next = await window.miaRuntime!.artifacts.batchStatus({ task_id: id });
    if (token !== generation.current) return;
    setStatus(next);
    if (TERMINAL.has(next.status)) {
      setTaskId(null);
      setMessage(next.status === 'completed'
        ? `Đã hoàn thành tải artifact${next.warning_count ? `; ${next.warning_count} mục lỗi/không khả dụng đã được ghi nhật ký.` : '.'}`
        : next.status === 'stopped' ? 'Đã dừng tải artifact.' : 'Không thể hoàn thành tác vụ tải artifact.');
    }
  }, []);

  useEffect(() => {
    if (!taskId) return;
    const token = generation.current;
    void poll(taskId, token).catch(() => undefined);
    const timer = window.setInterval(() => void poll(taskId, token).catch(() => undefined), 750);
    return () => window.clearInterval(timer);
  }, [poll, taskId]);

  const start = useCallback(async (request: ArtifactBatchRequest) => {
    if (taskId) return false;
    const token = ++generation.current;
    setMessage(null);
    setStatus(null);
    try {
      const started = await window.miaRuntime!.artifacts.startBatch(request);
      if (token !== generation.current) return false;
      setTaskId(started.task_id);
      return true;
    } catch {
      if (token === generation.current) setMessage('Không thể bắt đầu tải XML/HTML/PDF.');
      return false;
    }
  }, [taskId]);

  const stop = useCallback(async (kind?: InvoiceArtifactKind) => {
    if (!taskId) return;
    if (kind) {
      setStatus((current) => current?.formats[kind]
        ? { ...current, formats: { ...current.formats, [kind]: { ...current.formats[kind]!, status: 'stopping' } } }
        : current);
    }
    await window.miaRuntime?.artifacts.cancelBatch({ kind: kind ?? null });
  }, [taskId]);

  return useMemo(() => ({
    active: Boolean(taskId), status, message, start, stop,
    clearMessage: () => setMessage(null),
  }), [message, start, status, stop, taskId]);
}

export type ArtifactDownloadLifecycle = ReturnType<typeof useArtifactDownloadLifecycle>;
