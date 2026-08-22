import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CreateJobRequest, InvoiceDirection, InvoiceQueryType } from '../../lib/api/contracts';
import type { ArtifactExportRequest, RuntimeArtifactProgress } from '../../lib/runtime-bridge';
import { useBatchJobLifecycle } from '../jobs/use-batch-job-lifecycle';

export type InvoiceArtifactKind = 'xml' | 'html';
export type ArtifactBatchPhase = 'idle' | 'sync' | 'source' | 'copy' | 'completed' | 'failed' | 'stopping';

export interface XmlHtmlFilterRequest {
  connectionId: string;
  dateFrom: string;
  dateTo: string;
  direction: InvoiceDirection | null;
  queryType: InvoiceQueryType;
}

export interface XmlHtmlDownloadRequest extends XmlHtmlFilterRequest {
  search: string;
  kinds: InvoiceArtifactKind[];
  destination: string;
  totalRows: number;
}

export interface XmlHtmlSyncRequest extends XmlHtmlFilterRequest {
  forceRefresh: boolean;
}

export function phaseWeightedPercent(sourcePercent: number, copyPercent: number, kindCount: number) {
  const copies = Math.max(1, kindCount);
  return Math.max(0, Math.min(100, (
    Math.max(0, Math.min(100, sourcePercent))
    + Math.max(0, Math.min(100, copyPercent)) * copies
  ) / (1 + copies)));
}

function sourceIntent(value: XmlHtmlFilterRequest, overrides: Partial<CreateJobRequest>): CreateJobRequest {
  const directions: InvoiceDirection[] = value.direction ? [value.direction] : ['purchase', 'sold'];
  return {
    connection_id: value.connectionId,
    date_from: value.dateFrom,
    date_to: value.dateTo,
    directions,
    query_types: [value.queryType],
    refresh_latest_month: true,
    ...overrides,
  };
}

