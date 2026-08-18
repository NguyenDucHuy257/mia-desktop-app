import { useMemo, useState } from 'react';
import { NoticeDialog } from '../../components/NoticeDialog';
import pdfProgressIcon from '../../assets/figma/pdf-progress.svg';
import pdfStopIcon from '../../assets/figma/pdf-stop.svg';
import pdfFolderIcon from '../../assets/figma/pdf-folder.svg';
import artifactCancelIcon from '../../assets/figma/artifact-cancel.svg';
import artifactErrorIcon from '../../assets/figma/artifact-error.svg';
import artifactPreviousIcon from '../../assets/figma/artifact-previous.svg';
import artifactNextIcon from '../../assets/figma/artifact-next.svg';
import artifactCalendarIcon from '../../assets/figma/artifact-calendar.svg';
import artifactCompanyIcon from '../../assets/figma/artifact-company.svg';
import artifactStatusCheckIcon from '../../assets/figma/artifact-status-check.svg';
import artifactDownloadIcon from '../../assets/figma/artifact-download.svg';

type ArtifactKind = 'xml' | 'html';
type Direction = 'all' | 'purchase' | 'sold';

const demoRows = [
  { id: 'HD-2023-001', date: '10/10/2023', direction: 'purchase', state: 'ready' },
  { id: 'HD-2023-002', date: '10/10/2023', direction: 'sold', state: 'loading' },
  { id: 'HD-2023-003', date: '11/10/2023', direction: 'purchase', state: 'missing' },
  { id: 'HD-2023-004', date: '12/10/2023', direction: 'sold', state: 'error' },
] as const;

export function ArtifactDownloaderPage({ kind, folder, connectionIds }: { kind: ArtifactKind; folder: string; onFolder(value: string): void; connectionIds: string[] }) {
  const [direction, setDirection] = useState<Direction>('all');
  const [company, setCompany] = useState('company-a');
  const [dateFrom, setDateFrom] = useState('2023-10-01');
  const [dateTo, setDateTo] = useState('2023-10-31');
  const [page, setPage] = useState(1);
  const [message, setMessage] = useState<string | null>(null);
  const rows = useMemo(() => demoRows.filter((row) => direction === 'all' || row.direction === direction), [direction]);
  const name = kind.toUpperCase();
  const stateLabel = { ready: `Đã có ${name}`, loading: 'Đang tải...', missing: 'Không có dữ liệu gốc', error: 'Lỗi kết nối' };

  async function exportSelected() {
    if (!window.miaRuntime?.artifacts?.export) { setMessage(`Đã thêm ${rows.length} file ${name} vào hàng đợi tải.`); return; }
    if (!folder.trim()) { setMessage('Vui lòng chọn thư mục lưu ở tab PDF.'); return; }
    if (!connectionIds.length) { setMessage('Vui lòng chọn ít nhất một tài khoản ở tab Hóa đơn.'); return; }
    const result = await window.miaRuntime.artifacts.export({ destination: folder, connection_ids: connectionIds, kinds: [kind] });
    setMessage(`Đã xuất ${result.count} file ${name}.`);
  }


  return <section className="artifact-page" data-node-id={kind === 'xml' ? '1:654' : '104:22'} aria-labelledby={`${kind}-title`}>
    <header className="artifact-header">
      <h1 id={`${kind}-title`}>Công cụ tải {name}</h1>
      <p>Quản lý và tải file {name} hóa đơn điện tử.</p>
    </header>
    <ArtifactToolbar direction={direction} onDirection={(value) => { setDirection(value); setPage(1); }} company={company} onCompany={setCompany} dateFrom={dateFrom} dateTo={dateTo} onDateFrom={setDateFrom} onDateTo={setDateTo} />
    <div className="artifact-summary">
      <span><strong>Tiến trình:</strong> <i><img src={artifactStatusCheckIcon} alt="" />Đã tải: 450/500</i></span>
      <button type="button" onClick={() => void exportSelected()}><img src={artifactDownloadIcon} alt="" />Tải {name} hàng loạt</button>
    </div>
    <div className="artifact-table" role="table" aria-label={`Danh sách ${name}`}>
      <div className="artifact-table-head" role="row"><span aria-hidden="true" /><span>Mã hóa đơn</span><span>Ngày lập</span><span>Trạng thái {name}</span><span>Thao tác</span></div>
      {rows.map((row) => <div className="artifact-table-row" role="row" key={row.id} data-state={row.state}>
        <strong>{row.id}</strong><span className="artifact-row-date">{row.date}</span><span aria-hidden="true" />
        <span className="artifact-state" data-state={row.state}>
          {row.state === 'error' ? <img src={artifactErrorIcon} alt="" /> : null}{row.state === 'loading' ? null : stateLabel[row.state]}
          {row.state === 'loading' ? <small><span>Đang tải...</span><b>60%</b><i><em style={{ width: '60%' }} /></i></small> : null}
        </span>
        {row.state === 'loading' ? <button type="button" aria-label={`Hủy tải ${name} ${row.id}`} onClick={() => setMessage(`Đã hủy tải ${row.id}.`)}><img src={artifactCancelIcon} alt="" /></button> : <span aria-hidden="true" />}
      </div>)}
      <ArtifactPager count={rows.length} page={page} onPage={setPage} />
    </div>
    {message ? <NoticeDialog kind="notice" message={message} onClose={() => setMessage(null)} /> : null}
  </section>;
}

