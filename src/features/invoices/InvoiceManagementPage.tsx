import { useEffect, useState } from 'react';
import { NoticeDialog } from '../../components/NoticeDialog';
import addIcon from '../../assets/figma/add.png';
import calendarIcon from '../../assets/figma/calendar.png';
import searchIcon from '../../assets/figma/search.png';
import stopIcon from '../../assets/figma/stop.png';
import syncIcon from '../../assets/figma/sync.png';
import checkIcon from '../../assets/figma/check.svg';
import { useJobLifecycle } from '../jobs/use-job-lifecycle';
import { TERMINAL_JOB_STATUSES } from '../jobs/job-state-machine';
import type { AccountConnection, InvoiceDirection } from '../../lib/api/contracts';

type RowStatus = 'completed' | 'failed' | 'processing' | 'pending';

interface InvoiceRow {
  taxCode: string;
  company: string;
  status: RowStatus;
  selected: boolean;
  progress: number;
  progressLabel: string;
}

const rows: InvoiceRow[] = [
  { taxCode: '0101234567', company: 'Công ty Cổ phần Công nghệ A', status: 'completed', selected: true, progress: 100, progressLabel: 'Đã tải 150/150 HĐ' },
  { taxCode: '0309876543', company: 'Công ty TNHH Thương Mại Dịch Vụ B', status: 'failed', selected: true, progress: 0, progressLabel: 'Không thể đăng nhập Cổng HĐĐT' },
  { taxCode: '0104567890', company: 'Công ty TNHH Sản xuất C', status: 'processing', selected: true, progress: 37, progressLabel: 'Đang tải 45/120 HĐ...' },
  ...['E', 'G', 'H', 'Y', 'K', 'L', 'M'].map((letter) => ({
    taxCode: '0401122334',
    company: `Công ty CP Đầu tư ${letter}`,
    status: 'pending' as const,
    selected: false,
    progress: 0,
    progressLabel: 'Chờ trong hàng đợi...',
  })),
];

const statusLabels: Record<RowStatus, string> = {
  completed: 'Hoàn thành',
  failed: 'ⓘ Thất bại',
  processing: 'ϟ Đang xử lý',
  pending: 'Chờ xử lý',
};

function SelectionBox({ checked }: { checked: boolean }) {
  return <span className="selection-box" data-checked={checked}>{checked ? '✓' : ''}</span>;
}

function ProgressCell({ row }: { row: InvoiceRow }) {
  if (row.status === 'failed') {
    return (
      <div className="failure-message">
        <strong>{row.progressLabel}</strong>
        <span>Vui lòng kiểm tra lại MST hoặc Mật khẩu.</span>
      </div>
    );
  }
  return (
    <div className="progress-cell" data-status={row.status}>
      <div className="progress-copy"><span>{row.progressLabel}</span><span>{row.progress}%</span></div>
      <div className="progress-track"><span style={{ width: `${row.progress}%` }} /></div>
    </div>
  );
}

