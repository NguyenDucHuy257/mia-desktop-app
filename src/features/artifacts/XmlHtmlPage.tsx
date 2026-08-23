import { useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { pageBounds, paginationTokens } from '../../components/pagination-utils';
import { StorageFolderPicker } from '../../components/StorageFolderPicker';
import { NoticeDialog } from '../../components/NoticeDialog';
import searchIcon from '../../assets/figma/search.png';
import checkIcon from '../../assets/figma/check.svg';
import type { AccountConnection, InvoiceDirection } from '../../lib/api/contracts';
import type { ArtifactAccountSnapshot, ArtifactSnapshotRequest, InvoiceArtifactKind } from '../../lib/runtime-bridge';
import type { ArtifactDownloadLifecycle } from './use-artifact-download-lifecycle';
import '../../styles/xml-html.css';

const ACCOUNT_PAGE_SIZE = 20;
type RowStatus = 'ready' | 'not_ready' | 'downloading' | 'completed' | 'error' | 'stopped';

export interface ArtifactSelectionState {
  dateFrom: string;
  dateTo: string;
  directions: InvoiceDirection[];
}

export async function loadArtifactSnapshots(request: ArtifactSnapshotRequest) {
  const chunks: string[][] = [];
  for (let index = 0; index < request.connection_ids.length; index += 50) chunks.push(request.connection_ids.slice(index, index + 50));
  const results = await Promise.all(chunks.map((connection_ids) => window.miaRuntime!.artifacts.snapshot({ ...request, connection_ids })));
  return results.flatMap((result) => result.accounts);
}

function SelectionBox({ checked, indeterminate = false }: { checked: boolean; indeterminate?: boolean }) {
  return <span className="selection-box" data-checked={checked || indeterminate}>{indeterminate ? '−' : checked ? '✓' : ''}</span>;
}

function OptionCheck({ checked, label, onChange }: { checked: boolean; label: string; onChange(): void }) {
  return <label><input className="option-input" type="checkbox" checked={checked} onChange={onChange} /><span className="option-box" data-checked={checked}>{checked ? <img src={checkIcon} alt="" /> : null}</span>{label}</label>;
}

function formatDate(value: string) {
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}/${month}/${year}` : value;
}

function Quantity({ snapshot }: { snapshot?: ArtifactAccountSnapshot }) {
  const total = snapshot?.total ?? 0;
  return <span className="artifact-quantity">{(['xml', 'html', 'pdf'] as const).map((kind) => <span key={kind}><strong>{kind.toUpperCase()}</strong> {(snapshot?.cached[kind] ?? 0).toLocaleString('vi-VN')}/{total.toLocaleString('vi-VN')}</span>)}</span>;
}

function FormatIcon({ kind }: { kind: InvoiceArtifactKind }) {
  return <span className="artifact-format-icon" data-kind={kind} aria-hidden="true">{kind === 'xml' ? '</>' : kind === 'html' ? '<H>' : 'PDF'}</span>;
}

function ProgressCard({ kind, lifecycle }: { kind: InvoiceArtifactKind; lifecycle: ArtifactDownloadLifecycle }) {
  const progress = lifecycle.status?.formats[kind];
  if (!progress) return null;
  const label = kind === 'pdf'
    ? progress.status === 'preparing' ? 'Đang chuẩn bị dữ liệu...' : progress.status === 'completed' ? 'Đã xuất PDF' : 'Đang xuất PDF...'
    : progress.status === 'completed' ? `Đã tải ${kind.toUpperCase()}` : `Đang tải ${kind.toUpperCase()}...`;
  return <article className="artifact-progress-card" data-kind={kind} data-status={progress.status}>
    <header><FormatIcon kind={kind} /><div><h2>{kind.toUpperCase()}</h2><span>{label}</span></div></header>
    <div className="artifact-progress-copy"><span>Tiến độ: {progress.processed.toLocaleString('vi-VN')} / {progress.total.toLocaleString('vi-VN')}</span><strong>{Math.round(progress.percent)}%</strong></div>
    <div className="artifact-progress-track"><span style={{ width: `${Math.max(0, Math.min(100, progress.percent))}%` }} /></div>
    <p title={progress.current_invoice ?? ''}>{progress.current_invoice ? `Đang xử lý: ${progress.current_invoice}` : kind === 'pdf' && progress.status === 'preparing' ? 'Đang chờ HTML và tài nguyên offline đầu tiên.' : 'Đang chuẩn bị danh sách hóa đơn...'}</p>
    <footer><small>{progress.processed - progress.failed} {kind.toUpperCase()}{progress.failed ? ` · ${progress.failed} lỗi` : ''}</small><button type="button" disabled={['completed', 'stopped', 'failed', 'stopping'].includes(progress.status)} onClick={() => void lifecycle.stop(kind)}>Dừng {kind.toUpperCase()}</button></footer>
  </article>;
}

export function XmlHtmlPage({ accounts, selectedConnectionIds, onSelectAccount, onSelectAccounts, folder, onFolder, lifecycle, selection, onSelectionChange, pdfConcurrency }: {
  accounts: AccountConnection[];
  selectedConnectionIds: string[];
  onSelectAccount(id: string): void;
  onSelectAccounts(ids: string[]): void;
  folder: string;
  onFolder(value: string): void;
  lifecycle: ArtifactDownloadLifecycle;
  selection: ArtifactSelectionState;
  onSelectionChange(value: ArtifactSelectionState): void;
  pdfConcurrency: number;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [kinds, setKinds] = useState<InvoiceArtifactKind[]>(['xml', 'html']);
  const [snapshots, setSnapshots] = useState<Record<string, ArtifactAccountSnapshot>>({});
  const [snapshotState, setSnapshotState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<RowStatus | ''>('');
  const [page, setPage] = useState(1);
  const [feedback, setFeedback] = useState<string | null>(null);
  const generation = useRef(0);
  const accountIds = useMemo(() => accounts.map((account) => account.connection_id), [accounts]);

  useEffect(() => {
    if (!menuOpen) return;
    const outside = (event: PointerEvent) => { if (event.target instanceof Element && !event.target.closest('.artifact-direction-wrap')) setMenuOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [menuOpen]);

  useEffect(() => {
    const token = ++generation.current;
    if (!accountIds.length || !selection.directions.length) { setSnapshots({}); setSnapshotState('ready'); return; }
    setSnapshotState('loading');
    const timer = window.setTimeout(() => {
      void loadArtifactSnapshots({ connection_ids: accountIds, directions: selection.directions, date_from: selection.dateFrom, date_to: selection.dateTo }).then((items) => {
        if (token !== generation.current) return;
        setSnapshots(Object.fromEntries(items.map((item) => [item.connection_id, item])));
        setSnapshotState('ready');
      }).catch(() => { if (token === generation.current) setSnapshotState('error'); });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [accountIds, lifecycle.status?.status, selection.dateFrom, selection.dateTo, selection.directions]);

  const rows = accounts.map((account) => {
    const task = lifecycle.status?.accounts[account.connection_id];
    const snapshot = task ?? snapshots[account.connection_id];
    const status: RowStatus = task?.status === 'downloading' ? 'downloading' : task?.status === 'completed' ? 'completed' : task?.status === 'error' ? 'error' : task?.status === 'stopped' ? 'stopped' : snapshot?.ready ? 'ready' : 'not_ready';
    return { account, snapshot, status };
  });
  const filteredRows = rows.filter(({ account, status }) => {
    const term = search.trim().toLocaleLowerCase('vi');
    return (!term || `${account.username} ${account.company_name ?? ''}`.toLocaleLowerCase('vi').includes(term)) && (!statusFilter || status === statusFilter);
  });
  const { currentPage, totalPages, start, end } = pageBounds(filteredRows.length, page, ACCOUNT_PAGE_SIZE);
  const pageRows = filteredRows.slice(start, end);
  const tokens = paginationTokens(totalPages, currentPage);
  const filteredIds = filteredRows.map((row) => row.account.connection_id);
  const selectedFiltered = filteredIds.filter((id) => selectedConnectionIds.includes(id));
  const selectedSnapshots = selectedConnectionIds.map((id) => snapshots[id]).filter(Boolean);
  const coverageReady = selectedSnapshots.length === selectedConnectionIds.length && selectedSnapshots.every((item) => item.ready);
  useEffect(() => { if (page !== currentPage) setPage(currentPage); }, [currentPage, page]);

  function toggleDirection(direction: InvoiceDirection) {
    const directions = selection.directions.includes(direction) ? selection.directions.filter((item) => item !== direction) : [...selection.directions, direction];
    if (!directions.length) { setFeedback('Vui lòng giữ ít nhất một lựa chọn Mua vào hoặc Bán ra.'); return; }
    onSelectionChange({ ...selection, directions });
  }
  function toggleKind(kind: InvoiceArtifactKind) { setKinds((current) => current.includes(kind) ? current.filter((item) => item !== kind) : [...current, kind]); }
  async function chooseFolder() { const selected = await window.miaRuntime?.artifacts.selectDirectory(); if (selected) onFolder(selected); }
  async function startDownload() {
    if (!selectedConnectionIds.length) { setFeedback('Vui lòng chọn ít nhất một tài khoản.'); return; }
    if (!kinds.length) { setFeedback('Vui lòng chọn ít nhất một định dạng XML, HTML hoặc PDF.'); return; }
    if (!folder.trim()) { setFeedback('Vui lòng chọn thư mục lưu trữ.'); return; }
    if (!coverageReady) { setFeedback(`Khoảng thời gian ${formatDate(selection.dateFrom)} - ${formatDate(selection.dateTo)} chưa được đồng bộ đầy đủ. Vui lòng sang Quản lý HĐĐT để đồng bộ trước.`); return; }
    await lifecycle.start({ destination: folder, connection_ids: selectedConnectionIds, directions: selection.directions, kinds, date_from: selection.dateFrom, date_to: selection.dateTo, pdf_concurrency: pdfConcurrency });
  }

  return <section className="artifact-account-page" aria-labelledby="artifact-title">
    <header className="artifact-account-header"><h1 id="artifact-title">XML/HTML/PDF</h1><p>Tải artifact từ dữ liệu Tổng quan đã đồng bộ trong MIA WT.</p></header>
    <section className="artifact-toolbar-card" aria-label="Thiết lập tải artifact">
      <DateRangePicker disabled={lifecycle.active} dateFrom={selection.dateFrom} dateTo={selection.dateTo} onChange={(dateFrom, dateTo) => onSelectionChange({ ...selection, dateFrom, dateTo })} />
      <div className="artifact-direction-wrap"><button className="compact-select" type="button" aria-expanded={menuOpen} onClick={() => setMenuOpen((value) => !value)}>{selection.directions.length === 2 ? 'Mua vào và Bán ra' : selection.directions[0] === 'sold' ? 'Bán ra' : 'Mua vào'} <i className="chevron" /></button>{menuOpen ? <div className="figma-option-menu artifact-direction-menu"><OptionCheck checked={selection.directions.includes('purchase')} label="Mua vào" onChange={() => toggleDirection('purchase')} /><OptionCheck checked={selection.directions.includes('sold')} label="Bán ra" onChange={() => toggleDirection('sold')} /></div> : null}</div>
      <div className="artifact-kind-options" aria-label="Định dạng tải xuống">{(['xml', 'html', 'pdf'] as const).map((kind) => <label key={kind} data-active={kinds.includes(kind)}><input type="checkbox" checked={kinds.includes(kind)} disabled={lifecycle.active} onChange={() => toggleKind(kind)} /><span>{kind.toUpperCase()}</span></label>)}</div>
      <StorageFolderPicker value={folder} onChange={onFolder} onBrowse={chooseFolder} ariaLabel="Thư mục lưu trữ XML HTML PDF" />
    </section>
    <section className="artifact-account-content">
      <div className="artifact-account-filters"><div><label className="search-box"><img src={searchIcon} alt="" /><input aria-label="Tìm kiếm tài khoản artifact" placeholder="Tìm kiếm MST, Tên công ty..." value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} /></label><select className="status-filter" aria-label="Lọc trạng thái artifact" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value as RowStatus | ''); setPage(1); }}><option value="">Tất cả trạng thái</option><option value="ready">Sẵn sàng tải</option><option value="not_ready">Chưa đồng bộ</option><option value="downloading">Đang tải</option><option value="completed">Hoàn thành</option><option value="error">Lỗi tài khoản</option><option value="stopped">Đã dừng</option></select></div><div><button className="primary-download-button artifact-download-button" type="button" disabled={lifecycle.active || !selectedConnectionIds.length || !kinds.length || snapshotState !== 'ready'} onClick={() => void startDownload()}>Tải xuống</button><button className="stop-button artifact-global-stop" type="button" disabled={!lifecycle.active} onClick={() => void lifecycle.stop()}>Dừng tải</button></div></div>
      {lifecycle.status?.current_account_id ? <div className="artifact-progress-cards" data-count={Object.keys(lifecycle.status.formats).length}>{kinds.map((kind) => <ProgressCard key={kind} kind={kind} lifecycle={lifecycle} />)}</div> : null}
      <div className="artifact-account-table">
        <div className="artifact-account-row artifact-account-row--head"><button className="selection-button" type="button" aria-label="Chọn tất cả tài khoản artifact đã lọc" onClick={() => onSelectAccounts(selectedFiltered.length === filteredIds.length ? selectedConnectionIds.filter((id) => !filteredIds.includes(id)) : [...new Set([...selectedConnectionIds, ...filteredIds])])}><SelectionBox checked={filteredIds.length > 0 && selectedFiltered.length === filteredIds.length} indeterminate={selectedFiltered.length > 0 && selectedFiltered.length < filteredIds.length} /></button><span>MST</span><span>Tên công ty</span><span>Số lượng</span><span>Trạng thái</span><span>Tiến trình</span><span>Tác vụ</span></div>
        <div className="artifact-account-body">{pageRows.map(({ account, snapshot, status }) => {
          const formats = lifecycle.status?.current_account_id === account.connection_id ? lifecycle.status.formats : null;
          const progress = formats ? Math.round(Object.values(formats).reduce((sum, item) => sum + (item?.percent ?? 0), 0) / Math.max(1, Object.keys(formats).length)) : status === 'completed' ? 100 : 0;
          const statusText = status === 'ready' ? 'Sẵn sàng tải' : status === 'not_ready' ? `Chưa đồng bộ từ ${formatDate(selection.dateFrom)} - ${formatDate(selection.dateTo)}` : status === 'downloading' ? 'Đang tải' : status === 'completed' ? 'Hoàn thành' : status === 'error' ? 'Lỗi tài khoản' : 'Đã dừng';
          return <div className="artifact-account-row" data-status={status} key={account.connection_id}><button className="selection-button" type="button" aria-label={`Chọn ${account.username}`} onClick={() => onSelectAccount(account.connection_id)}><SelectionBox checked={selectedConnectionIds.includes(account.connection_id)} /></button><span>{account.username}</span><strong title={account.company_name ?? ''}>{account.company_name || '—'}</strong><Quantity snapshot={snapshot} /><span className="artifact-account-status" data-status={status}>{statusText}</span><span className="artifact-row-progress"><i><b style={{ width: `${progress}%` }} /></i><em>{progress}%</em></span><span className="artifact-row-action">—</span></div>;
        })}</div>
        {snapshotState === 'loading' && !pageRows.length ? <div className="artifact-table-state">Đang kiểm tra dữ liệu cục bộ...</div> : null}{snapshotState === 'error' ? <div className="artifact-table-state">Không thể kiểm tra trạng thái artifact.</div> : null}{snapshotState === 'ready' && !pageRows.length ? <div className="artifact-table-state">Không có tài khoản phù hợp.</div> : null}
      </div>
      <footer className="pagination"><span>{`Hiển thị ${filteredRows.length ? start + 1 : 0}–${end} trên tổng ${filteredRows.length} tài khoản`}</span><div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>‹</button>{tokens.map((token, index) => token === 'ellipsis' ? <span key={`ellipsis-${index}`}>...</span> : <button type="button" key={token} data-active={currentPage === token} onClick={() => setPage(token)}>{token}</button>)}<button type="button" aria-label="Trang sau" disabled={currentPage === totalPages} onClick={() => setPage(currentPage + 1)}>›</button></div></footer>
      <div className="artifact-open-folder"><button type="button" disabled={!folder.trim()} onClick={() => void window.miaRuntime?.artifacts.openDirectory(folder).catch(() => setFeedback('Không thể mở thư mục lưu trữ.'))}>Mở thư mục</button></div>
    </section>
    {(feedback || lifecycle.message) ? <NoticeDialog kind={lifecycle.status?.status === 'completed' ? 'success' : 'notice'} message={feedback || lifecycle.message || ''} onClose={() => { setFeedback(null); lifecycle.clearMessage(); }} /> : null}
  </section>;
}
