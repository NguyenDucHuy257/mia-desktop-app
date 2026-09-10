import { useCallback, useEffect, useRef, useState } from 'react';
import { NoticeDialog } from '../../components/NoticeDialog';
import { DateRangePicker } from '../../components/DateRangePicker';
import { StorageFolderPicker } from '../../components/StorageFolderPicker';
import { currentYearDateRange } from '../../components/date-input-utils';
import { pageBounds, paginationTokens } from '../../components/pagination-utils';
import addIcon from '../../assets/figma/add.png';
import searchIcon from '../../assets/figma/search.png';
import syncIcon from '../../assets/figma/sync.png';
import { OptionCheck } from '../../components/OptionCheck';
import { DownloadIcon, StopIcon } from '../../components/InvoiceActionIcons';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import type { ArtifactExportRequest } from '../../lib/runtime-bridge';
import { formatSourceJobProgress } from '../jobs/job-progress-presentation';
import { type BatchJobLifecycle } from '../jobs/use-batch-job-lifecycle';
import { resultExportErrorMessage } from '../results/result-export-errors';
import type { ResultExportLifecycle } from '../results/use-result-export-lifecycle';
import type { AccountConnection, InvoiceDirection, InvoiceSyncState } from '../../lib/api/contracts';
import { workspaceTaskConflictMessage, type WorkspaceTask } from '../../lib/workspace-task';
import '../../styles/invoice-refresh.css';

type RowStatus = 'completed' | 'failed' | 'processing' | 'pending' | 'stopped' | 'ready';

interface InvoiceRow {
  taxCode: string;
  company: string;
  status: RowStatus;
  selected: boolean;
  progress: number;
  progressLabel: string;
  failureHint?: string;
  syncState?: InvoiceSyncState;
}

const ACCOUNT_PAGE_SIZE = 20;

interface ResultExportSnapshot {
  connectionIds: string[];
  destination: string;
  dateFrom: string;
  dateTo: string;
  direction: InvoiceDirection;
  scopes: Array<'overview' | 'details'>;
}

const rows: InvoiceRow[] = [
  { taxCode: '0101234567', company: 'Công ty Cổ phần Công nghệ A', status: 'completed', selected: true, progress: 100, progressLabel: 'Đã tải xong' },
  { taxCode: '0309876543', company: 'Công ty TNHH Thương Mại Dịch Vụ B', status: 'failed', selected: true, progress: 0, progressLabel: 'Không thể đăng nhập Cổng HĐĐT', failureHint: 'Vui lòng kiểm tra lại MST hoặc mật khẩu.' },
  { taxCode: '0104567890', company: 'Công ty TNHH Sản xuất C', status: 'processing', selected: true, progress: 37, progressLabel: 'Chi tiết - Mua vào - 45/120 hóa đơn' },
  ...['E', 'G', 'H', 'Y', 'K', 'L', 'M'].map((letter) => ({
    taxCode: '0401122334',
    company: `Công ty CP Đầu tư ${letter}`,
    status: 'ready' as const,
    selected: false,
    progress: 0,
    progressLabel: 'Chưa đồng bộ',
  })),
];

const statusLabels: Record<RowStatus, string> = {
  completed: 'Hoàn thành',
  failed: 'ⓘ Lỗi',
  processing: 'ϟ Đang xử lý',
  pending: 'Chờ xử lý',
  stopped: 'Đã dừng',
  ready: 'Sẵn sàng',
};

function jobFailureHint(code?: string) {
  if (code === 'invalid_source_credentials') return 'Vui lòng kiểm tra lại MST hoặc mật khẩu.';
  if (code === 'source_account_locked') return 'Vui lòng mở khóa tài khoản trên Cổng HĐĐT trước khi thử lại.';
  if (code === 'source_rate_limited' || code?.startsWith('source_http_')) return 'Hãy chờ dịch vụ nguồn ổn định rồi thử lại.';
  return 'Hãy thử lại hoặc xem Nhật ký để biết thêm chi tiết.';
}

function clampProgress(value: number) {
  return Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
}

function SelectionBox({ checked, indeterminate = false }: { checked: boolean; indeterminate?: boolean }) {
  return <span className="selection-box" data-checked={checked || indeterminate}>{indeterminate ? '−' : checked ? '✓' : ''}</span>;
}

