import { useEffect, useMemo, useRef, useState } from 'react';
import { NoticeDialog } from '../../components/NoticeDialog';
import { DateRangePicker } from '../../components/DateRangePicker';
import pdfProgressIcon from '../../assets/figma/pdf-progress.svg';
import pdfStopIcon from '../../assets/figma/pdf-stop.svg';
import pdfFolderIcon from '../../assets/figma/pdf-folder.svg';
import artifactCancelIcon from '../../assets/figma/artifact-cancel.svg';
import artifactErrorIcon from '../../assets/figma/artifact-error.svg';
import artifactPreviousIcon from '../../assets/figma/artifact-previous.svg';
import artifactNextIcon from '../../assets/figma/artifact-next.svg';
import artifactCompanyIcon from '../../assets/figma/artifact-company.svg';
import artifactStatusCheckIcon from '../../assets/figma/artifact-status-check.svg';
import artifactDownloadIcon from '../../assets/figma/artifact-download.svg';
import type { AccountConnection } from '../../lib/api/contracts';
import type { ArtifactItem } from '../../lib/runtime-bridge';
import { useBatchJobLifecycle } from '../jobs/use-batch-job-lifecycle';

type ArtifactKind = 'xml' | 'html';
type Direction = 'all' | 'purchase' | 'sold';

const demoRows = [
  { id: 'HD-2023-001', date: '10/10/2023', direction: 'purchase', state: 'ready' },
  { id: 'HD-2023-002', date: '10/10/2023', direction: 'sold', state: 'loading' },
  { id: 'HD-2023-003', date: '11/10/2023', direction: 'purchase', state: 'missing' },
  { id: 'HD-2023-004', date: '12/10/2023', direction: 'sold', state: 'error' },
] as const;

