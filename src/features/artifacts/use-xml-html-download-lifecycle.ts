import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CreateJobRequest, InvoiceDirection, InvoiceQueryType } from '../../lib/api/contracts';
import type { ArtifactExportRequest, RuntimeArtifactProgress } from '../../lib/runtime-bridge';
import { useBatchJobLifecycle } from '../jobs/use-batch-job-lifecycle';

export type InvoiceArtifactKind = 'xml' | 'html';
export type ArtifactBatchPhase = 'idle' | 'source' | 'copy' | 'completed' | 'failed' | 'stopping';

export interface XmlHtmlDownloadRequest {
  connectionId: string;
  dateFrom: string;
  dateTo: string;
  direction: InvoiceDirection | null;
  queryType: InvoiceQueryType;
  search: string;
  kinds: InvoiceArtifactKind[];
  destination: string;
  totalRows: number;
}

export function phaseWeightedPercent(sourcePercent: number, copyPercent: number, kindCount: number) {
  const copies = Math.max(1, kindCount);
  return Math.max(0, Math.min(100, (
    Math.max(0, Math.min(100, sourcePercent))
    + Math.max(0, Math.min(100, copyPercent)) * copies
  ) / (1 + copies)));
}

export function useXmlHtmlDownloadLifecycle() {
  const jobs = useBatchJobLifecycle({ hydrateExisting: false });
  const [request, setRequest] = useState<XmlHtmlDownloadRequest | null>(null);
  const [phase, setPhase] = useState<ArtifactBatchPhase>('idle');
  const [copyProgress, setCopyProgress] = useState<RuntimeArtifactProgress>({ status: 'running', processed: 0, total: 0, percent: 0 });
  const [message, setMessage] = useState<string | null>(null);
  const exportStarted = useRef(false);

  const item = request ? jobs.items[request.connectionId] : undefined;
  const job = item?.status ?? item?.record;
  const sourcePercent = Number(job?.overall_percent ?? 0);
  const percent = phase === 'completed' || phase === 'failed'
    ? 100
    : phase === 'copy'
      ? phaseWeightedPercent(100, copyProgress.percent, request?.kinds.length ?? 1)
      : phaseWeightedPercent(sourcePercent, 0, request?.kinds.length ?? 1);

  useEffect(() => window.miaRuntime?.artifacts?.onInvoiceProgress((progress) => {
    setCopyProgress(progress);
  }), []);

  useEffect(() => {
    if (!request || !job?.status || exportStarted.current) return;
    if (['failed', 'cancelled', 'abandoned'].includes(job.status)) {
      setPhase('failed');
      setMessage(job.status === 'cancelled' ? 'Đã dừng tải XML/HTML.' : 'Không thể chuẩn bị dữ liệu XML/HTML từ nguồn.');
      return;
    }
    if (!['completed', 'completed_with_warning'].includes(job.status)) return;
    exportStarted.current = true;
    setPhase('copy');
    const exportRequest: ArtifactExportRequest = {
      destination: request.destination,
      connection_ids: [request.connectionId],
      kinds: request.kinds,
      date_from: request.dateFrom,
      date_to: request.dateTo,
      direction: request.direction,
      query_type: request.queryType,
      search: request.search,
    };
    void window.miaRuntime!.artifacts.export(exportRequest).then((result) => {
      setPhase('completed');
      setMessage(`Đã tải ${result.count} file XML/HTML vào thư mục lưu trữ.`);
    }).catch((error: { code?: string; message?: string }) => {
      setPhase('failed');
      setMessage(error.code === 'artifact_batch_empty' || error.message === 'artifact_batch_empty'
        ? 'Không tồn tại hóa đơn phù hợp để tải XML/HTML.'
        : 'Không thể ghi XML/HTML vào thư mục lưu trữ.');
    });
  }, [job?.status, request]);

  const start = useCallback((value: XmlHtmlDownloadRequest) => {
    if (phase === 'source' || phase === 'copy' || phase === 'stopping') return false;
    exportStarted.current = false;
    setRequest(value);
    setMessage(null);
    setCopyProgress({ status: 'running', processed: 0, total: 0, percent: 0 });
    setPhase('source');
    const directions: InvoiceDirection[] = value.direction ? [value.direction] : ['purchase', 'sold'];
    const intent: CreateJobRequest = {
      connection_id: value.connectionId,
      date_from: value.dateFrom,
      date_to: value.dateTo,
      directions,
      query_types: [value.queryType],
      scopes: ['overview', 'detail'],
      data_types: value.kinds,
    };
    jobs.startMany([intent]);
    return true;
  }, [jobs.startMany, phase]);

  const stop = useCallback(async () => {
    if (phase !== 'source') return;
    setPhase('stopping');
    await jobs.cancelAll();
  }, [jobs.cancelAll, phase]);

  return useMemo(() => ({
    active: phase === 'source' || phase === 'copy' || phase === 'stopping',
    phase, percent, request, job, copyProgress, message, start, stop,
    clearMessage: () => setMessage(null),
  }), [copyProgress, job, message, percent, phase, request, start, stop]);
}

export type XmlHtmlDownloadLifecycle = ReturnType<typeof useXmlHtmlDownloadLifecycle>;