function ProgressCell({ row }: { row: InvoiceRow }) {
  if (row.status === 'failed') {
    return (
      <div className="failure-message">
        <strong>{row.progressLabel}</strong>
        <span>{row.failureHint ?? 'Hãy thử lại hoặc xem Nhật ký.'}</span>
      </div>
    );
  }

  const overallProgress = clampProgress(row.progress);

  return (
    <div className="progress-cell" data-status={row.status}>
      <div className="progress-section progress-section--overall">
        <div className="progress-copy">
          <span>{row.progressLabel}</span>
          <span>{Math.round(overallProgress)}%</span>
        </div>
        <div className="progress-track progress-track--overall"><span style={{ width: `${overallProgress}%` }} /></div>
      </div>
    </div>
  );
}

function dateLabel(value: string | null | undefined) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? '');
  return match ? `${match[3]}/${match[2]}/${match[1]}` : null;
}

function monthEndIso(value: string | null | undefined, selectedUntil?: string) {
  const match = /^(\d{4})-(\d{2})/.exec(value ?? '');
  if (!match) return null;
  const lastDay = new Date(Date.UTC(Number(match[1]), Number(match[2]), 0)).getUTCDate();
  const monthEnd = `${match[1]}-${match[2]}-${String(lastDay).padStart(2, '0')}`;
  return selectedUntil && selectedUntil < monthEnd ? selectedUntil : monthEnd;
}

function SyncStatusCell({ state }: { state?: InvoiceSyncState }) {
  const label = !state ? 'Chưa đồng bộ'
    : state.status === 'not_synced' && state.overview_ready ? 'Chưa đồng bộ đầy đủ'
    : state.status === 'not_synced' ? 'Chưa đồng bộ'
    : state.status === 'queued' ? 'Chờ đồng bộ'
      : state.status === 'running' && state.current_stage === 'detail' ? 'Đang đồng bộ Chi tiết'
      : state.status === 'running' ? 'Đang đồng bộ Tổng quan'
        : state.status === 'failed' && state.overview_ready ? 'Đồng bộ Chi tiết thất bại'
        : state.status === 'failed' ? 'Đồng bộ lỗi'
          : state.status === 'cancelled' ? 'Đồng bộ bị hủy'
            : 'Đã đồng bộ';
  const processingLabel = dateLabel(state?.current_until ?? monthEndIso(state?.current_month));
  const from = dateLabel(state?.sync_from);
  const until = dateLabel(state?.sync_until);
  const missingDetail = state?.missing_detail_ranges?.[0];
  const missingOverview = state?.missing_overview_ranges?.[0];
  const missing = missingOverview ?? missingDetail;
  const detail = state?.status === 'running' && processingLabel
    ? `Đang xử lý đến ${processingLabel}`
    : state?.status === 'completed' && from && until ? `Từ ${from} đến ${until}`
    : missing ? `Thiếu ${missingOverview ? 'Tổng quan' : 'Chi tiết'}: ${dateLabel(missing.date_from)} - ${dateLabel(missing.date_to)}` : null;
  return <div className="sync-state-cell" data-status={state?.status ?? 'not_synced'}><strong>{label}</strong>{detail ? <span>{detail}</span> : null}</div>;
}

const formatInvoiceCount = (value: number) => new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 }).format(value);