export function ArtifactDownloaderPage({ kind, folder, connectionIds, accounts }: { kind: ArtifactKind; folder: string; onFolder(value: string): void; connectionIds: string[]; accounts: AccountConnection[] }) {
  const [direction, setDirection] = useState<Direction>('all');
  const [company, setCompany] = useState('all');
  const [dateFrom, setDateFrom] = useState('2023-10-01');
  const [dateTo, setDateTo] = useState('2023-10-31');
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<(string | null)[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [runtimeItems, setRuntimeItems] = useState<ArtifactItem[] | null>(null);
  const [loadState, setLoadState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [pendingExport, setPendingExport] = useState(false);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const requestGeneration = useRef(0);
  const { items: artifactJobs, startMany, cancelAll } = useBatchJobLifecycle();
  const demo = typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).get('demo') === '1';
  const selectedConnections = company === 'all' ? connectionIds : connectionIds.includes(company) ? [company] : [];
  const rows = useMemo(() => demo
    ? demoRows.filter((row) => direction === 'all' || row.direction === direction)
    : (runtimeItems ?? []).map((item) => ({ id: item.filename, date: new Date(Math.floor(item.updated_at / 1_000_000)).toLocaleDateString('vi-VN'), direction: item.direction ?? 'purchase', state: 'ready' as const })), [demo, direction, runtimeItems]);
  const name = kind.toUpperCase();
  const stateLabel = { ready: `Đã có ${name}`, loading: 'Đang tải...', missing: 'Không có dữ liệu gốc', error: 'Lỗi kết nối' };

  useEffect(() => {
    if (demo) { setLoadState('ready'); return; }
    const bridge = window.miaRuntime?.artifacts;
    if (!bridge || !selectedConnections.length) { setRuntimeItems([]); setLoadState('ready'); return; }
    const token = ++requestGeneration.current;
    setLoadState('loading');
    void bridge.list({ connection_ids: selectedConnections, kind, direction: direction === 'all' ? null : direction, search, cursor, date_from: dateFrom, date_to: dateTo, limit: 50 }).then((result) => {
      if (token !== requestGeneration.current) return;
      setRuntimeItems(result.items);
      setNextCursor(result.pagination.next_cursor);
      setLoadState('ready');
    }).catch(() => { if (token === requestGeneration.current) setLoadState('error'); });
  }, [company, connectionIds.join('|'), cursor, dateFrom, dateTo, demo, direction, kind, refreshVersion, search]);

  function resetCursor() {
    setCursor(null);
    setCursorHistory([]);
    setPage(1);
  }

  useEffect(() => {
    if (!pendingExport) return;
    const jobs = Object.values(artifactJobs);
    if (!jobs.length || jobs.some((item) => !item.status || !['completed', 'completed_with_warning', 'failed', 'cancelled', 'abandoned'].includes(item.status.status))) return;
    if (jobs.some((item) => item.status?.status === 'failed')) {
      setPendingExport(false);
      setMessage(`Không thể tải đủ dữ liệu ${name}. Các tài khoản khác vẫn được giữ kết quả.`);
      return;
    }
    setPendingExport(false);
    void exportLocalFiles();
  }, [artifactJobs, pendingExport]);

  async function exportLocalFiles() {
    if (!window.miaRuntime?.artifacts?.export) return;
    const result = await window.miaRuntime.artifacts.export({ destination: folder, connection_ids: selectedConnections, kinds: [kind] });
    setMessage(`Đã xuất ${result.count} file ${name}.`);
    setRefreshVersion((value) => value + 1);
  }

  async function exportSelected() {
    if (!window.miaRuntime?.artifacts?.export) { setMessage(`Đã thêm ${rows.length} file ${name} vào hàng đợi tải.`); return; }
    if (!folder.trim()) { setMessage('Vui lòng chọn thư mục lưu ở tab PDF.'); return; }
    if (!connectionIds.length) { setMessage('Vui lòng chọn ít nhất một tài khoản ở tab Hóa đơn.'); return; }
    if (rows.length) { await exportLocalFiles(); return; }
    const directions = direction === 'all' ? ['purchase', 'sold'] as const : [direction];
    setPendingExport(true);
    startMany(selectedConnections.map((connection_id) => ({
      connection_id, date_from: dateFrom, date_to: dateTo, directions: [...directions],
      query_types: ['query', 'sco-query'], scopes: ['overview'], data_types: [kind],
    })));
  }


  return <section className="artifact-page" data-node-id={kind === 'xml' ? '1:654' : '104:22'} aria-labelledby={`${kind}-title`}>
    <header className="artifact-header">
      <h1 id={`${kind}-title`}>Công cụ tải {name}</h1>
      <p>Quản lý và tải file {name} hóa đơn điện tử.</p>
    </header>
    <ArtifactToolbar direction={direction} onDirection={(value) => { setDirection(value); resetCursor(); }} company={company} onCompany={(value) => { setCompany(value); resetCursor(); }} accounts={accounts} demo={demo} dateFrom={dateFrom} dateTo={dateTo} onDateFrom={(value) => { setDateFrom(value); resetCursor(); }} onDateTo={(value) => { setDateTo(value); resetCursor(); }} />
    {!demo ? <label className="artifact-search">Tìm hóa đơn<input aria-label={`Tìm kiếm ${name}`} value={search} onChange={(event) => { setSearch(event.target.value); resetCursor(); }} /></label> : null}
    <div className="artifact-summary">
      <span><strong>Tiến trình:</strong> <i><img src={artifactStatusCheckIcon} alt="" />Đã tải: 450/500</i></span>
      {!demo && pendingExport ? <button type="button" onClick={() => void cancelAll()}><img src={artifactCancelIcon} alt="" />Dừng tải {name}</button> : <button type="button" onClick={() => void exportSelected()}><img src={artifactDownloadIcon} alt="" />Tải {name} hàng loạt</button>}
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
      {!demo && loadState === 'loading' ? <div className="results-state" role="status">Đang tải danh sách {name}...</div> : null}
      {!demo && loadState === 'error' ? <div className="results-state" role="alert">Không thể tải danh sách {name}.</div> : null}
      {!demo && loadState === 'ready' && rows.length === 0 ? <div className="results-state">Chưa có file {name} cho lựa chọn hiện tại.</div> : null}
      {demo ? <ArtifactPager count={rows.length} page={page} onPage={setPage} /> : <footer className="artifact-pager"><span>Hiển thị {rows.length} file trên trang {page}</span><div><button type="button" disabled={!cursorHistory.length} onClick={() => { const history = [...cursorHistory]; setCursor(history.pop() ?? null); setCursorHistory(history); setPage((value) => Math.max(1, value - 1)); }}>Trang trước</button><button type="button" disabled={!nextCursor} onClick={() => { if (!nextCursor) return; setCursorHistory((value) => [...value, cursor]); setCursor(nextCursor); setPage((value) => value + 1); }}>Tải trang sau</button></div></footer>}
    </div>
    {message ? <NoticeDialog kind={message.startsWith('Đã ') ? 'success' : 'notice'} message={message} onClose={() => setMessage(null)} /> : null}
  </section>;
}

function ArtifactToolbar({ direction, onDirection, company, onCompany, accounts = [], demo = false, dateFrom, dateTo, onDateFrom, onDateTo }: { direction: Direction; onDirection(value: Direction): void; company: string; onCompany(value: string): void; accounts?: AccountConnection[]; demo?: boolean; dateFrom: string; dateTo: string; onDateFrom(value: string): void; onDateTo(value: string): void }) {
  return <div className="artifact-toolbar">
    <DateRangePicker dateFrom={dateFrom} dateTo={dateTo} onChange={(from, to) => { onDateFrom(from); onDateTo(to); }} />
    <label className="artifact-company"><img src={artifactCompanyIcon} alt="" /><select aria-label="Chọn công ty" value={company} onChange={(event) => onCompany(event.target.value)}>{demo ? <><option value="company-a">Công ty Cổ phần ABC</option><option value="company-b">Công ty TNHH Thương mại B</option></> : accounts.map((account) => <option key={account.connection_id} value={account.connection_id}>{account.company_name || account.username}</option>)}<option value="all">Tất cả công ty đã chọn</option></select></label>
    <div className="artifact-segment" aria-label="Loại hóa đơn">
      {([['all', 'Tất cả'], ['purchase', 'Mua vào'], ['sold', 'Bán ra']] as const).map(([value, label]) => <button type="button" key={value} data-active={direction === value} onClick={() => onDirection(value)}>{label}</button>)}
    </div>
  </div>;
}


function ArtifactPager({ count, page, onPage }: { count: number; page: number; onPage(value: number): void }) {
  return <footer className="artifact-pager"><span>Hiển thị {count ? (page - 1) * 50 + 1 : 0} - {Math.min(page * 50, 128)} trong tổng số 128 hóa đơn</span><div><span>Chọn trang:</span><button aria-label="Trang trước" disabled={page === 1} onClick={() => onPage(page - 1)}><img src={artifactPreviousIcon} alt="" /></button>{[1, 2, 3].map((value) => <button key={value} data-active={page === value} onClick={() => onPage(value)}>{value}</button>)}<span>...</span><button data-active={page === 3} onClick={() => onPage(3)}>3</button><button aria-label="Trang sau" disabled={page === 3} onClick={() => onPage(page + 1)}><img src={artifactNextIcon} alt="" /></button></div></footer>;
}

export function PdfDownloaderPage({ folder, onFolder, connectionIds, accounts }: { folder: string; onFolder(value: string): void; connectionIds: string[]; accounts: AccountConnection[] }) {
  const [direction, setDirection] = useState<Direction>('all');
  const [company, setCompany] = useState(() => typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).get('demo') === '1' ? 'company-a' : 'all');
  const [dateFrom, setDateFrom] = useState('2023-10-01');
  const [dateTo, setDateTo] = useState('2023-10-31');
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(31);
  const [message, setMessage] = useState<string | null>(null);
  const demo = typeof window !== 'undefined'
    && new URLSearchParams(window.location.search).get('demo') === '1';
  const { items: pdfJobs, startMany: startPdfJobs, cancelAll: cancelPdfJobs } = useBatchJobLifecycle();

  async function chooseFolder() {
    const selected = await window.miaRuntime?.artifacts?.selectDirectory();
    if (selected) onFolder(selected);
  }

  async function startPdfExport() {
    setProgress(31);
    setRunning(true);
    if (demo || !window.miaRuntime?.artifacts?.export || !connectionIds.length) return;
    try {
      const selected = company === 'all' ? connectionIds : connectionIds.includes(company) ? [company] : [];
      if (!selected.length) { setRunning(false); setMessage('Vui lòng chọn ít nhất một tài khoản.'); return; }
      const directions = direction === 'all' ? ['purchase', 'sold'] as const : [direction];
      startPdfJobs(selected.map((connection_id) => ({
        connection_id, date_from: dateFrom, date_to: dateTo, directions: [...directions],
        query_types: ['query', 'sco-query'], scopes: ['overview'], data_types: ['pdf'],
      })));
    } catch {
      setRunning(false);
      setMessage('Không thể xuất PDF. Vui lòng kiểm tra thư mục và thử lại.');
    }
  }

  useEffect(() => {
    if (demo || !running) return;
    const jobs = Object.values(pdfJobs);
    if (!jobs.length) return;
    const percents = jobs.map((item) => item.status?.overall_percent ?? 0);
    setProgress(Math.floor(percents.reduce((sum, value) => sum + value, 0) / percents.length));
    if (jobs.some((item) => item.status?.status === 'failed')) { setRunning(false); setMessage('Không thể tạo đủ PDF. Vui lòng kiểm tra dữ liệu HTML và thử lại.'); return; }
    if (jobs.every((item) => item.status && ['completed', 'completed_with_warning'].includes(item.status.status))) {
      const selected = company === 'all' ? connectionIds : connectionIds.includes(company) ? [company] : [];
      void window.miaRuntime?.artifacts?.export({ destination: folder, connection_ids: selected, kinds: ['pdf'] }).then((result) => {
        setProgress(100);
        setMessage(`Đã xuất ${result.count} file PDF.`);
      }).catch(() => { setRunning(false); setMessage('Không thể ghi PDF vào thư mục đích.'); });
    }
  }, [pdfJobs, running]);

  return <section className="pdf-page" data-node-id={running ? '106:19444' : '106:18856'} aria-labelledby="pdf-title">
    <div className="pdf-container">
      <header><h1 id="pdf-title">Chuyển đổi PDF Hàng Loạt</h1><p>Chuyển đổi từ HTML → PDF</p></header>
      <div className="pdf-config">
        <label>THƯ MỤC LƯU TRỮ<div><input aria-label="Thư mục lưu trữ" value={folder} onChange={(event) => onFolder(event.target.value)} /><button type="button" aria-label="Chọn thư mục" onClick={() => void chooseFolder()}>▱</button></div></label>
        <ArtifactToolbar direction={direction} onDirection={setDirection} company={company} onCompany={setCompany} accounts={accounts} demo={demo} dateFrom={dateFrom} dateTo={dateTo} onDateFrom={setDateFrom} onDateTo={setDateTo} />
      </div>
      <button className="pdf-start" type="button" disabled={!folder.trim()} onClick={() => { if (!running) void startPdfExport(); }}>⇩ Tải HTML hàng loạt</button>
      {running ? <div className="pdf-progress" role="status">
        <div className="pdf-progress-heading"><img className="pdf-file-icon" src={pdfProgressIcon} alt="" /><h2>Đang xuất PDF...</h2><p>Vui lòng không đóng ứng dụng trong quá trình này.</p></div>
        <div className="pdf-progress-body"><div className="pdf-progress-copy"><span>Tiến độ: 380 / 1.220</span><strong>{progress}%</strong></div><div className="pdf-progress-track"><span style={{ width: `${progress}%` }} /></div><small>Đang xử lý: Hóa đơn GTGT #0004829 - Công ty TNHH Thương Mại Dịch Vụ ABC...</small></div>
        <div className="pdf-actions"><button type="button" onClick={() => { if (!demo) void cancelPdfJobs(); setRunning(false); }}><img src={pdfStopIcon} alt="" />Dừng lại</button><button type="button" disabled={progress < 100} aria-disabled={progress < 100} onClick={() => void window.miaRuntime?.artifacts?.openDirectory(folder).catch(() => setMessage('Không thể mở thư mục đích.'))}><img src={pdfFolderIcon} alt="" />Mở thư mục</button></div>
      </div> : null}
    </div>
    {message ? <NoticeDialog kind={message.startsWith('Đã ') ? 'success' : 'notice'} message={message} onClose={() => setMessage(null)} /> : null}
  </section>;
}

