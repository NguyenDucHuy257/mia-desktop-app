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

export function InvoiceManagementPage({ onAddAccount, connectionId, accounts, onDeleteAccount, onSelectAccount }: {
  onAddAccount(): void;
  connectionId: string;
  accounts: AccountConnection[] | null;
  onDeleteAccount(id: string): Promise<void>;
  onSelectAccount(id: string): void;
}) {
  const [menu, setMenu] = useState<'scope' | 'direction' | null>(null);
  const [scopes, setScopes] = useState<Array<'overview' | 'detail'>>(['overview', 'detail']);
  const [directions, setDirections] = useState<InvoiceDirection[]>(['purchase', 'sold']);
  const [selectionError, setSelectionError] = useState<string | null>(null);
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
      connection_id: connectionId, date_from: '2023-10-01', date_to: '2023-10-31',
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
  const visibleRows: InvoiceRow[] = accounts === null ? rows : accounts.map((account) => ({
    taxCode: account.username,
    company: '—',
    status: account.status === 'active' ? 'completed' : 'pending',
    selected: account.connection_id === connectionId,
    progress: 0,
    progressLabel: account.status === 'unchecked' ? 'Chưa kiểm tra đăng nhập' : account.status,
  }));
  return (
    <div className="invoice-page">
      <section className="toolbar-canvas" aria-label="Thiết lập đồng bộ">
        <div className="toolbar-card">
          <button className="date-picker" type="button">
            <img src={calendarIcon} alt="" />
            <span><small>KHOẢNG THỜI GIAN</small><strong>01/10/2023 - 31/10/2023</strong></span>
            <i className="chevron" />
          </button>
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
              <input aria-label="Tìm kiếm tài khoản" placeholder="Tìm kiếm MST, Tên công ty..." />
            </label>
            <button className="status-filter" type="button">Tất cả trạng thái <i className="chevron" /></button>
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
                {accounts ? <button className="row-actions" type="button" aria-label={`Xóa ${row.taxCode}`} onClick={() => void onDeleteAccount(accounts[index]!.connection_id)}>×</button> : <span className="row-actions">{row.status === 'failed' ? '✎  ↻' : '⋮'}</span>}
              </div>
            ))}
          </div>
        </div>
        <footer className="pagination">
          <span>{accounts ? `Hiển thị ${accounts.length} tài khoản` : 'Hiển thị 1 - 50 trong tổng số 128 hóa đơn'}</span>
          <div><span>Chọn trang:</span><button>‹</button><button data-active="true">1</button><button>2</button><button>3</button><span>...</span><button>3</button><button>›</button></div>
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
