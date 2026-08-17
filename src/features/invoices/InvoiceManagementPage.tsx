import addIcon from '../../assets/figma/add.png';
import calendarIcon from '../../assets/figma/calendar.png';
import searchIcon from '../../assets/figma/search.png';
import stopIcon from '../../assets/figma/stop.png';
import syncIcon from '../../assets/figma/sync.png';

type RowStatus = 'completed' | 'failed' | 'processing' | 'pending';

interface InvoiceRow {
  taxCode: string;
  company: string;
  period: string;
  status: RowStatus;
  selected: boolean;
  progress: number;
  progressLabel: string;
}

const rows: InvoiceRow[] = [
  { taxCode: '0101234567', company: 'Công ty Cổ phần Công nghệ A', period: 'Tháng 10/2023', status: 'completed', selected: true, progress: 100, progressLabel: 'Đã tải 150/150 HĐ' },
  { taxCode: '0309876543', company: 'Công ty TNHH Thương Mại Dịch Vụ B', period: 'Tháng 10/2023', status: 'failed', selected: true, progress: 0, progressLabel: 'Không thể đăng nhập Cổng HĐĐT' },
  { taxCode: '0104567890', company: 'Công ty TNHH Sản xuất C', period: 'Tháng 10/2023', status: 'processing', selected: true, progress: 37, progressLabel: 'Đang tải 45/120 HĐ...' },
  ...['E', 'G', 'H', 'Y', 'K', 'L', 'M'].map((letter) => ({
    taxCode: '0401122334',
    company: `Công ty CP Đầu tư ${letter}`,
    period: 'Tháng 10/2023',
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

export function InvoiceManagementPage() {
  return (
    <div className="invoice-page">
      <section className="toolbar-canvas" aria-label="Thiết lập đồng bộ">
        <div className="toolbar-card">
          <button className="date-picker" type="button">
            <img src={calendarIcon} alt="" />
            <span><small>KHOẢNG THỜI GIAN</small><strong>01/10/2023 - 31/10/2023</strong></span>
            <i className="chevron" />
          </button>
          <button className="compact-select" type="button">Hóa đơn <i className="chevron" /></button>
          <button className="compact-select compact-select--detail" type="button">Chi tiết <i className="chevron" /></button>
          <button className="sync-button" type="button"><img src={syncIcon} alt="" /> Đồng bộ dữ liệu</button>
        </div>
      </section>
      <section className="invoice-content">
        <div className="filters">
          <div className="filters-left">
            <button className="add-account" type="button"><img src={addIcon} alt="" /> Thêm tài khoản</button>
            <label className="search-box">
              <img src={searchIcon} alt="" />
              <input aria-label="Tìm kiếm tài khoản" placeholder="Tìm kiếm MST, Tên công ty..." />
            </label>
            <button className="status-filter" type="button">Tất cả trạng thái <i className="chevron" /></button>
          </div>
          <button className="stop-button" type="button"><img src={stopIcon} alt="" /> Dừng tải</button>
        </div>
        <div className="data-card">
          <div className="table-header table-grid">
            <SelectionBox checked={false} />
            <span>MST</span><span>Kỳ tải</span><span>Trạng thái</span><span>Tiến trình</span><span>Tác vụ</span>
          </div>
          <div className="table-body">
            {rows.map((row, index) => (
              <div className="table-row table-grid" data-status={row.status} key={`${row.taxCode}-${index}`}>
                <SelectionBox checked={row.selected} />
                <div className="company-cell"><span>{row.taxCode}</span><strong>{row.company}</strong></div>
                <span>{row.period}</span>
                <span className="status-badge" data-status={row.status}>{statusLabels[row.status]}</span>
                <ProgressCell row={row} />
                <span className="row-actions">{row.status === 'failed' ? '✎  ↻' : '⋮'}</span>
              </div>
            ))}
          </div>
        </div>
        <footer className="pagination">
          <span>Hiển thị 1 - 50 trong tổng số 128 hóa đơn</span>
          <div><span>Chọn trang:</span><button>‹</button><button data-active="true">1</button><button>2</button><button>3</button><span>...</span><button>3</button><button>›</button></div>
        </footer>
      </section>
    </div>
  );
}
