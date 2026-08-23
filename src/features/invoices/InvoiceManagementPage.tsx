import { useEffect, useRef, useState } from 'react';
import { NoticeDialog } from '../../components/NoticeDialog';
import { DateRangePicker } from '../../components/DateRangePicker';
import { StorageFolderPicker } from '../../components/StorageFolderPicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import { pageBounds, paginationTokens } from '../../components/pagination-utils';
import addIcon from '../../assets/figma/add.png';
import searchIcon from '../../assets/figma/search.png';
import syncIcon from '../../assets/figma/sync.png';
import checkIcon from '../../assets/figma/check.svg';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import type { ArtifactExportRequest } from '../../lib/runtime-bridge';
import { formatSourceJobProgress } from '../jobs/job-progress-presentation';
import { type BatchJobLifecycle } from '../jobs/use-batch-job-lifecycle';
import { resultExportErrorMessage } from '../results/result-export-errors';
import type { ResultExportLifecycle } from '../results/use-result-export-lifecycle';
import type { AccountConnection, InvoiceDirection, InvoiceSyncState } from '../../lib/api/contracts';
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
  actionsReady?: boolean;
  syncState?: InvoiceSyncState;
}

const DEFAULT_SYNC_RANGE = { dateFrom: '2023-10-01', dateTo: '2023-10-31' };
const ACCOUNT_PAGE_SIZE = 20;

const rows: InvoiceRow[] = [
  { taxCode: '0101234567', company: 'Công ty Cổ phần Công nghệ A', status: 'completed', selected: true, progress: 100, progressLabel: 'Đã tải xong', actionsReady: true },
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
  const year = Number(match[1]);
  const month = Number(match[2]);
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const monthEnd = `${match[1]}-${match[2]}-${String(lastDay).padStart(2, '0')}`;
  return selectedUntil && selectedUntil < monthEnd ? selectedUntil : monthEnd;
}

function SyncStatusCell({ state }: { state?: InvoiceSyncState }) {
  const label = !state || state.status === 'not_synced' ? 'Chưa đồng bộ'
    : state.status === 'queued' ? 'Chờ đồng bộ'
      : state.status === 'running' ? 'Đang đồng bộ'
        : state.status === 'failed' ? 'Đồng bộ lỗi'
          : state.status === 'cancelled' ? 'Đồng bộ bị hủy'
            : 'Đã đồng bộ';
  const processingUntil = state?.current_until ?? monthEndIso(state?.current_month);
  const processingLabel = dateLabel(processingUntil);
  const syncFromLabel = dateLabel(state?.sync_from);
  const syncUntilLabel = dateLabel(state?.sync_until);
  const detail = state?.status === 'running' && processingLabel
    ? `Đang xử lý đến ${processingLabel}`
    : state?.status === 'completed' && syncFromLabel && syncUntilLabel
      ? `Từ ${syncFromLabel} đến ${syncUntilLabel}`
      : null;
  return <div className="sync-state-cell" data-status={state?.status ?? 'not_synced'}><strong>{label}</strong>{detail ? <span>{detail}</span> : null}</div>;
}

function InvoiceCountCell({ state }: { state?: InvoiceSyncState }) {
  const format = (value: number) => new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 }).format(value);
  const current = state?.invoice_count ?? 0;
  const baseline = state?.baseline_invoice_count ?? current;
  const added = state?.added_invoice_count ?? 0;
  return <div className="invoice-count-cell">
    <strong className="invoice-count-current">{format(current)}</strong>
    <span className="invoice-count-added">(+{format(added)} mới)</span>
    <span className="invoice-count-baseline">{format(baseline)} cũ</span>
  </div>;
}