export function useXmlHtmlDownloadLifecycle() {
  const jobs = useBatchJobLifecycle({ hydrateExisting: false });
  const [request, setRequest] = useState<XmlHtmlDownloadRequest | null>(null);
  const [syncRequest, setSyncRequest] = useState<XmlHtmlSyncRequest | null>(null);
  const [operation, setOperation] = useState<'sync' | 'download' | null>(null);
  const [phase, setPhase] = useState<ArtifactBatchPhase>('idle');
  const [syncRevision, setSyncRevision] = useState(0);
  const [copyProgress, setCopyProgress] = useState<RuntimeArtifactProgress>({ status: 'running', processed: 0, total: 0, percent: 0 });
  const [completedKeys, setCompletedKeys] = useState<Record<InvoiceArtifactKind, Set<string>>>({ xml: new Set(), html: new Set() });
  const [message, setMessage] = useState<string | null>(null);
  const [failedCount, setFailedCount] = useState(0);
  const exportStarted = useRef(false);
  const lastCopied = useRef(0);
  const activeRef = useRef(false);

  const connectionId = operation === 'sync' ? syncRequest?.connectionId : request?.connectionId;
  const item = connectionId ? jobs.items[connectionId] : undefined;
  const job = item?.status ?? item?.record;
  const sourcePercent = Number(job?.overall_percent ?? 0);
  const percent = phase === 'completed' || phase === 'failed'
    ? 100
    : phase === 'copy'
      ? phaseWeightedPercent(100, copyProgress.percent, request?.kinds.length ?? 1)
      : phase === 'source'
        ? phaseWeightedPercent(sourcePercent, 0, request?.kinds.length ?? 1)
        : sourcePercent;

  useEffect(() => window.miaRuntime?.artifacts?.onInvoiceProgress((progress) => {
    setCopyProgress(progress);
    if (progress.processed > lastCopied.current && progress.artifact_key && (progress.kind === 'xml' || progress.kind === 'html')) {
      const kind = progress.kind;
      setCompletedKeys((current) => ({ ...current, [kind]: new Set([...current[kind], progress.artifact_key!]) }));
    }
    lastCopied.current = Math.max(lastCopied.current, progress.processed);
  }), []);

  useEffect(() => {
    if (!job?.status) return;
    if (['failed', 'cancelled', 'abandoned'].includes(job.status)) {
      activeRef.current = false;
      setPhase('failed');
      setMessage(job.status === 'cancelled'
        ? operation === 'sync' ? 'Đã dừng đồng bộ dữ liệu.' : 'Đã dừng tải XML/HTML.'
        : operation === 'sync' ? 'Không thể đồng bộ dữ liệu hóa đơn.' : 'Không thể chuẩn bị dữ liệu XML/HTML từ nguồn.');
      return;
    }
    if (!['completed', 'completed_with_warning'].includes(job.status)) return;
    if (operation === 'sync') {
      activeRef.current = false;
      setPhase('completed');
      setSyncRevision((value) => value + 1);
      setMessage('Đã đồng bộ dữ liệu hóa đơn.');
      return;
    }
    if (operation !== 'download' || !request || exportStarted.current) return;
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
      activeRef.current = false;
      const expected = request.totalRows * request.kinds.length;
      const failed = Math.max(0, expected - result.count);
      setFailedCount(failed);
      setPhase('completed');
      setMessage(failed
        ? `Đã tải ${result.count} file XML/HTML; ${failed} artifact lỗi hoặc không khả dụng.`
        : `Đã tải ${result.count} file XML/HTML vào thư mục lưu trữ.`);
    }).catch((error: { code?: string; message?: string }) => {
      activeRef.current = false;
      setPhase('failed');
      setMessage(error.code === 'artifact_cancelled'
        ? 'Đã dừng tải XML/HTML.'
        : error.code === 'artifact_batch_empty' || error.message === 'artifact_batch_empty'
        ? 'Không tồn tại hóa đơn phù hợp để tải XML/HTML.'
        : 'Không thể ghi XML/HTML vào thư mục lưu trữ.');
    });
  }, [job?.status, operation, request]);

  const startSync = useCallback((value: XmlHtmlSyncRequest) => {
    if (activeRef.current || jobs.active) return false;
    activeRef.current = true;
    setOperation('sync');
    setSyncRequest(value);
    setMessage(null);
    setFailedCount(0);
    setPhase('sync');
    jobs.startMany([sourceIntent(value, { scopes: ['overview'], data_types: ['invoice'], force_refresh: value.forceRefresh })]);
    return true;
  }, [jobs.active, jobs.startMany]);

  const start = useCallback((value: XmlHtmlDownloadRequest) => {
    if (activeRef.current || jobs.active) return false;
    activeRef.current = true;
    exportStarted.current = false;
    lastCopied.current = 0;
    setCompletedKeys({ xml: new Set(), html: new Set() });
    setRequest(value);
    setOperation('download');
    setMessage(null);
    setFailedCount(0);
    setCopyProgress({ status: 'running', processed: 0, total: 0, percent: 0 });
    setPhase('source');
    jobs.startMany([sourceIntent(value, { scopes: ['overview', 'detail'], data_types: value.kinds })]);
    return true;
  }, [jobs.active, jobs.startMany]);

  const stop = useCallback(async () => {
    if (!['sync', 'source', 'copy'].includes(phase)) return;
    setPhase('stopping');
    if (phase === 'copy') await window.miaRuntime?.artifacts?.cancel();
    else await jobs.cancelAll();
  }, [jobs.cancelAll, phase]);

  const active = ['sync', 'source', 'copy', 'stopping'].includes(phase);
  return useMemo(() => ({
    active,
    syncActive: active && operation === 'sync',
    downloadActive: active && operation === 'download',
    canStop: phase === 'sync' || phase === 'source' || phase === 'copy',
    phase, percent, sourcePercent, request, syncRequest, job, copyProgress,
    completedKeys, failedCount, syncRevision, message, start, startSync, stop,
    clearMessage: () => setMessage(null),
  }), [active, completedKeys, copyProgress, failedCount, job, message, operation, percent, phase, request, sourcePercent, start, startSync, stop, syncRequest, syncRevision]);
}

export type XmlHtmlDownloadLifecycle = ReturnType<typeof useXmlHtmlDownloadLifecycle>;
