import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CreateJobRequest, InvoiceDirection, InvoiceQueryType } from '../../lib/api/contracts';
import type { ArtifactExportRequest, RuntimeArtifactProgress } from '../../lib/runtime-bridge';
import { useBatchJobLifecycle } from '../jobs/use-batch-job-lifecycle';

export type InvoiceArtifactKind = 'xml' | 'html';
export type InvoiceArtifactState = 'pending' | 'running' | 'completed' | 'failed';
export type InvoiceArtifactStates = Record<string, Record<InvoiceArtifactKind, InvoiceArtifactState>>;
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

export function actualArtifactPercent(
  sourceInvoicesTerminal: number, copied: number, invoiceTotal: number, kindCount: number,
) {
  const safeInvoices = Math.max(0, invoiceTotal);
  const total = safeInvoices + safeInvoices * Math.max(0, kindCount);
  if (!total) return 0;
  return Math.max(0, Math.min(99.9, (Math.max(0, sourceInvoicesTerminal) + Math.max(0, copied)) / total * 100));
}

export function completedArtifactKeys(states: InvoiceArtifactStates, targets: Iterable<string>) {
  const keys = [...targets];
  return {
    xml: new Set(keys.filter((key) => states[key]?.xml === 'completed')),
    html: new Set(keys.filter((key) => states[key]?.html === 'completed')),
  };
}