export function InvoiceManagementPage({ onAddAccount, connectionId, accounts, onDeleteAccount, onSelectAccount, onViewResults }: {
  onAddAccount(): void;
  connectionId: string;
  accounts: AccountConnection[] | null;
  onDeleteAccount(id: string): Promise<void>;
  onSelectAccount(id: string): void;
  onViewResults(id: string): void;
}) {
  const [menu, setMenu] = useState<'scope' | 'direction' | null>(null);
  const [scopes, setScopes] = useState<Array<'overview' | 'detail'>>(['overview', 'detail']);
  const [directions, setDirections] = useState<InvoiceDirection[]>(['purchase', 'sold']);
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<RowStatus | ''>('');
  const [dateFrom, setDateFrom] = useState('2023-10-01');
  const [dateTo, setDateTo] = useState('2023-10-31');
  const [page, setPage] = useState(1);
  const { state: job, start, cancel, retry, dismissMessage } = useJobLifecycle();

  useEffect(() => {
    if (!menu) return;
    const closeOutside = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Element && !target.closest('.select-wrap')) setMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenu(null);
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [menu]);

  function startJob() {
    if (!connectionId) { onAddAccount(); return; }
    if (directions.length === 0 || scopes.length === 0) {
      setSelectionError('Vui lòng chọn ít nhất một hướng và phạm vi dữ liệu trước khi đồng bộ.');
      return;
    }
    setSelectionError(null);
    void start({
      connection_id: connectionId, date_from: dateFrom, date_to: dateTo,
      directions, query_types: ['query'], scopes, data_types: ['invoice'],
    });
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

  const activeJob = Boolean(job.status && !TERMINAL_JOB_STATUSES.has(job.status.status));
  const allRows: InvoiceRow[] = accounts === null ? rows : accounts.map((account) => ({
    taxCode: account.username,
    company: '—',
    status: account.status === 'active' ? 'completed' : 'pending',
    selected: account.connection_id === connectionId,
    progress: 0,
    progressLabel: account.status === 'unchecked' ? 'Chưa kiểm tra đăng nhập' : account.status,
  }));
  const visibleRows = allRows.filter((row) => {
    const term = search.trim().toLocaleLowerCase('vi');
    return (!term || `${row.taxCode} ${row.company}`.toLocaleLowerCase('vi').includes(term)) && (!statusFilter || row.status === statusFilter);
  });
  return (
    <div className="invoice-page">
      <section className="toolbar-canvas" aria-label="Thiết lập đồng bộ">
        <div className="toolbar-card">
          <fieldset className="date-picker">
            <img src={calendarIcon} alt="" />
            <legend>KHOẢNG THỜI GIAN</legend><input aria-label="Từ ngày đồng bộ" type="date" value={dateFrom} max={dateTo} onChange={(event) => setDateFrom(event.target.value)} /><span>–</span><input aria-label="Đến ngày đồng bộ" type="date" value={dateTo} min={dateFrom} onChange={(event) => setDateTo(event.target.value)} />
          </fieldset>
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
          <button className="sync-button" type="button" onClick={startJob}><img src={syncIcon} alt="" /> {job.phase === 'starting' ? 'Đang tạo job...' : 'Đồng bộ dữ liệu'}</button>
          {job.status ? <div className="job-progress-panel" role="status">
            <strong>{job.status.status}</strong><span>{job.status.overall_percent}% tổng thể</span>
            <div className="progress-track"><span style={{ width: `${job.status.overall_percent}%` }} /></div>
            {job.status.current_month ? <><small>Tháng {job.status.current_month.key}: {job.status.current_month.processed}/{job.status.current_month.planned}</small><div className="progress-track"><span style={{ width: `${job.status.current_month.percent}%` }} /></div></> : null}
            {job.phase === 'error' ? <button type="button" onClick={retry}>Thử lại</button> : null}
          </div> : null}
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
            <select className="status-filter" aria-label="Lọc trạng thái" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value as RowStatus | ''); setPage(1); }}><option value="">Tất cả trạng thái</option><option value="completed">Hoàn thành</option><option value="processing">Đang xử lý</option><option value="failed">Thất bại</option><option value="pending">Chờ xử lý</option></select>
          </div>
          <button className="stop-button" type="button" disabled={!activeJob} onClick={() => void cancel()}><img src={stopIcon} alt="" /> Dừng tải</button>
        </div>
        <div className="data-card">
          <div className="table-header table-grid">
            <SelectionBox checked={false} />
            <span>MST</span><span>Tên công ty</span><span>Trạng thái</span><span>Tiến trình</span><span>Tác vụ</span>
          </div>
          <div className="table-body">
            {visibleRows.map((row, index) => (
              <div className="table-row table-grid" data-status={row.status} key={`${row.taxCode}-${index}`}>
                {accounts ? <button className="selection-button" type="button" aria-label={`Chọn ${row.taxCode}`} onClick={() => onSelectAccount(accounts[index]!.connection_id)}><SelectionBox checked={row.selected} /></button> : <SelectionBox checked={row.selected} />}
                <span>{row.taxCode}</span>
                <strong className="company-name">{row.company}</strong>
                <span className="status-badge" data-status={row.status}>{statusLabels[row.status]}</span>
                <ProgressCell row={row} />
                {accounts ? <span className="row-action-group"><button type="button" aria-label={`Xem kết quả ${row.taxCode}`} onClick={() => onViewResults(accounts[index]!.connection_id)}>⋮</button><button type="button" aria-label={`Xóa ${row.taxCode}`} onClick={() => void onDeleteAccount(accounts[index]!.connection_id)}>×</button></span> : <span className="row-actions">{row.status === 'failed' ? '✎  ↻' : '⋮'}</span>}
              </div>
            ))}
          </div>
        </div>
        <footer className="pagination">
          <span>{accounts ? `Hiển thị ${visibleRows.length} tài khoản` : `Hiển thị trang ${page} trong tổng số 128 hóa đơn`}</span>
          <div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={page === 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>‹</button>{[1, 2, 3].map((value) => <button type="button" key={value} data-active={page === value} onClick={() => setPage(value)}>{value}</button>)}<span>...</span><button type="button" onClick={() => setPage(3)}>3</button><button type="button" aria-label="Trang sau" disabled={page === 3} onClick={() => setPage((value) => Math.min(3, value + 1))}>›</button></div>
        </footer>
      </section>
      {!job.status && job.message ? <NoticeDialog kind={job.phase === 'error' ? 'error' : 'notice'} message={job.message} onClose={dismissMessage} actionLabel={job.phase === 'error' ? 'Thử lại' : undefined} onAction={job.phase === 'error' ? () => { dismissMessage(); retry(); } : undefined} /> : null}
      {selectionError ? <NoticeDialog kind="notice" message={selectionError} onClose={() => setSelectionError(null)} /> : null}
    </div>
  );
}

function OptionCheck({ checked, label, disabled, title, onChange }: { checked: boolean; label: string; disabled?: boolean; title?: string; onChange?(): void }) {
  return <label title={title}><input className="option-input" type="checkbox" checked={checked} disabled={disabled} readOnly={!onChange} onChange={onChange} /><span className="option-box" data-checked={checked}>{checked ? <img src={checkIcon} alt="" /> : null}</span>{label}</label>;
}