export function InvoiceManagementPage({ jobLifecycle, resultExports, activeWorkspaceTask, onAddAccount, accounts, selectedAccountIds, exportFolder, onExportFolder, onDeleteAccount, onSelectAccount, onSelectAccounts, onViewResults, initialDateFrom, initialDateTo, initialDirection = 'purchase', onDateRangeChange, onDirectionChange }: {
  jobLifecycle: BatchJobLifecycle;
  resultExports: ResultExportLifecycle;
  activeWorkspaceTask?: WorkspaceTask | null;
  onAddAccount(): void;
  connectionId: string;
  selectedAccountIds: string[];
  exportFolder: string;
  onExportFolder(value: string): void;
  accounts: AccountConnection[] | null;
  onDeleteAccount(id: string): Promise<void>;
  onSelectAccount(id: string): void;
  onSelectAccounts(ids: string[]): void;
  onViewResults(id: string, dateFrom: string, dateTo: string): void;
  initialDateFrom?: string;
  initialDateTo?: string;
  initialDirection?: InvoiceDirection;
  onDateRangeChange?(dateFrom: string, dateTo: string): void;
  onDirectionChange?(direction: InvoiceDirection): void;
}) {
  const initialRange = useRef(initialDateFrom && initialDateTo ? { dateFrom: initialDateFrom, dateTo: initialDateTo } : currentYearDateRange()).current;
  const [menu, setMenu] = useState<'scope' | 'direction' | 'sync' | null>(null);
  const [resultScopes, setResultScopes] = useState<Array<'overview' | 'detail'>>(['overview']);
  const [direction, setDirection] = useState<InvoiceDirection>(initialDirection);
  const [activeBatchDirection, setActiveBatchDirection] = useState<InvoiceDirection | null>(null);
  const [syncStates, setSyncStates] = useState<Record<string, InvoiceSyncState>>({});
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<RowStatus | ''>('');
  const [dateFrom, setDateFrom] = useState(initialRange.dateFrom);
  const [dateTo, setDateTo] = useState(initialRange.dateTo);
  const [page, setPage] = useState(1);
  const [accountPageSize, setAccountPageSize] = useState(ACCOUNT_PAGE_SIZE);
  const pendingAutoExport = useRef<ResultExportSnapshot | null>(null);
  const autoSyncObservedActive = useRef(false);
  const {
    items: batchItems,
    active: batchActive,
    stopping: batchStopping,
    startMany,
    cancelAll,
    message: batchMessage,
    dismissMessage,
  } = jobLifecycle;
  const figmaFixture = typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).get('figma') === '1';

  useEffect(() => {
    if (!menu) return;
    const closeOutside = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Element && !target.closest('.select-wrap')) setMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenu(null); };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [menu]);

  useEffect(() => {
    const ids = (accounts ?? []).map((account) => account.connection_id);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      if (!window.miaRuntime?.jobs?.syncStates || ids.length === 0) {
        if (!disposed) setSyncStates({});
        return;
      }
      try {
        const values = await window.miaRuntime.jobs.syncStates(ids, direction, dateFrom, dateTo);
        if (!disposed) setSyncStates(Object.fromEntries(values.map((value) => [value.connection_id, value])));
      } catch (error) {
        diagnosticLog('sync_states_refresh_failed', { code: (error as { code?: string })?.code }, 'warn');
      }
      if (!disposed && batchActive) timer = setTimeout(refresh, 1000);
    };
    void refresh();
    return () => { disposed = true; if (timer) clearTimeout(timer); };
  }, [accounts, batchActive, dateFrom, dateTo, direction]);

  async function chooseExportFolder() {
    const folder = await window.miaRuntime?.artifacts?.selectDirectory();
    if (folder) onExportFolder(folder);
  }

  const executeResultExport = useCallback(async (
    snapshot: ResultExportSnapshot, automatic = false,
  ) => {
    diagnosticLog('bulk_result_export_requested', {
      account_count: snapshot.connectionIds.length,
      date_from: snapshot.dateFrom,
      date_to: snapshot.dateTo,
      scopes: snapshot.scopes,
      direction: snapshot.direction,
      automatic_after_sync: automatic,
    });

    const requests: ArtifactExportRequest[] = snapshot.connectionIds.map((connection_id) => ({
      destination: snapshot.destination,
      connection_ids: [connection_id],
      kinds: ['excel'],
      result_scopes: snapshot.scopes,
      date_from: snapshot.dateFrom,
      date_to: snapshot.dateTo,
      direction: snapshot.direction,
      search: '',
    }));

    try {
      const summary = await resultExports.run('bulk', requests);
      diagnosticLog('bulk_result_export_completed', {
        account_count: snapshot.connectionIds.length,
        file_count: summary.count,
        failed_count: summary.failures.length,
        automatic_after_sync: automatic,
      });
      if (!summary.failures.length) {
        setSelectionError(`Đã xuất ${summary.count} file Excel cho ${snapshot.connectionIds.length} tài khoản.`);
      } else if (summary.count > 0) {
        setSelectionError(`Đã xuất ${summary.count} file Excel; ${summary.failures.length}/${snapshot.connectionIds.length} tài khoản không có hoặc không thể tạo kết quả.`);
      } else {
        setSelectionError(resultExportErrorMessage(
          summary.failures[0]?.error, snapshot.dateFrom, snapshot.dateTo, snapshot.scopes,
        ));
      }
    } catch (error) {
      diagnosticLog('bulk_result_export_failed', {
        code: (error as { code?: string })?.code,
        automatic_after_sync: automatic,
      }, 'error');
      setSelectionError(resultExportErrorMessage(
        error, snapshot.dateFrom, snapshot.dateTo, snapshot.scopes,
      ));
    }
  }, [resultExports]);

  async function exportAllResults() {
    const conflict = workspaceTaskConflictMessage(activeWorkspaceTask ?? null, 'result-export');
    if (conflict) { setSelectionError(conflict); return; }
    if (!selectedAccountIds.length) { setSelectionError('Vui lòng chọn ít nhất một tài khoản để tải kết quả.'); return; }
    if (!exportFolder.trim()) { setSelectionError('Vui lòng chọn thư mục lưu trữ trước khi tải kết quả.'); return; }
    if (!resultScopes.length) { setSelectionError('Vui lòng chọn ít nhất một loại bảng kê xuất Excel.'); return; }
    if (resultExports.active) {
      setSelectionError(resultExports.owner === 'results'
        ? 'Đang tạo Excel trong tab Kết quả. Hãy chờ tác vụ đó hoàn tất.'
        : 'Đang tải kết quả tất cả. Hãy chờ tác vụ hiện tại hoàn tất.');
      return;
    }
    await executeResultExport({
      connectionIds: [...selectedAccountIds],
      destination: exportFolder,
      dateFrom,
      dateTo,
      direction,
      scopes: resultScopes.map((scope) => scope === 'detail' ? 'details' : 'overview'),
    });
  }

  function startJob(syncMode: 'new' | 'supplement', downloadAfterSync = false) {
    if (batchActive) return;
    const conflict = workspaceTaskConflictMessage(activeWorkspaceTask ?? null, 'sync');
    if (conflict) { setSelectionError(conflict); setMenu(null); return; }
    if (selectedAccountIds.length === 0) { setSelectionError('Vui lòng chọn ít nhất một tài khoản.'); return; }
    if (downloadAfterSync && !exportFolder.trim()) {
      setSelectionError('Vui lòng chọn thư mục lưu trữ trước khi đồng bộ và tải kết quả.');
      return;
    }
    if (downloadAfterSync && resultScopes.length === 0) {
      setSelectionError('Vui lòng chọn ít nhất một loại bảng kê xuất Excel.');
      return;
    }
    // The data selector controls both synchronization and the optional Excel
    // export. Detail jobs still include Overview as a source prerequisite, but
    // an Overview-only selection must never be widened to Detail.
    const syncScopes: Array<'overview' | 'detail'> = resultScopes.includes('detail')
      ? ['overview', 'detail']
      : ['overview'];
    diagnosticLog('sync_clicked', {
      account_count: selectedAccountIds.length,
      date_from: dateFrom,
      date_to: dateTo,
      directions: [direction],
      scopes: syncScopes,
      sync_mode: syncMode,
    });
    setSelectionError(null);
    setActiveBatchDirection(direction);
    const started = startMany(selectedAccountIds.map((connection_id) => ({
      connection_id,
      date_from: dateFrom,
      date_to: dateTo,
      directions: [direction],
      query_types: ['query', 'sco-query'],
      scopes: syncScopes,
      data_types: ['invoice'],
      force_refresh: syncMode === 'new',
      refresh_latest_month: false,
      sync_mode: syncMode,
    })));
    if (started && downloadAfterSync) {
      pendingAutoExport.current = {
        connectionIds: [...selectedAccountIds],
        destination: exportFolder,
        dateFrom,
        dateTo,
        direction,
        scopes: resultScopes.map((scope) => scope === 'detail' ? 'details' : 'overview'),
      };
      autoSyncObservedActive.current = false;
      diagnosticLog('sync_auto_export_scheduled', {
        account_count: selectedAccountIds.length,
        date_from: dateFrom,
        date_to: dateTo,
        direction,
      });
    }
    setMenu(null);
  }

  useEffect(() => {
    const snapshot = pendingAutoExport.current;
    if (!snapshot) return;
    if (batchActive) {
      autoSyncObservedActive.current = true;
      return;
    }
    if (!autoSyncObservedActive.current) return;

    pendingAutoExport.current = null;
    autoSyncObservedActive.current = false;
    const unsuccessful = snapshot.connectionIds.some((connectionId) => {
      const item = batchItems[connectionId];
      const status = item?.status?.status ?? item?.record?.status;
      return Boolean(item?.error || item?.errorCode)
        || (status !== 'completed' && status !== 'completed_with_warning');
    });
    if (unsuccessful) {
      diagnosticLog('sync_auto_export_skipped', { reason: 'sync_not_completed' }, 'warn');
      setSelectionError('Đồng bộ chưa hoàn tất cho tất cả tài khoản nên MIA chưa tự tải kết quả.');
      return;
    }
    void executeResultExport(snapshot, true);
  }, [batchActive, batchItems, executeResultExport]);

  function toggleScope(value: 'overview' | 'detail') {
    setResultScopes((current) => current.includes(value)
      ? current.filter((item) => item !== value)
      : [...current, value]);
  }

  const allRows: InvoiceRow[] = figmaFixture ? rows : (accounts ?? []).map((account) => {
    const item = batchItems[account.connection_id];
    const job = item?.status ?? item?.record;
    const runtimeStatus = job?.status;
    const errorCode = job?.error?.code ?? item?.errorCode;
    const sourceError = job?.error?.message;
    const inlineError = item?.error;
    const phase = item?.phase;
    const accountNeedsAuth = ['auth_failed', 'suspended'].includes(account.status);
    const persistedSyncState = syncStates[account.connection_id];
    const transientSyncStatus = activeBatchDirection === direction
      ? inlineError ? 'failed' as const
        : phase === 'stopped' ? 'cancelled' as const
          : batchActive && (phase === 'queued' || phase === 'starting') ? 'running' as const
            : null
      : null;
    const syncState: InvoiceSyncState | undefined = transientSyncStatus
      ? {
          connection_id: account.connection_id,
          direction,
          status: transientSyncStatus,
          current_month: dateFrom.slice(0, 7),
          current_until: monthEndIso(dateFrom, dateTo),
          sync_from: persistedSyncState?.sync_from ?? null,
          sync_until: persistedSyncState?.sync_until ?? null,
          invoice_count: persistedSyncState?.invoice_count ?? 0,
          detail_invoice_count: persistedSyncState?.detail_invoice_count ?? 0,
          baseline_invoice_count: persistedSyncState?.invoice_count ?? 0,
          added_invoice_count: 0,
          replaced_old_count: null,
          downloaded_new_count: null,
          last_job_id: null,
          sync_mode: null,
        }
      : persistedSyncState;
    const status: RowStatus = phase === 'stopped'
      ? 'stopped'
      : inlineError || runtimeStatus === 'failed' || runtimeStatus === 'abandoned'
        ? 'failed'
        : phase === 'starting' || phase === 'stopping'
          ? 'processing'
          : phase === 'queued'
            ? 'pending'
            : runtimeStatus === 'running' || runtimeStatus === 'waiting_account' || runtimeStatus === 'cancelling'
              ? 'processing'
              : runtimeStatus === 'queued'
                ? 'pending'
                : runtimeStatus === 'cancelled'
                  ? 'stopped'
                  : runtimeStatus === 'completed' || runtimeStatus === 'completed_with_warning'
                  ? 'completed'
                    : accountNeedsAuth
                      ? 'failed'
                      : 'ready';

    const progress = Number(job?.overall_percent ?? 0);
    const progressLabel = phase === 'stopped' || runtimeStatus === 'cancelled'
      ? 'Đã dừng'
      : phase === 'stopping'
        ? 'Đang dừng…'
        : phase === 'starting'
          ? 'Đang tạo tác vụ đồng bộ…'
          : phase === 'queued'
            ? 'Chờ đến lượt xử lý…'
            : status === 'failed'
              ? inlineError ?? sourceError ?? errorCode ?? (accountNeedsAuth
                ? 'Cần xác thực lại tài khoản.'
                : 'Job xử lý thất bại.')
              : status === 'ready'
                ? 'Chưa đồng bộ'
                : formatSourceJobProgress(job);
    return {
      taxCode: account.username,
      company: account.company_name || '—',
      status,
      selected: selectedAccountIds.includes(account.connection_id),
      progress,
      progressLabel,
      failureHint: status === 'failed'
        ? accountNeedsAuth && !errorCode
          ? 'Vui lòng nhập lại MST và mật khẩu.'
          : jobFailureHint(errorCode)
        : undefined,
      syncState,
    };
  });
  const filteredRows = allRows.filter((row) => {
    const term = search.trim().toLocaleLowerCase('vi');
    return (!term || `${row.taxCode} ${row.company}`.toLocaleLowerCase('vi').includes(term)) && (!statusFilter || row.status === statusFilter);
  });
  const { currentPage, totalPages, start: firstRowIndex, end: lastRowIndex } = pageBounds(filteredRows.length, page, accountPageSize);
  const pageRows = filteredRows.slice(firstRowIndex, lastRowIndex);
  const accountPageTokens = paginationTokens(totalPages, currentPage);
  const accountByTaxCode = new Map(accounts?.map((account) => [account.username, account]) ?? []);
  const filteredAccountIds = filteredRows.map((row) => accountByTaxCode.get(row.taxCode)?.connection_id).filter((id): id is string => Boolean(id));
  const selectedFilteredCount = filteredAccountIds.filter((id) => selectedAccountIds.includes(id)).length;
  const bulkExportWorking = resultExports.active && resultExports.owner === 'bulk';

  function toggleAllFilteredAccounts() {
    const allFilteredSelected = filteredAccountIds.length > 0
      && selectedFilteredCount === filteredAccountIds.length;
    onSelectAccounts(allFilteredSelected
      ? selectedAccountIds.filter((id) => !filteredAccountIds.includes(id))
      : [...new Set([...selectedAccountIds, ...filteredAccountIds])]);
  }

  function moveAccountPage(offset: number) {
    setPage((current) => Math.max(1, Math.min(totalPages, current + offset)));
  }

  useEffect(() => {
    if (page !== currentPage) setPage(currentPage);
  }, [currentPage, page]);

  return (
    <div className="invoice-page">
      <section className="toolbar-canvas invoice-sync-toolbar-canvas" aria-label="Thiết lập đồng bộ">
        <div className="toolbar-card invoice-sync-toolbar-card">
          <div className="invoice-toolbar-field invoice-direction-field">
            <label>1. Loại hóa đơn</label>
            <div className="select-wrap">
              <button className="compact-select compact-select--direction" type="button" aria-expanded={menu === 'direction'} onClick={() => setMenu(menu === 'direction' ? null : 'direction')}><InvoiceDirectionIcon /> <span>{direction === 'purchase' ? 'Mua vào' : 'Bán ra'}</span> <i className="chevron" /></button>
              {menu === 'direction' ? <div className="figma-option-menu figma-direction-menu" aria-label="Loại giao dịch">
                <OptionCheck checked={direction === 'purchase'} label="Mua vào" onChange={() => { setDirection('purchase'); onDirectionChange?.('purchase'); setMenu(null); }} />
                <OptionCheck checked={direction === 'sold'} label="Bán ra" onChange={() => { setDirection('sold'); onDirectionChange?.('sold'); setMenu(null); }} />
              </div> : null}
            </div>
          </div>
          <div className="invoice-toolbar-field invoice-date-field">
            <label>2. Khoảng thời gian</label>
            <DateRangePicker dateFrom={dateFrom} dateTo={dateTo} fromLabel="Từ ngày đồng bộ" toLabel="Đến ngày đồng bộ" onChange={(from, to) => { setDateFrom(from); setDateTo(to); onDateRangeChange?.(from, to); }} />
          </div>
          <div className="invoice-toolbar-field invoice-scope-field">
            <label>3. Loại bảng kê xuất Excel</label>
            <div className="select-wrap">
              <button className="compact-select compact-select--detail" type="button" aria-expanded={menu === 'scope'} onClick={() => setMenu(menu === 'scope' ? null : 'scope')}><InvoiceScopeIcon /> <span>{resultScopes.length === 2 ? 'Tổng quan + Chi tiết' : resultScopes[0] === 'detail' ? 'Chi tiết' : 'Tổng quan'}</span> <i className="chevron" /></button>
              {menu === 'scope' ? <div className="figma-option-menu figma-scope-menu" data-node-id="4:654">
                <OptionCheck checked={resultScopes.includes('overview')} label="Tổng quan" onChange={() => toggleScope('overview')} />
                <OptionCheck checked={resultScopes.includes('detail')} label="Chi tiết" onChange={() => toggleScope('detail')} />
              </div> : null}
            </div>
          </div>
          <div className="invoice-toolbar-storage">
            <StorageFolderPicker
              className="invoice-export-folder"
              value={exportFolder}
              onChange={onExportFolder}
              onBrowse={chooseExportFolder}
              ariaLabel="Thư mục lưu trữ hóa đơn"
            />
          </div>
          <div className="invoice-toolbar-actions">
            <button
              className="sync-button invoice-sync-export-button"
              type="button"
              disabled={batchActive || resultExports.active || selectedAccountIds.length === 0}
              onClick={() => startJob('supplement', true)}
              title="Đồng bộ bổ sung, sau đó tự tải các bảng kê đã chọn"
            >
              <DownloadIcon /> Đồng bộ &amp; tải xuống
            </button>
            <div className="select-wrap sync-menu-wrap">
              <button className="sync-button" type="button" aria-label="Đồng bộ dữ liệu" aria-expanded={menu === 'sync'} disabled={batchActive} aria-busy={batchActive} onClick={() => setMenu(menu === 'sync' ? null : 'sync')}>
                <img src={syncIcon} alt="" /> {batchStopping ? 'Đang dừng…' : batchActive ? 'Đang đồng bộ…' : 'Đồng bộ dữ liệu'}
              </button>
              {menu === 'sync' ? <div className="sync-mode-menu" role="menu" aria-label="Chọn cách đồng bộ">
                <button type="button" role="menuitem" onClick={() => startJob('new')}><SyncNewIcon /><span><strong>Đồng bộ mới</strong><small>Tải lại toàn bộ dữ liệu trong khoảng thời gian đã chọn, kể cả các tháng đã đồng bộ trước đó.</small></span></button>
                <button type="button" role="menuitem" onClick={() => startJob('supplement')}><SyncSupplementIcon /><span><strong>Đồng bộ bổ sung</strong><small>Chỉ tải các tháng chưa đồng bộ và kiểm tra lại tháng trước, tháng hiện tại để cập nhật dữ liệu mới.</small></span></button>
              </div> : null}
            </div>
          </div>
        </div>
      </section>
      <section className="invoice-content">
        <div className="filters">
          <div className="filters-left">
            <button className="add-account" type="button" onClick={onAddAccount}><img src={addIcon} alt="" /> Thêm tài khoản</button>
            <label className="search-box">
              <img src={searchIcon} alt="" />
              <input aria-label="Tìm kiếm tài khoản" placeholder="Tìm kiếm MST, Tên công ty..." value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
            </label>
            <select className="status-filter" aria-label="Lọc trạng thái" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value as RowStatus | ''); setPage(1); }}><option value="">Tất cả trạng thái</option><option value="completed">Hoàn thành</option><option value="processing">Đang xử lý</option><option value="failed">Lỗi</option><option value="pending">Chờ xử lý</option><option value="stopped">Đã dừng</option><option value="ready">Sẵn sàng</option></select>
          </div>
          <div className="invoice-filter-actions">
            <div className="invoice-export-all-wrap">
              <button
                className="invoice-export-all-button"
                type="button"
                data-exporting={bulkExportWorking}
                disabled={bulkExportWorking || selectedAccountIds.length === 0}
                aria-label={bulkExportWorking
                  ? `Tiến trình tải kết quả ${resultExports.accountIndex}/${resultExports.accountTotal}, ${Math.round(resultExports.percent)}%`
                  : 'Tải xuống kết quả'}
                aria-valuemin={bulkExportWorking ? 0 : undefined}
                aria-valuemax={bulkExportWorking ? 100 : undefined}
                aria-valuenow={bulkExportWorking ? Math.round(resultExports.percent) : undefined}
                onClick={() => void exportAllResults()}
              >
                {bulkExportWorking ? (
                  <>
                    <span className="invoice-export-button-fill" style={{ width: `${Math.max(0, Math.min(100, resultExports.percent))}%` }} />
                    <span className="invoice-export-button-progress">
                      <strong>{resultExports.accountIndex}/{resultExports.accountTotal}</strong>
                      <strong>{Math.round(resultExports.percent)}%</strong>
                    </span>
                  </>
                ) : <><DownloadIcon /><span>Tải xuống kết quả</span></>}
              </button>
            </div>
            <button className="stop-button" type="button" disabled={!batchActive || batchStopping} onClick={() => void cancelAll()}><StopIcon /> {batchStopping ? 'Đang dừng…' : 'Dừng tải'}</button>
          </div>
        </div>
        <div className="data-card">
          <div className="table-header table-grid">
            <button className="selection-button" type="button" aria-label="Chọn tất cả tài khoản trên mọi trang" onClick={toggleAllFilteredAccounts}><SelectionBox checked={filteredAccountIds.length > 0 && selectedFilteredCount === filteredAccountIds.length} indeterminate={selectedFilteredCount > 0 && selectedFilteredCount < filteredAccountIds.length} /></button>
            <span>MST</span><span>Tên công ty</span><span>Trạng thái</span><span className="artifact-quantity-header invoice-count-header"><strong>Số lượng</strong><span><b>Tổng quan</b><b>Chi tiết</b></span></span><span>Tiến trình</span><span>Trạng thái đồng bộ</span><span>Tác vụ</span>
          </div>
          <div className="table-body">
            {pageRows.map((row, index) => {
              const account = accountByTaxCode.get(row.taxCode);
              return <div className="table-row table-grid" data-status={row.status} key={account?.connection_id ?? `${row.taxCode}-${firstRowIndex + index}`}>
                {account ? <button className="selection-button" type="button" aria-label={`Chọn ${row.taxCode}`} onClick={() => onSelectAccount(account.connection_id)}><SelectionBox checked={row.selected} /></button> : <SelectionBox checked={row.selected} />}
                <span>{row.taxCode}</span>
                <strong className="company-name" title={row.company}>{row.company}</strong>
                <span className="status-badge" data-status={row.status}>{statusLabels[row.status]}</span>
                <span className="invoice-count-value invoice-count-value--overview">{formatInvoiceCount(row.syncState?.invoice_count ?? 0)}</span>
                <span className="invoice-count-value invoice-count-value--detail">{formatInvoiceCount(row.syncState?.detail_invoice_count ?? 0)}</span>
                <ProgressCell row={row} />
                <SyncStatusCell state={row.syncState} />
                {account ? <span className="row-action-group">
                  <button className="row-result-button" type="button" onClick={() => {
                    const resultDateFrom = dateFrom;
                    const resultDateTo = dateTo;
                    diagnosticLog('results_opened', { connection_id: account.connection_id, date_from: resultDateFrom, date_to: resultDateTo });
                    onViewResults(account.connection_id, resultDateFrom, resultDateTo);
                  }}>Xem kết quả</button>
                  <button className="row-delete-button" type="button" aria-label={`Xóa ${row.taxCode}`} onClick={() => void onDeleteAccount(account.connection_id)}>×</button>
                </span> : <span className="row-action-placeholder">—</span>}
              </div>;
            })}
          </div>
        </div>
        <footer className="pagination">
          <label>Số tài khoản/trang: <select aria-label="Số tài khoản mỗi trang" value={accountPageSize} onChange={(event) => { setAccountPageSize(Number(event.target.value)); setPage(1); }}><option value={10}>10</option><option value={20}>20</option><option value={50}>50</option></select></label>
          <span>{`Hiển thị ${filteredRows.length ? firstRowIndex + 1 : 0}–${lastRowIndex} trên tổng ${filteredRows.length} tài khoản`}</span>
          <div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={currentPage === 1} onClick={() => moveAccountPage(-1)}>‹</button>{accountPageTokens.map((token, index) => token === 'ellipsis' ? <span key={`ellipsis-${index}`}>...</span> : <button type="button" key={token} data-active={currentPage === token} onClick={() => setPage(token)}>{token}</button>)}<button type="button" aria-label="Trang sau" disabled={currentPage === totalPages} onClick={() => moveAccountPage(1)}>›</button></div>
        </footer>
      </section>
      {batchMessage ? <NoticeDialog kind={batchMessage.kind} message={batchMessage.text} onClose={dismissMessage} /> : selectionError ? <NoticeDialog kind={selectionError.startsWith('Đã ') ? 'success' : 'notice'} message={selectionError} onClose={() => setSelectionError(null)} /> : null}
    </div>
  );
}

function SyncNewIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 18h10a4 4 0 0 0 .7-7.94A6 6 0 0 0 6.1 9.1 4.5 4.5 0 0 0 7 18Zm5-9v6m0 0 2.5-2.5M12 15l-2.5-2.5" /></svg>; }

function SyncSupplementIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.9-4M4 5v5h5m-5 3a8 8 0 0 0 14.9 4M20 19v-5h-5" /></svg>; }

function InvoiceDirectionIcon() { return <svg className="invoice-toolbar-control-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h9l3 3v14H6v-17Zm9 0v4h3M9 11h6M9 15h6" /></svg>; }

function InvoiceScopeIcon() { return <svg className="invoice-toolbar-control-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v14H5V5Zm4 0v14M9 10h10M9 14h10" /></svg>; }