function ArtifactToolbar({ direction, onDirection, company, onCompany, dateFrom, dateTo, onDateFrom, onDateTo }: { direction: Direction; onDirection(value: Direction): void; company: string; onCompany(value: string): void; dateFrom: string; dateTo: string; onDateFrom(value: string): void; onDateTo(value: string): void }) {
  return <div className="artifact-toolbar">
    <fieldset className="artifact-date"><img src={artifactCalendarIcon} alt="" /><legend>KHOẢNG THỜI GIAN</legend><strong>{formatDate(dateFrom)} - {formatDate(dateTo)}</strong><input aria-label="Từ ngày" type="date" value={dateFrom} max={dateTo} onChange={(event) => onDateFrom(event.target.value)} /><input aria-label="Đến ngày" type="date" value={dateTo} min={dateFrom} onChange={(event) => onDateTo(event.target.value)} /></fieldset>
    <label className="artifact-company"><img src={artifactCompanyIcon} alt="" /><select aria-label="Chọn công ty" value={company} onChange={(event) => onCompany(event.target.value)}><option value="company-a">Công ty Cổ phần ABC</option><option value="company-b">Công ty TNHH Thương mại B</option><option value="all">Tất cả công ty đã chọn</option></select></label>
    <div className="artifact-segment" aria-label="Loại hóa đơn">
      {([['all', 'Tất cả'], ['purchase', 'Mua vào'], ['sold', 'Bán ra']] as const).map(([value, label]) => <button type="button" key={value} data-active={direction === value} onClick={() => onDirection(value)}>{label}</button>)}
    </div>
  </div>;
}

function formatDate(value: string) { const [year, month, day] = value.split('-'); return year && month && day ? `${day}/${month}/${year}` : value; }

function ArtifactPager({ count, page, onPage }: { count: number; page: number; onPage(value: number): void }) {
  return <footer className="artifact-pager"><span>Hiển thị {count ? (page - 1) * 50 + 1 : 0} - {Math.min(page * 50, 128)} trong tổng số 128 hóa đơn</span><div><span>Chọn trang:</span><button aria-label="Trang trước" disabled={page === 1} onClick={() => onPage(page - 1)}><img src={artifactPreviousIcon} alt="" /></button>{[1, 2, 3].map((value) => <button key={value} data-active={page === value} onClick={() => onPage(value)}>{value}</button>)}<span>...</span><button data-active={page === 3} onClick={() => onPage(3)}>3</button><button aria-label="Trang sau" disabled={page === 3} onClick={() => onPage(page + 1)}><img src={artifactNextIcon} alt="" /></button></div></footer>;
}