export function UtilityPage({ title, description }: { title: string; description: string }) {
  const [query, setQuery] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [retries, setRetries] = useState(5);
  const isSettings = title === 'Cài đặt';
  const isLogs = title === 'Nhật ký';
  useEffect(() => {
    if (isSettings) void window.miaRuntime?.preferences?.get().then((value) => { setRetries(value.retries); }).catch(() => undefined);
    if (isLogs) void window.miaRuntime?.logs?.list().then(setLogs).catch(() => setMessage('Không thể đọc nhật ký cục bộ.'));
  }, [isLogs, isSettings]);
  const visibleLogs = logs.filter((line) => line.toLocaleLowerCase('vi').includes(query.toLocaleLowerCase('vi')));
  async function saveSettings() {
    try {
      const saved = await window.miaRuntime?.preferences?.set({ concurrency: 1, retries });
      if (!saved) throw new Error('preferences_unavailable');
      setRetries(saved.retries);
      setMessage('Đã lưu cài đặt trên máy. Tác vụ mới sẽ áp dụng cấu hình này.');
    } catch { setMessage('Không thể lưu cài đặt.'); }
  }
  return <section className="utility-page"><h1>{title}</h1><p>{description}</p>{isSettings ? <div className="utility-panel"><label>Chế độ xử lý<select value={1} disabled aria-label="Chế độ xử lý tuần tự"><option value={1}>Tuần tự (1 tài khoản/lần)</option></select></label><label>Số lần thử lại<input type="number" min="0" max="5" value={retries} onChange={(event) => setRetries(Number(event.target.value))} /></label><button type="button" onClick={() => void saveSettings()}>Lưu cài đặt</button></div> : isLogs ? <div className="utility-panel"><label>Tìm kiếm<input aria-label="Tìm kiếm Nhật ký" value={query} onChange={(event) => setQuery(event.target.value)} /></label><button type="button" onClick={() => void window.miaRuntime?.logs?.list().then(setLogs).catch(() => setMessage('Không thể làm mới nhật ký.'))}>Làm mới</button>{visibleLogs.length ? <ol className="utility-log-list">{visibleLogs.map((line, index) => <li key={`${index}:${line}`}>{line}</li>)}</ol> : <div className="utility-empty"><strong>Chưa có nhật ký phù hợp</strong></div>}</div> : <div className="utility-panel"><div className="utility-empty"><strong>Chưa có nguồn dữ liệu mã vật tư</strong><span>Runtime crawler hiện không cung cấp danh mục mã vật tư. Không có dữ liệu giả được hiển thị.</span></div></div>}{message ? <NoticeDialog kind={message.startsWith('Đã ') ? 'success' : 'notice'} message={message} onClose={() => setMessage(null)} /> : null}</section>;
}