export function settleRunningArtifactStates(states: InvoiceArtifactStates, cancelled: boolean) {
  return Object.fromEntries(Object.entries(states).map(([key, item]) => [
    key,
    Object.fromEntries(Object.entries(item).map(([kind, state]) => [
      kind, state === 'running' ? (cancelled ? 'pending' : 'failed') : state,
    ])) as Record<InvoiceArtifactKind, InvoiceArtifactState>,
  ])) as InvoiceArtifactStates;
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
  const [sourceStates, setSourceStates] = useState<InvoiceArtifactStates>({});
  const [artifactStates, setArtifactStates] = useState<InvoiceArtifactStates>({});
  const [targetKeys, setTargetKeys] = useState(new Set<string>());
  const [message, setMessage] = useState<string | null>(null);
  const [failedCount, setFailedCount] = useState(0);
  const activeRef = useRef(false);
  const startGeneration = useRef(0);
  const sourceStarted = useRef(false);

  const connectionId = operation === 'sync' ? syncRequest?.connectionId : request?.connectionId;
  const item = connectionId ? jobs.items[connectionId] : undefined;
  const job = item?.status ?? item?.record;
  const sourcePercent = Number(job?.overall_percent ?? 0);
  const selectedKinds = request?.kinds ?? [];
  const sourceTerminal = [...targetKeys].filter((key) => selectedKinds.every(
    (kind) => ['completed', 'failed'].includes(sourceStates[key]?.[kind]),
  )).length;
  const percent = phase === 'completed'
    ? 100
    : actualArtifactPercent(
      sourceTerminal, 0, targetKeys.size, 0,
    );

  const completedKeys = useMemo(
    () => completedArtifactKeys(sourceStates, targetKeys),
    [sourceStates, targetKeys],
  );

  useEffect(() => window.miaRuntime?.artifacts?.onInvoiceProgress((progress) => {
    if (!activeRef.current) return;
    setPhase('source');
    if (!progress.artifact_key || (progress.kind !== 'xml' && progress.kind !== 'html')) return;
    const key = progress.artifact_key;
    const kind = progress.kind;
    const state = progress.status as InvoiceArtifactState;
    setSourceStates((current) => current[key]
      ? { ...current, [key]: { ...current[key], [kind]: state } }
      : current);
    setArtifactStates((current) => current[key]
      ? { ...current, [key]: { ...current[key], [kind]: state } }
      : current);
  }), []);

  useEffect(() => {
    if (operation !== 'sync' || !job?.status) return;
    if (['failed', 'cancelled', 'abandoned'].includes(job.status)) {
      activeRef.current = false;
      setArtifactStates((current) => settleRunningArtifactStates(current, job.status === 'cancelled'));
      setPhase('failed');
      setMessage(job.status === 'cancelled'
        ? 'Đã dừng đồng bộ dữ liệu.' : 'Không thể đồng bộ dữ liệu hóa đơn.');
      return;
    }
    if (!['completed', 'completed_with_warning'].includes(job.status)) return;
    activeRef.current = false;
    setPhase('completed');
    setSyncRevision((value) => value + 1);
    setMessage('Đã đồng bộ dữ liệu hóa đơn.');
  }, [job?.status, operation]);

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
    const token = ++startGeneration.current;
    sourceStarted.current = false;
    setSourceStates({});
    setArtifactStates({});
    setTargetKeys(new Set());
    setRequest(value);
    setOperation('download');
    setMessage(null);
    setFailedCount(0);
    setCopyProgress({ status: 'running', processed: 0, total: 0, percent: 0 });
    setPhase('source');
    const exportRequest: ArtifactExportRequest = {
      destination: value.destination, connection_ids: [value.connectionId], kinds: value.kinds,
      date_from: value.dateFrom, date_to: value.dateTo, direction: value.direction,
      query_type: value.queryType, search: value.search,
    };
    void window.miaRuntime!.artifacts.targets(exportRequest).then((snapshot) => {
      if (token !== startGeneration.current || !activeRef.current) return;
      if (!snapshot.total) {
        activeRef.current = false;
        setPhase('failed');
        setMessage('Không tồn tại hóa đơn phù hợp để tải XML/HTML.');
        return;
      }
      const keys = new Set(snapshot.keys);
      const pending = Object.fromEntries(snapshot.keys.map((key) => [key, { xml: 'pending', html: 'pending' }])) as InvoiceArtifactStates;
      setTargetKeys(keys);
      setSourceStates(pending);
      setArtifactStates(pending);
      setRequest({ ...value, totalRows: snapshot.total });
      sourceStarted.current = true;
      void window.miaRuntime!.artifacts.export(exportRequest).then((result) => {
        if (token !== startGeneration.current) return;
        activeRef.current = false;
        const expected = snapshot.total * value.kinds.length;
        const failed = Math.max(0, expected - result.count);
        setFailedCount(failed);
        setPhase('completed');
        setMessage(failed
          ? `Đã tải ${result.count} file XML/HTML; ${failed} artifact lỗi hoặc không khả dụng.`
          : `Đã tải ${result.count} file XML/HTML vào thư mục lưu trữ.`);
      }).catch((error: { code?: string; message?: string }) => {
        if (token !== startGeneration.current) return;
        activeRef.current = false;
        setArtifactStates((current) => settleRunningArtifactStates(current, error.code === 'artifact_cancelled'));
        setPhase('failed');
        setMessage(error.code === 'artifact_cancelled'
          ? 'Đã dừng tải XML/HTML.'
          : error.code === 'artifact_batch_empty' || error.message === 'artifact_batch_empty'
          ? 'Không tồn tại hóa đơn phù hợp để tải XML/HTML.'
          : 'Không thể tải hoặc ghi XML/HTML vào thư mục lưu trữ.');
      });
    }).catch(() => {
      if (token !== startGeneration.current) return;
      activeRef.current = false;
      setPhase('failed');
      setMessage('Không thể xác định danh sách hóa đơn cần tải.');
    });
    return true;
  }, [jobs.active]);

  const stop = useCallback(async () => {
    if (!['sync', 'source', 'copy'].includes(phase)) return;
    setPhase('stopping');
    if (operation === 'download' && sourceStarted.current) await window.miaRuntime?.artifacts?.cancel();
    else if (operation === 'download' && !sourceStarted.current) {
      startGeneration.current += 1;
      activeRef.current = false;
      setPhase('failed');
      setMessage('Đã dừng tải XML/HTML.');
    } else await jobs.cancelAll();
  }, [jobs.cancelAll, operation, phase]);

  const active = ['sync', 'source', 'copy', 'stopping'].includes(phase);
  return useMemo(() => ({
    active,
    syncActive: active && operation === 'sync',
    downloadActive: active && operation === 'download',
    canStop: phase === 'sync' || phase === 'source' || phase === 'copy',
    phase, percent, sourcePercent, request, syncRequest, job, copyProgress,
    artifactStates, targetKeys, completedKeys, failedCount, syncRevision, message, start, startSync, stop,
    clearMessage: () => setMessage(null),
  }), [active, artifactStates, completedKeys, copyProgress, failedCount, job, message, operation, percent, phase, request, sourcePercent, start, startSync, stop, syncRequest, syncRevision, targetKeys]);
}

export type XmlHtmlDownloadLifecycle = ReturnType<typeof useXmlHtmlDownloadLifecycle>;