export function PdfDownloaderPage({ folder, onFolder, connectionIds }: { folder: string; onFolder(value: string): void; connectionIds: string[] }) {
  const [direction, setDirection] = useState<Direction>('all');
  const [company, setCompany] = useState('company-a');
  const [dateFrom, setDateFrom] = useState('2023-10-01');
  const [dateTo, setDateTo] = useState('2023-10-31');
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(31);
  const [message, setMessage] = useState<string | null>(null);

  async function chooseFolder() {
    const selected = await window.miaRuntime?.artifacts?.selectDirectory();
    if (selected) onFolder(selected);
  }

  async function startPdfExport() {
    setProgress(31);
    setRunning(true);
    if (!window.miaRuntime?.artifacts?.export || !connectionIds.length) return;
    try {
      const result = await window.miaRuntime.artifacts.export({ destination: folder, connection_ids: connectionIds, kinds: ['pdf'] });
      setProgress(100);
      setMessage(`Đã xuất ${result.count} file PDF.`);
    } catch {
      setRunning(false);
      setMessage('Không thể xuất PDF. Vui lòng kiểm tra thư mục và thử lại.');
    }
  }

  return <section className="pdf-page" data-node-id={running ? '106:19444' : '106:18856'} aria-labelledby="pdf-title">
    <div className="pdf-container">
      <header><h1 id="pdf-title">Chuyển đổi PDF Hàng Loạt</h1><p>Chuyển đổi từ HTML → PDF</p></header>
      <div className="pdf-config">
        <label>THƯ MỤC LƯU TRỮ<div><input aria-label="Thư mục lưu trữ" value={folder} onChange={(event) => onFolder(event.target.value)} /><button type="button" aria-label="Chọn thư mục" onClick={() => void chooseFolder()}>▱</button></div></label>
        <ArtifactToolbar direction={direction} onDirection={setDirection} company={company} onCompany={setCompany} dateFrom={dateFrom} dateTo={dateTo} onDateFrom={setDateFrom} onDateTo={setDateTo} />
      </div>
      <button className="pdf-start" type="button" disabled={!folder.trim()} onClick={() => { if (!running) void startPdfExport(); }}>⇩ Tải HTML hàng loạt</button>
      {running ? <div className="pdf-progress" role="status">
        <div className="pdf-progress-heading"><img className="pdf-file-icon" src={pdfProgressIcon} alt="" /><h2>Đang xuất PDF...</h2><p>Vui lòng không đóng ứng dụng trong quá trình này.</p></div>
        <div className="pdf-progress-body"><div className="pdf-progress-copy"><span>Tiến độ: 380 / 1.220</span><strong>{progress}%</strong></div><div className="pdf-progress-track"><span style={{ width: `${progress}%` }} /></div><small>Đang xử lý: Hóa đơn GTGT #0004829 - Công ty TNHH Thương Mại Dịch Vụ ABC...</small></div>
        <div className="pdf-actions"><button type="button" onClick={() => setRunning(false)}><img src={pdfStopIcon} alt="" />Dừng lại</button><button type="button" disabled aria-disabled="true" onClick={() => setMessage('Thư mục đích sẽ được mở qua Electron main trong Phase 5.')}><img src={pdfFolderIcon} alt="" />Mở thư mục</button></div>
      </div> : null}
    </div>
    {message ? <NoticeDialog kind="notice" message={message} onClose={() => setMessage(null)} /> : null}
  </section>;
}

export function UtilityPage({ title, description }: { title: string; description: string }) {
  return <section className="utility-page"><h1>{title}</h1><p>{description}</p><div className="utility-empty"><strong>Chưa có dữ liệu</strong><span>Dữ liệu sẽ xuất hiện tại đây khi tính năng được sử dụng.</span></div></section>;
}