export function InvoiceManagementPage({ jobLifecycle, resultExports, onAddAccount, accounts, selectedAccountIds, exportFolder, onExportFolder, onDeleteAccount, onSelectAccount, onSelectAccounts, onViewResults }: {
  jobLifecycle: BatchJobLifecycle;
  resultExports: ResultExportLifecycle;
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
}) {
  const initialRange = useRef(readLastSyncDateRange() ?? DEFAULT_SYNC_RANGE).current;
  const [menu, setMenu] = useState<'scope' | 'direction' | 'sync' | null>(null);
  const [scopes, setScopes] = useState<Array<'overview' | 'detail'>>(['overview', 'detail']);
  const [direction, setDirection] = useState<InvoiceDirection>('purchase');
  const [activeBatchDirection, setActiveBatchDirection] = useState<InvoiceDirection | null>(null);
  const [syncStates, setSyncStates] = useState<Record<string, InvoiceSyncState>>({});
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<RowStatus | ''>('');
  const [dateFrom, setDateFrom] = useState(initialRange.dateFrom);
  const [dateTo, setDateTo] = useState(initialRange.dateTo);
  const [page, setPage] = useState(1);
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
        const values = await window.miaRuntime.jobs.syncStates(ids, direction);
        if (!disposed) setSyncStates(Object.fromEntries(values.map((value) => [value.connection_id, value])));
      } catch (error) {
        diagnosticLog('sync_states_refresh_failed', { code: (error as { code?: string })?.code }, 'warn');
      }
      if (!disposed && batchActive) timer = setTimeout(refresh, 1000);
    };
    void refresh();
    return () => { disposed = true; if (timer) clearTimeout(timer); };
  }, [accounts, batchActive, direction]);

  async function chooseExportFolder() {
    const folder = await window.miaRuntime?.artifacts?.selectDirectory();
    if (folder) onExportFolder(folder);
  }

  async function exportAllResults() {
    if (!selectedAccountIds.length) { setSelectionError('Vui lòng chọn ít nhất một tài khoản để tải kết quả.'); return; }
    if (!exportFolder.trim()) { setSelectionError('Vui lòng chọn thư mục lưu trữ trước khi tải kết quả.'); return; }
    if (resultExports.active) {
      setSelectionError(resultExports.owner === 'results'
        ? 'Đang tạo Excel trong tab Kết quả. Hãy chờ tác vụ đó hoàn tất.'
        : 'Đang tải kết quả tất cả. Hãy chờ tác vụ hiện tại hoàn tất.');
      return;
    }

    const resultScopes = scopes.map((scope) => scope === 'detail' ? 'details' as const : 'overview' as const);
    const resultDirection = direction;
    diagnosticLog('bulk_result_export_requested', {
      account_count: selectedAccountIds.length,
      date_from: dateFrom,
      date_to: dateTo,
      scopes: resultScopes,
      direction: resultDirection,
    });

    const requests: ArtifactExportRequest[] = selectedAccountIds.map((connection_id) => ({
      destination: exportFolder,
      connection_ids: [connection_id],
      kinds: ['excel'],
      result_scopes: resultScopes,
      date_from: dateFrom,
      date_to: dateTo,
      direction: resultDirection,
      search: '',
    }));

    try {
      const summary = await resultExports.run('bulk', requests);
      diagnosticLog('bulk_result_export_completed', {
        account_count: selectedAccountIds.length,
        file_count: summary.count,
        failed_count: summary.failures.length,
      });
      if (!summary.failures.length) {
        setSelectionError(`Đã xuất ${summary.count} file Excel cho ${selectedAccountIds.length} tài khoản.`);
      } else if (summary.count > 0) {
        setSelectionError(`Đã xuất ${summary.count} file Excel; ${summary.failures.length}/${selectedAccountIds.length} tài khoản không có hoặc không thể tạo kết quả.`);
      } else {
        setSelectionError(resultExportErrorMessage(summary.failures[0]?.error, dateFrom, dateTo, resultScopes));
      }
    } catch (error) {
      diagnosticLog('bulk_result_export_failed', { code: (error as { code?: string })?.code }, 'error');
      setSelectionError(resultExportErrorMessage(error, dateFrom, dateTo, resultScopes));
    }
  }

  function startJob(syncMode: 'new' | 'supplement') {
    if (batchActive) return;
    if (selectedAccountIds.length === 0) { setSelectionError('Vui lòng chọn ít nhất một tài khoản.'); return; }
    if (scopes.length === 0) {
      setSelectionError('Vui lòng chọn ít nhất một phạm vi dữ liệu trước khi đồng bộ.');
      return;
    }
    diagnosticLog('sync_clicked', {
      account_count: selectedAccountIds.length,
      date_from: dateFrom,
      date_to: dateTo,
      directions: [direction],
      scopes,
      sync_mode: syncMode,
    });
    setSelectionError(null);
    setActiveBatchDirection(direction);
    startMany(selectedAccountIds.map((connection_id) => ({
      connection_id,
      date_from: dateFrom,
      date_to: dateTo,
      directions: [direction],
      query_types: ['query', 'sco-query'],
      scopes,
      data_types: ['invoice'],
      force_refresh: syncMode === 'new',
      refresh_latest_month: false,
      sync_mode: syncMode,
    })));
    setMenu(null);
  }

  function toggleScope(value: 'overview' | 'detail') {
    setScopes((current) => current.includes(value)
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
          connection_id: account.connection_id, direction, status: transientSyncStatus, current_month: dateFrom.slice(0, 7), current_until: monthEndIso(dateFrom, dateTo),
          sync_from: persistedSyncState?.sync_from ?? null, sync_until: persistedSyncState?.sync_until ?? null,
          invoice_count: persistedSyncState?.invoice_count ?? 0,
          baseline_invoice_count: persistedSyncState?.invoice_count ?? 0, added_invoice_count: 0,
          last_job_id: null, sync_mode: null,
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
      actionsReady: Boolean(job?.job_id),
      syncState,
    };
  });
  const filteredRows = allRows.filter((row) => {
    const term = search.trim().toLocaleLowerCase('vi');
    return (!term || `${row.taxCode} ${row.company}`.toLocaleLowerCase('vi').includes(term)) && (!statusFilter || row.status === statusFilter);
  });
  const { currentPage, totalPages, start: firstRowIndex, end: lastRowIndex } = pageBounds(filteredRows.length, page, ACCOUNT_PAGE_SIZE);
  const pageRows = filteredRows.slice(firstRowIndex, lastRowIndex);
  const accountPageTokens = paginationTokens(totalPages, currentPage);
  const accountByTaxCode = new Map(accounts?.map((account) => [account.username, account]) ?? []);
  const filteredAccountIds = filteredRows.map((row) => accountByTaxCode.get(row.taxCode)?.connection_id).filter((id): id is string => Boolean(id));
  const selectedFilteredCount = filteredAccountIds.filter((id) => selectedAccountIds.includes(id)).length;
  const bulkExportWorking = resultExports.active && resultExports.owner === 'bulk';

  useEffect(() => {
    if (page !== currentPage) setPage(currentPage);
  }, [currentPage, page]);

  return (
    <div className="invoice-page">
      <section className="toolbar-canvas" aria-label="Thiết lập đồng bộ">
        <div className="toolbar-card">
          <DateRangePicker dateFrom={dateFrom} dateTo={dateTo} fromLabel="Từ ngày đồng bộ" toLabel="Đến ngày đồng bộ" onChange={(from, to) => { setDateFrom(from); setDateTo(to); }} />
          <div className="select-wrap">
            <button className="compact-select compact-select--direction" type="button" aria-expanded={menu === 'direction'} onClick={() => setMenu(menu === 'direction' ? null : 'direction')}>{direction === 'purchase' ? 'Mua vào' : 'Bán ra'} <i className="chevron" /></button>
            {menu === 'direction' ? <div className="figma-option-menu figma-direction-menu" aria-label="Loại giao dịch">
              <OptionCheck checked={direction === 'purchase'} label="Mua vào" onChange={() => setDirection('purchase')} />
              <OptionCheck checked={direction === 'sold'} label="Bán ra" onChange={() => setDirection('sold')} />
            </div> : null}
          </div>
          <div className="select-wrap">
            <button className="compact-select compact-select--detail" type="button" aria-expanded={menu === 'scope'} onClick={() => setMenu(menu === 'scope' ? null : 'scope')}>Chi tiết <i className="chevron" /></button>
            {menu === 'scope' ? <div className="figma-option-menu figma-scope-menu" data-node-id="4:654">
              <OptionCheck checked={scopes.includes('overview')} label="Tổng quan" onChange={() => toggleScope('overview')} />
              <OptionCheck checked={scopes.includes('detail')} label="Chi tiết" onChange={() => toggleScope('detail')} />
            </div> : null}
          </div>
          <div className="select-wrap sync-menu-wrap">
            <button className="sync-button" type="button" aria-label="Đồng bộ dữ liệu" aria-expanded={menu === 'sync'} disabled={batchActive} aria-busy={batchActive} onClick={() => setMenu(menu === 'sync' ? null : 'sync')}>
              <img src={syncIcon} alt="" /> {batchStopping ? 'Đang dừng…' : batchActive ? 'Đang đồng bộ…' : 'Đồng bộ dữ liệu'}
            </button>
            {menu === 'sync' ? <div className="sync-mode-menu" role="menu" aria-label="Chọn cách đồng bộ">
              <button type="button" role="menuitem" onClick={() => startJob('new')}><SyncNewIcon /><span><strong>Đồng bộ mới</strong><small>Tải lại toàn bộ dữ liệu trong khoảng thời gian đã chọn, kể cả các tháng đã đồng bộ trước đó.</small></span></button>
              <button type="button" role="menuitem" onClick={() => startJob('supplement')}><SyncSupplementIcon /><span><strong>Đồng bộ bổ sung</strong><small>Chỉ tải các tháng chưa đồng bộ và kiểm tra lại tháng trước, tháng hiện tại để cập nhật dữ liệu mới.</small></span></button>
            </div> : null}
          </div>
          <StorageFolderPicker
            className="invoice-export-folder"
            value={exportFolder}
            onChange={onExportFolder}
            onBrowse={chooseExportFolder}
            ariaLabel="Thư mục lưu trữ hóa đơn"
          />
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
                disabled={resultExports.active || selectedAccountIds.length === 0}
                aria-label={bulkExportWorking
                  ? `Tiến trình tải kết quả ${resultExports.accountIndex}/${resultExports.accountTotal}, ${Math.round(resultExports.percent)}%`
                  : 'Tải kết quả tất cả'}
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
                ) : <><DownloadIcon /><span>Tải kết quả tất cả</span></>}
              </button>
            </div>
            <button className="stop-button" type="button" disabled={!batchActive || batchStopping} onClick={() => void cancelAll()}><StopIcon /> {batchStopping ? 'Đang dừng…' : 'Dừng tải'}</button>
          </div>
        </div>
        <div className="data-card">
          <div className="table-header table-grid">
            <button className="selection-button" type="button" aria-label="Chọn tất cả tài khoản đã lọc" onClick={() => onSelectAccounts(selectedFilteredCount === filteredAccountIds.length ? selectedAccountIds.filter((id) => !filteredAccountIds.includes(id)) : [...new Set([...selectedAccountIds, ...filteredAccountIds])])}><SelectionBox checked={filteredAccountIds.length > 0 && selectedFilteredCount === filteredAccountIds.length} indeterminate={selectedFilteredCount > 0 && selectedFilteredCount < filteredAccountIds.length} /></button>
            <span>MST</span><span>Tên công ty</span><span>Trạng thái</span><span>Tiến trình</span><span>Trạng thái đồng bộ</span><span>Số lượng hóa đơn</span><span>Tác vụ</span>
          </div>
          <div className="table-body">
            {pageRows.map((row, index) => {
              const account = accountByTaxCode.get(row.taxCode);
              return <div className="table-row table-grid" data-status={row.status} key={account?.connection_id ?? `${row.taxCode}-${firstRowIndex + index}`}>
                {account ? <button className="selection-button" type="button" aria-label={`Chọn ${row.taxCode}`} onClick={() => onSelectAccount(account.connection_id)}><SelectionBox checked={row.selected} /></button> : <SelectionBox checked={row.selected} />}
                <span>{row.taxCode}</span>
                <strong className="company-name" title={row.company}>{row.company}</strong>
                <span className="status-badge" data-status={row.status}>{statusLabels[row.status]}</span>
                <ProgressCell row={row} />
                <SyncStatusCell state={row.syncState} />
                <InvoiceCountCell state={row.syncState} />
                {account ? <span className="row-action-group">
                  {row.actionsReady ? <button className="row-result-button" type="button" onClick={() => { diagnosticLog('results_opened', { connection_id: account.connection_id, date_from: dateFrom, date_to: dateTo }); onViewResults(account.connection_id, dateFrom, dateTo); }}>Xem kết quả</button> : <span className="row-action-placeholder">—</span>}
                  <button className="row-delete-button" type="button" aria-label={`Xóa ${row.taxCode}`} onClick={() => void onDeleteAccount(account.connection_id)}>×</button>
                </span> : <span className="row-action-placeholder">—</span>}
              </div>;
            })}
          </div>
        </div>
        <footer className="pagination">
          <span>{`Hiển thị ${filteredRows.length ? firstRowIndex + 1 : 0}–${lastRowIndex} trên tổng ${filteredRows.length} tài khoản`}</span>
          <div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>‹</button>{accountPageTokens.map((token, index) => token === 'ellipsis' ? <span key={`ellipsis-${index}`}>...</span> : <button type="button" key={token} data-active={currentPage === token} onClick={() => setPage(token)}>{token}</button>)}<button type="button" aria-label="Trang sau" disabled={currentPage === totalPages} onClick={() => setPage(currentPage + 1)}>›</button></div>
        </footer>
      </section>
      {batchMessage ? <NoticeDialog kind={batchMessage.kind} message={batchMessage.text} onClose={dismissMessage} /> : selectionError ? <NoticeDialog kind={selectionError.startsWith('Đã ') ? 'success' : 'notice'} message={selectionError} onClose={() => setSelectionError(null)} /> : null}
    </div>
  );
}

function DownloadIcon() {
  return <svg className="invoice-action-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v11m0 0 4-4m-4 4-4-4M5 17v3h14v-3" /></svg>;
}

function StopIcon() {
  return <svg className="stop-button-icon" viewBox="0 0 18 18" aria-hidden="true" focusable="false"><rect x="4" y="4" width="10" height="10" rx="1.5" /></svg>;
}

function SyncNewIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 18h10a4 4 0 0 0 .7-7.94A6 6 0 0 0 6.1 9.1 4.5 4.5 0 0 0 7 18Zm5-9v6m0 0 2.5-2.5M12 15l-2.5-2.5" /></svg>; }

function SyncSupplementIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 0 0-14.9-4M4 5v5h5m-5 3a8 8 0 0 0 14.9 4M20 19v-5h-5" /></svg>; }

function OptionCheck({ checked, label, disabled, title, onChange }: { checked: boolean; label: string; disabled?: boolean; title?: string; onChange?(): void }) {
  return <label title={title}><input className="option-input" type="checkbox" checked={checked} disabled={disabled} readOnly={!onChange} onChange={onChange} /><span className="option-box" data-checked={checked}>{checked ? <img src={checkIcon} alt="" /> : null}</span>{label}</label>;
}
