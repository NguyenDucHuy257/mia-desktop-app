import { useEffect, useRef, useState } from 'react';
import { NoticeDialog } from '../../components/NoticeDialog';
import { DateRangePicker } from '../../components/DateRangePicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import addIcon from '../../assets/figma/add.png';
import searchIcon from '../../assets/figma/search.png';
import stopIcon from '../../assets/figma/stop.png';
import syncIcon from '../../assets/figma/sync.png';
import checkIcon from '../../assets/figma/check.svg';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import { jobFailureMessage, type BatchJobLifecycle } from '../jobs/use-batch-job-lifecycle';
import type { AccountConnection, InvoiceDirection } from '../../lib/api/contracts';
import '../../styles/invoice-refresh.css';

type RowStatus = 'completed' | 'failed' | 'processing' | 'pending' | 'ready';

interface InvoiceRow {
  taxCode: string;
  company: string;
  status: RowStatus;
  selected: boolean;
  progress: number;
  progressLabel: string;
  failureHint?: string;
  actionsReady?: boolean;
}

const DEFAULT_SYNC_RANGE = { dateFrom: '2023-10-01', dateTo: '2023-10-31' };

const rows: InvoiceRow[] = [
  { taxCode: '0101234567', company: 'Công ty Cổ phần Công nghệ A', status: 'completed', selected: true, progress: 100, progressLabel: 'Đã tải xong', actionsReady: true },
  { taxCode: '0309876543', company: 'Công ty TNHH Thương Mại Dịch Vụ B', status: 'failed', selected: true, progress: 0, progressLabel: 'Không thể đăng nhập Cổng HĐĐT', failureHint: 'Vui lòng kiểm tra lại MST hoặc mật khẩu.' },
  { taxCode: '0104567890', company: 'Công ty TNHH Sản xuất C', status: 'processing', selected: true, progress: 37, progressLabel: 'Tháng 08/2026: 45/120 HĐ' },
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
  failed: 'ⓘ Thất bại',
  processing: 'ϟ Đang xử lý',
  pending: 'Chờ xử lý',
  ready: 'Sẵn sàng',
};

function jobFailureHint(code?: string) {
  if (code === 'invalid_source_credentials') return 'Vui lòng kiểm tra lại MST hoặc mật khẩu.';
  if (code === 'source_account_locked') return 'Vui lòng mở khóa tài khoản trên Cổng HĐĐT trước khi thử lại.';
  if (code === 'source_rate_limited' || code?.startsWith('source_http_')) return 'Hãy chờ dịch vụ nguồn ổn định rồi thử lại.';
  return 'Hãy thử lại hoặc xem Nhật ký để biết thêm chi tiết.';
}

function formatMonthKey(value?: string | null) {
  const match = String(value ?? '').match(/^(\d{4})-(\d{2})$/);
  return match ? `${match[2]}/${match[1]}` : value || '—';
}

function stageLabel(stage?: string | null) {
  if (stage === 'auth') return 'Đang đăng nhập Cổng HĐĐT…';
  if (stage === 'overview') return 'Đang chuẩn bị dữ liệu hóa đơn…';
  if (stage === 'detail') return 'Đang tải chi tiết hóa đơn…';
  if (stage === 'ensure_xml') return 'Đang tạo dữ liệu XML…';
  if (stage === 'finalize') return 'Đang hoàn tất dữ liệu…';
  return 'Đang xử lý…';
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
  return (
    <div className="progress-cell" data-status={row.status}>
      <div className="progress-copy"><span>{row.progressLabel}</span><span>{Math.round(row.progress)}%</span></div>
      <div className="progress-track"><span style={{ width: `${Math.max(0, Math.min(100, row.progress))}%` }} /></div>
    </div>
  );
}

export function InvoiceManagementPage({ jobLifecycle, onAddAccount, accounts, selectedAccountIds, exportFolder, onExportFolder, onDeleteAccount, onSelectAccount, onSelectAccounts, onViewResults }: {
  jobLifecycle: BatchJobLifecycle;
  onAddAccount(): void;
  connectionId: string;
  selectedAccountIds: string[];
  exportFolder: string;
  onExportFolder(value: string): void;
  accounts: AccountConnection[] | null;
  onDeleteAccount(id: string): Promise<void>;
  onSelectAccount(id: string): void;
  onSelectAccounts(ids: string[]): void;
  onViewResults(id: string): void;
}) {
  const initialRange = useRef(readLastSyncDateRange() ?? DEFAULT_SYNC_RANGE).current;
  const [menu, setMenu] = useState<'scope' | 'direction' | null>(null);
  const [scopes, setScopes] = useState<Array<'overview' | 'detail'>>(['overview', 'detail']);
  const [directions, setDirections] = useState<InvoiceDirection[]>(['purchase', 'sold']);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<RowStatus | ''>('');
  const [dateFrom, setDateFrom] = useState(initialRange.dateFrom);
  const [dateTo, setDateTo] = useState(initialRange.dateTo);
  const [page, setPage] = useState(1);
  const { items: batchItems, startMany, cancelAll, message, dismissMessage } = jobLifecycle;
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

  async function exportAccounts(connectionIds: string[]) {
    if (!window.miaRuntime?.artifacts?.export) { setSelectionError('Tính năng xuất file chỉ có trong ứng dụng desktop.'); return; }
    if (connectionIds.length !== 1) { setSelectionError('Chỉ tải Excel cho một tài khoản tại một thời điểm.'); return; }
    let destination = exportFolder;
    if (!destination) {
      destination = await window.miaRuntime.artifacts.selectDirectory() ?? '';
      if (!destination) return;
      onExportFolder(destination);
    }
    const resultScopes = scopes.map((scope) => scope === 'detail' ? 'details' as const : 'overview' as const);
    const resultDirection = directions.length === 1 ? directions[0] : null;
    diagnosticLog('account_excel_export_requested', {
      date_from: dateFrom,
      date_to: dateTo,
      scopes: resultScopes,
      direction: resultDirection,
    });
    try {
      const result = await window.miaRuntime.artifacts.export({
        destination,
        connection_ids: connectionIds,
        kinds: ['excel'],
        result_scopes: resultScopes,
        date_from: dateFrom,
        date_to: dateTo,
        direction: resultDirection,
        search: '',
      });
      diagnosticLog('account_excel_export_completed', { file_count: result.count });
      setSelectionError(`Đã xuất ${result.count} file Excel.`);
    } catch (error) {
      diagnosticLog('account_excel_export_failed', { code: (error as { code?: string })?.code }, 'error');
      setSelectionError('Không thể xuất Excel. Vui lòng kiểm tra thư mục lưu và thử lại.');
    }
  }

  function startJob() {
    if (selectedAccountIds.length === 0) { setSelectionError('Vui lòng chọn ít nhất một tài khoản.'); return; }
    if (directions.length === 0 || scopes.length === 0) {
      setSelectionError('Vui lòng chọn ít nhất một hướng và phạm vi dữ liệu trước khi đồng bộ.');
      return;
    }
    diagnosticLog('sync_clicked', {
      account_count: selectedAccountIds.length,
      date_from: dateFrom,
      date_to: dateTo,
      directions,
      scopes,
      force_refresh: forceRefresh,
    });
    setSelectionError(null);
    startMany(selectedAccountIds.map((connection_id) => ({
      connection_id, date_from: dateFrom, date_to: dateTo,
      directions, query_types: ['query', 'sco-query'], scopes, data_types: ['invoice'],
      force_refresh: forceRefresh,
    })));
  }

  function toggleDirection(value: InvoiceDirection) {
    setDirections((current) => current.includes(value)
      ? current.filter((item) => item !== value)
      : [...current, value]);
  }

  function toggleScope(value: 'overview' | 'detail') {
    setScopes((current) => current.includes(value)
      ? current.filter((item) => item !== value)
      : [...current, value]);
  }

  const terminal = new Set(['completed', 'completed_with_warning', 'failed', 'cancelled', 'abandoned']);
  const activeJob = Object.values(batchItems).some((item) => {
    const status = item.status?.status ?? item.record?.status;
    return Boolean(item.record?.job_id && status && !terminal.has(status));
  });
  const allRows: InvoiceRow[] = figmaFixture ? rows : (accounts ?? []).map((account) => {
    const item = batchItems[account.connection_id];
    const job = item?.status ?? item?.record;
    const runtimeStatus = job?.status;
    const currentMonth = job?.current_month;
    const errorCode = job?.error?.code;
    const status: RowStatus = runtimeStatus === 'failed' || runtimeStatus === 'abandoned'
      ? 'failed'
      : runtimeStatus === 'running' || runtimeStatus === 'waiting_account' || runtimeStatus === 'cancelling'
        ? 'processing'
        : runtimeStatus === 'queued'
          ? 'pending'
          : runtimeStatus === 'completed' || runtimeStatus === 'completed_with_warning'
            ? 'completed'
            : 'ready';
    const monthPercent = currentMonth ? Number(currentMonth.percent) : null;
    const progress = status === 'completed'
      ? 100
      : status === 'processing' && monthPercent !== null && Number.isFinite(monthPercent)
        ? monthPercent
        : Number(job?.overall_percent ?? 0);
    const progressLabel = status === 'failed'
      ? jobFailureMessage(errorCode)
      : status === 'completed'
        ? 'Đã tải xong'
        : status === 'pending'
          ? 'Chờ trong hàng đợi…'
          : status === 'ready'
            ? 'Chưa đồng bộ'
            : currentMonth
              ? `Tháng ${formatMonthKey(currentMonth.key)}: ${currentMonth.processed}/${currentMonth.planned ?? '…'} HĐ`
              : stageLabel(job?.stage);
    return {
      taxCode: account.username,
      company: account.company_name || '—',
      status,
      selected: selectedAccountIds.includes(account.connection_id),
      progress,
      progressLabel,
      failureHint: status === 'failed' ? jobFailureHint(errorCode) : undefined,
      actionsReady: runtimeStatus === 'completed' || runtimeStatus === 'completed_with_warning',
    };
  });
  const visibleRows = allRows.filter((row) => {
    const term = search.trim().toLocaleLowerCase('vi');
    return (!term || `${row.taxCode} ${row.company}`.toLocaleLowerCase('vi').includes(term)) && (!statusFilter || row.status === statusFilter);
  });
  const accountByTaxCode = new Map(accounts?.map((account) => [account.username, account]) ?? []);
  const visibleAccountIds = visibleRows.map((row) => accountByTaxCode.get(row.taxCode)?.connection_id).filter((id): id is string => Boolean(id));
  const selectedVisibleCount = visibleAccountIds.filter((id) => selectedAccountIds.includes(id)).length;

  return (
    <div className="invoice-page">
      <section className="toolbar-canvas" aria-label="Thiết lập đồng bộ">
        <div className="toolbar-card">
          <DateRangePicker dateFrom={dateFrom} dateTo={dateTo} fromLabel="Từ ngày đồng bộ" toLabel="Đến ngày đồng bộ" onChange={(from, to) => { setDateFrom(from); setDateTo(to); }} />
          <div className="select-wrap">
            <button className="compact-select compact-select--direction" type="button" aria-expanded={menu === 'direction'} onClick={() => setMenu(menu === 'direction' ? null : 'direction')}>Mua vào <i className="chevron" /></button>
            {menu === 'direction' ? <div className="figma-option-menu figma-direction-menu" aria-label="Loại giao dịch">
              <OptionCheck checked={directions.includes('purchase')} label="Mua vào" onChange={() => toggleDirection('purchase')} />
              <OptionCheck checked={directions.includes('sold')} label="Bán ra" onChange={() => toggleDirection('sold')} />
            </div> : null}
          </div>
          <div className="select-wrap">
            <button className="compact-select compact-select--detail" type="button" aria-expanded={menu === 'scope'} onClick={() => setMenu(menu === 'scope' ? null : 'scope')}>Chi tiết <i className="chevron" /></button>
            {menu === 'scope' ? <div className="figma-option-menu figma-scope-menu" data-node-id="4:654">
              <OptionCheck checked={scopes.includes('overview')} label="Tổng quan" onChange={() => toggleScope('overview')} />
              <OptionCheck checked={scopes.includes('detail')} label="Chi tiết" onChange={() => toggleScope('detail')} />
            </div> : null}
          </div>
          <label className="refresh-data-toggle" title="Tích để tải mới toàn bộ khoảng đã chọn. Nếu không tích, dữ liệu lịch sử hợp lệ được dùng lại; tháng hiện tại và tháng trước luôn được tải mới.">
            <input type="checkbox" checked={forceRefresh} onChange={(event) => setForceRefresh(event.target.checked)} aria-label="Tải mới dữ liệu" />
            <span className="refresh-data-box" data-checked={forceRefresh}>{forceRefresh ? <img src={checkIcon} alt="" /> : null}</span>
            <span>Tải mới dữ liệu</span>
          </label>
          <button className="sync-button" type="button" aria-label="Đồng bộ dữ liệu" onClick={startJob}><img src={syncIcon} alt="" /> Đồng bộ dữ liệu</button>
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
            <select className="status-filter" aria-label="Lọc trạng thái" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value as RowStatus | ''); setPage(1); }}><option value="">Tất cả trạng thái</option><option value="completed">Hoàn thành</option><option value="processing">Đang xử lý</option><option value="failed">Thất bại</option><option value="pending">Chờ xử lý</option><option value="ready">Sẵn sàng</option></select>
          </div>
          <button className="stop-button" type="button" disabled={!activeJob} onClick={() => void cancelAll()}><img src={stopIcon} alt="" /> Dừng tải</button>
        </div>
        <div className="data-card">
          <div className="table-header table-grid">
            <button className="selection-button" type="button" aria-label="Chọn tất cả tài khoản" onClick={() => onSelectAccounts(selectedVisibleCount === visibleAccountIds.length ? selectedAccountIds.filter((id) => !visibleAccountIds.includes(id)) : [...new Set([...selectedAccountIds, ...visibleAccountIds])])}><SelectionBox checked={visibleAccountIds.length > 0 && selectedVisibleCount === visibleAccountIds.length} indeterminate={selectedVisibleCount > 0 && selectedVisibleCount < visibleAccountIds.length} /></button>
            <span>MST</span><span>Tên công ty</span><span>Trạng thái</span><span>Tiến trình</span><span>Tác vụ</span>
          </div>
          <div className="table-body">
            {visibleRows.map((row, index) => {
              const account = accountByTaxCode.get(row.taxCode);
              return <div className="table-row table-grid" data-status={row.status} key={`${row.taxCode}-${index}`}>
                {account ? <button className="selection-button" type="button" aria-label={`Chọn ${row.taxCode}`} onClick={() => onSelectAccount(account.connection_id)}><SelectionBox checked={row.selected} /></button> : <SelectionBox checked={row.selected} />}
                <span>{row.taxCode}</span>
                <strong className="company-name">{row.company}</strong>
                <span className="status-badge" data-status={row.status}>{statusLabels[row.status]}</span>
                <ProgressCell row={row} />
                {account ? <span className="row-action-group">
                  {row.actionsReady ? <>
                    <button className="row-result-button" type="button" onClick={() => { diagnosticLog('results_opened', { connection_id: account.connection_id }); onViewResults(account.connection_id); }}>Xem kết quả</button>
                    <button className="row-excel-button" type="button" onClick={() => void exportAccounts([account.connection_id])}>Tải Excel</button>
                  </> : <span className="row-action-placeholder">—</span>}
                  <button className="row-delete-button" type="button" aria-label={`Xóa ${row.taxCode}`} onClick={() => void onDeleteAccount(account.connection_id)}>×</button>
                </span> : <span className="row-action-placeholder">—</span>}
              </div>;
            })}
          </div>
        </div>
        <footer className="pagination">
          <span>{accounts ? `Hiển thị ${visibleRows.length} tài khoản` : `Hiển thị trang ${page} trong tổng số 128 hóa đơn`}</span>
          <div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={page === 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>‹</button>{[1, 2, 3].map((value) => <button type="button" key={value} data-active={page === value} onClick={() => setPage(value)}>{value}</button>)}<span>...</span><button type="button" onClick={() => setPage(3)}>3</button><button type="button" aria-label="Trang sau" disabled={page === 3} onClick={() => setPage((value) => Math.min(3, value + 1))}>›</button></div>
        </footer>
      </section>
      {selectionError ? <NoticeDialog kind={selectionError.startsWith('Đã ') ? 'success' : 'notice'} message={selectionError} onClose={() => setSelectionError(null)} /> : null}
      {message ? <NoticeDialog kind={message.kind} message={message.text} onClose={dismissMessage} /> : null}
    </div>
  );
}

function OptionCheck({ checked, label, disabled, title, onChange }: { checked: boolean; label: string; disabled?: boolean; title?: string; onChange?(): void }) {
  return <label title={title}><input className="option-input" type="checkbox" checked={checked} disabled={disabled} readOnly={!onChange} onChange={onChange} /><span className="option-box" data-checked={checked}>{checked ? <img src={checkIcon} alt="" /> : null}</span>{label}</label>;
}
