import { useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { pageBounds, paginationTokens } from '../../components/pagination-utils';
import { StorageFolderPicker } from '../../components/StorageFolderPicker';
import { NoticeDialog } from '../../components/NoticeDialog';
import searchIcon from '../../assets/figma/search.png';
import backIcon from '../../assets/figma/back.png';
import { OptionCheck } from '../../components/OptionCheck';
import { DownloadIcon, StopIcon } from '../../components/InvoiceActionIcons';
import type { AccountConnection, InvoiceDirection } from '../../lib/api/contracts';
import type { ArtifactAccountSnapshot, ArtifactCoverageAccount, ArtifactFailureRecord, ArtifactSnapshotRequest, InvoiceArtifactKind } from '../../lib/runtime-bridge';
import type { ArtifactDownloadLifecycle } from './use-artifact-download-lifecycle';
import '../../styles/xml-html.css';
import '../../styles/results-enhancements.css';
import '../../styles/results-luxury.css';

const ACCOUNT_PAGE_SIZE = 20;
type RowStatus = 'checking' | 'ready' | 'not_ready' | 'downloading' | 'completed' | 'error' | 'stopped';

export interface ArtifactSelectionState {
  dateFrom: string;
  dateTo: string;
  direction: InvoiceDirection;
}

export async function loadArtifactSnapshots(request: ArtifactSnapshotRequest) {
  const chunks: string[][] = [];
  for (let index = 0; index < request.connection_ids.length; index += 50) chunks.push(request.connection_ids.slice(index, index + 50));
  const results = await Promise.all(chunks.map((connection_ids) => window.miaRuntime!.artifacts.snapshot({ ...request, connection_ids })));
  return results.flatMap((result) => result.accounts);
}

export async function loadArtifactCoverage(request: ArtifactSnapshotRequest) {
  const chunks: string[][] = [];
  for (let index = 0; index < request.connection_ids.length; index += 50) chunks.push(request.connection_ids.slice(index, index + 50));
  const coverage = window.miaRuntime!.artifacts.coverage;
  const results = await Promise.all(chunks.map(async (connection_ids) => {
    const value = { ...request, connection_ids };
    if (typeof coverage === 'function') return (await coverage(value)).accounts;
    return (await window.miaRuntime!.artifacts.snapshot(value)).accounts;
  }));
  return results.flatMap((items) => items) as ArtifactCoverageAccount[];
}

function SelectionBox({ checked, indeterminate = false }: { checked: boolean; indeterminate?: boolean }) {
  return <span className="selection-box" data-checked={checked || indeterminate}>{indeterminate ? '−' : checked ? '✓' : ''}</span>;
}

function formatDate(value: string) {
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}/${month}/${year}` : value;
}

function Quantity({ snapshot }: { snapshot?: ArtifactAccountSnapshot }) {
  const total = snapshot?.total ?? 0;
  return <span className="artifact-quantity">{(['xml', 'html', 'pdf'] as const).map((kind) => <span key={kind}><strong>{kind.toUpperCase()}</strong> {(snapshot?.cached[kind] ?? 0).toLocaleString('vi-VN')}/{total.toLocaleString('vi-VN')}</span>)}</span>;
}

function missingCoverageText(snapshot: ArtifactAccountSnapshot | undefined, dateFrom: string, dateTo: string) {
  const ranges = snapshot?.missing_ranges ?? [];
  if (!ranges.length) return `Chưa đồng bộ từ ${formatDate(dateFrom)} - ${formatDate(dateTo)}`;
  if (ranges.length === 1) return `Chưa đồng bộ từ ${formatDate(ranges[0].date_from)} - ${formatDate(ranges[0].date_to)}`;
  return `Chưa đồng bộ: ${ranges.map((range) => `${formatDate(range.date_from)} - ${formatDate(range.date_to)}`).join(', ')}`;
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
  return <article className="artifact-progress-card data-card" data-kind={kind} data-status={progress.status}>
    <header><FormatIcon kind={kind} /><h2>{kind.toUpperCase()}</h2><span>{progress.processed.toLocaleString('vi-VN')} / {progress.total.toLocaleString('vi-VN')}</span><strong>{Math.round(progress.percent)}%</strong></header>
    <div className="artifact-progress-status">{label}</div>
    <div className="artifact-progress-track"><span style={{ width: `${Math.max(0, Math.min(100, progress.percent))}%` }} /></div>
  </article>;
}

const FAILURE_PAGE_SIZE = 50;

function failureDate(value: string) {
  const match = String(value || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : value || '—';
}

function ArtifactFailureView({ account, lifecycle, onBack }: {
  account: AccountConnection;
  lifecycle: ArtifactDownloadLifecycle;
  onBack(): void;
}) {
  const [items, setItems] = useState<ArtifactFailureRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const taskId = lifecycle.status?.task_id;
  const failureCount = lifecycle.status?.accounts[account.connection_id]?.failure_count ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / FAILURE_PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const tokens = paginationTokens(totalPages, currentPage);

  useEffect(() => {
    let current = true;
    if (!taskId) { setItems([]); setTotal(0); setLoading(false); return; }
    setLoading(true);
    void window.miaRuntime!.artifacts.batchFailures({
      task_id: taskId, connection_id: account.connection_id,
      offset: (currentPage - 1) * FAILURE_PAGE_SIZE, limit: FAILURE_PAGE_SIZE,
    }).then((result) => {
      if (!current) return;
      setItems(result.items);
      setTotal(result.total);
      setLoading(false);
    }).catch(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [account.connection_id, currentPage, failureCount, taskId]);

  const gridTemplateColumns = '140px 150px 150px 150px 180px 220px 150px minmax(300px, 1fr)';
  const first = total ? (currentPage - 1) * FAILURE_PAGE_SIZE + 1 : 0;
  const last = Math.min(total, currentPage * FAILURE_PAGE_SIZE);
  return <section className="results-page results-page--figma artifact-failure-page" aria-label="Danh sách hóa đơn lỗi">
    <button className="results-back" type="button" onClick={onBack}><img src={backIcon} alt="" /> Quay lại XML/HTML/PDF</button>
    <header className="results-header results-header--figma"><div><h1>Danh sách hóa đơn lỗi</h1><p>{account.username} · {account.company_name || 'Chưa có tên công ty'}</p></div></header>
    <div className="results-table results-table--figma results-table--excel-schema" tabIndex={0} aria-label="Bảng hóa đơn không tạo được file">
      <div className="results-row results-row--header" style={{ gridTemplateColumns }}><span>Ngày lập</span><span>Ký hiệu mẫu số</span><span>Ký hiệu hóa đơn</span><span>Số hóa đơn</span><span>MST đối tác</span><span>Tên đối tác</span><span>Định dạng</span><span>Lỗi</span></div>
      {items.map((item) => <div className="results-row" style={{ gridTemplateColumns }} key={item.invoice_key}><span>{failureDate(item.date)}</span><span>{item.khmshdon || '—'}</span><span>{item.khhdon || '—'}</span><span>{item.shdon || '—'}</span><span>{item.nbmst || '—'}</span><span title={item.partner_name}>{item.partner_name || '—'}</span><span>{item.affected_formats.map((kind) => kind.toUpperCase()).join(', ')}</span><span title={item.message}>{item.message || 'Không thể tạo file'}</span></div>)}
      {loading ? <div className="results-state" role="status">Đang tải danh sách...</div> : !items.length ? <div className="results-state results-empty">Không có hóa đơn lỗi.</div> : null}
    </div>
    <footer className="results-pager artifact-pager"><span>Hiển thị {first}–{last} trên tổng {total} hóa đơn</span><div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>‹</button>{tokens.map((token, index) => token === 'ellipsis' ? <span key={`ellipsis-${index}`}>...</span> : <button type="button" key={token} data-active={currentPage === token} onClick={() => setPage(token)}>{token}</button>)}<button type="button" aria-label="Trang sau" disabled={currentPage === totalPages} onClick={() => setPage(currentPage + 1)}>›</button></div></footer>
  </section>;
}

export function XmlHtmlPage({ accounts, selectedConnectionIds, onSelectAccount, onSelectAccounts, folder, onFolder, lifecycle, selection, onSelectionChange, coverageRevision, pdfConcurrency }: {
  accounts: AccountConnection[];
  selectedConnectionIds: string[];
  onSelectAccount(id: string): void;
  onSelectAccounts(ids: string[]): void;
  folder: string;
  onFolder(value: string): void;
  lifecycle: ArtifactDownloadLifecycle;
  selection: ArtifactSelectionState;
  onSelectionChange(value: ArtifactSelectionState): void;
  coverageRevision: number;
  pdfConcurrency: number;
}) {
  const [menu, setMenu] = useState<'direction' | 'formats' | null>(null);
  const [kinds, setKinds] = useState<InvoiceArtifactKind[]>(['xml', 'html']);
  const [snapshots, setSnapshots] = useState<Record<string, ArtifactAccountSnapshot>>({});
  const [snapshotState, setSnapshotState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [cacheRevision, setCacheRevision] = useState(0);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<RowStatus | ''>('');
  const [page, setPage] = useState(1);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [failureAccountId, setFailureAccountId] = useState<string | null>(null);
  const generation = useRef(0);
  const cacheGeneration = useRef(0);
  const coverageFingerprint = useRef('');
  const snapshotSelectionKey = useRef('');
  const accountIds = useMemo(() => accounts.map((account) => account.connection_id), [accounts]);

  useEffect(() => {
    if (!menu) return;
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Element && !event.target.closest('.select-wrap')) setMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenu(null); };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => { document.removeEventListener('pointerdown', closeOutside); document.removeEventListener('keydown', closeOnEscape); };
  }, [menu]);

  useEffect(() => {
    const token = ++generation.current;
    if (!accountIds.length) { setSnapshots({}); setSnapshotState('ready'); return; }
    // Never render a snapshot from the previous account/range/direction while
    // the authoritative persisted coverage for the new selection is loading.
    const selectionKey = JSON.stringify([accountIds, selection.dateFrom, selection.dateTo, selection.direction]);
    const selectionChanged = snapshotSelectionKey.current !== selectionKey;
    if (selectionChanged) {
      snapshotSelectionKey.current = selectionKey;
      coverageFingerprint.current = '';
      setSnapshots({});
    }
    setSnapshotState('loading');
    void loadArtifactCoverage({ connection_ids: accountIds, directions: [selection.direction], date_from: selection.dateFrom, date_to: selection.dateTo }).then((items) => {
      if (token !== generation.current) return;
      const fingerprint = JSON.stringify(items.map((item) => [item.connection_id, item.ready, item.missing_ranges]));
      setSnapshots((current) => Object.fromEntries(items.map((item) => [item.connection_id, {
        ...item,
        total: current[item.connection_id]?.total ?? 0,
        cached: current[item.connection_id]?.cached ?? { xml: 0, html: 0, pdf: 0 },
      }])));
      setSnapshotState('ready');
      if (coverageFingerprint.current !== fingerprint) {
        coverageFingerprint.current = fingerprint;
        setCacheRevision((current) => current + 1);
      }
    }).catch(() => { if (token === generation.current) setSnapshotState('error'); });
    return () => { if (token === generation.current) generation.current += 1; };
  }, [accountIds, coverageRevision, lifecycle.status?.status, selection.dateFrom, selection.dateTo, selection.direction]);

  useEffect(() => {
    if (!accountIds.length || cacheRevision < 1) return;
    const token = ++cacheGeneration.current;
    void loadArtifactSnapshots({ connection_ids: accountIds, directions: [selection.direction], date_from: selection.dateFrom, date_to: selection.dateTo }).then((items) => {
      if (token !== cacheGeneration.current) return;
      setSnapshots((current) => {
        const merged = { ...current };
        for (const item of items) {
          merged[item.connection_id] = {
            ...item,
            ready: current[item.connection_id]?.ready ?? item.ready,
            missing_ranges: current[item.connection_id]?.missing_ranges ?? item.missing_ranges,
          };
        }
        return merged;
      });
    }).catch(() => undefined);
    return () => { if (token === cacheGeneration.current) cacheGeneration.current += 1; };
  }, [accountIds, cacheRevision, selection.dateFrom, selection.dateTo, selection.direction]);

  const rows = accounts.map((account) => {
    // A terminal download snapshot belongs to the direction/range used by that
    // completed task. Once it is no longer active, persisted coverage is again
    // authoritative so changing direction cannot reuse stale task totals.
    const task = lifecycle.active ? lifecycle.status?.accounts[account.connection_id] : undefined;
    const failureCount = lifecycle.status?.accounts[account.connection_id]?.failure_count ?? 0;
    const snapshot = task ?? snapshots[account.connection_id];
    const status: RowStatus = task?.status === 'downloading' ? 'downloading'
      : task?.status === 'completed' ? 'completed'
        : task?.status === 'error' ? 'error'
          : task?.status === 'stopped' ? 'stopped'
            : snapshotState === 'loading' ? 'checking'
              : snapshotState === 'error' ? 'error'
                : snapshot?.ready ? 'ready' : 'not_ready';
    return { account, snapshot, status, failureCount };
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
  const coverageReady = snapshotState === 'ready' && selectedSnapshots.length === selectedConnectionIds.length && selectedSnapshots.every((item) => item.ready);
  useEffect(() => { if (page !== currentPage) setPage(currentPage); }, [currentPage, page]);

  function toggleKind(kind: InvoiceArtifactKind) { setKinds((current) => current.includes(kind) ? current.filter((item) => item !== kind) : [...current, kind]); }
  async function chooseFolder() { const selected = await window.miaRuntime?.artifacts.selectDirectory(); if (selected) onFolder(selected); }
  async function startDownload() {
    if (!selectedConnectionIds.length) { setFeedback('Vui lòng chọn ít nhất một tài khoản.'); return; }
    if (!kinds.length) { setFeedback('Vui lòng chọn ít nhất một định dạng XML, HTML hoặc PDF.'); return; }
    if (!folder.trim()) { setFeedback('Vui lòng chọn thư mục lưu trữ.'); return; }
    if (!coverageReady) { setFeedback(`Khoảng thời gian ${formatDate(selection.dateFrom)} - ${formatDate(selection.dateTo)} chưa được đồng bộ đầy đủ. Vui lòng sang Quản lý HĐĐT để đồng bộ trước.`); return; }
    await lifecycle.start({ destination: folder, connection_ids: selectedConnectionIds, directions: [selection.direction], kinds, date_from: selection.dateFrom, date_to: selection.dateTo, pdf_concurrency: pdfConcurrency });
  }

  const failureAccount = failureAccountId
    ? accounts.find((account) => account.connection_id === failureAccountId)
    : undefined;
  if (failureAccount) {
    return <ArtifactFailureView account={failureAccount} lifecycle={lifecycle} onBack={() => setFailureAccountId(null)} />;
  }

  return <section className="artifact-account-page invoice-page" aria-labelledby="artifact-title">
    <header className="artifact-account-header"><h1 id="artifact-title">XML/HTML/PDF</h1><p>Tải XML, HTML và PDF từ dữ liệu hóa đơn đã đồng bộ.</p></header>
    <section className="artifact-toolbar-card toolbar-card" aria-label="Thiết lập tải XML HTML PDF">
      <DateRangePicker disabled={lifecycle.active} dateFrom={selection.dateFrom} dateTo={selection.dateTo} onChange={(dateFrom, dateTo) => onSelectionChange({ ...selection, dateFrom, dateTo })} />
      <div className="select-wrap artifact-direction-select">
        <button className="compact-select compact-select--direction" type="button" disabled={lifecycle.active} aria-expanded={menu === 'direction'} onClick={() => setMenu(menu === 'direction' ? null : 'direction')}>{selection.direction === 'purchase' ? 'Mua vào' : 'Bán ra'} <i className="chevron" /></button>
        {menu === 'direction' ? <div className="figma-option-menu figma-direction-menu" aria-label="Loại hóa đơn">
          <OptionCheck checked={selection.direction === 'purchase'} label="Mua vào" onChange={() => { onSelectionChange({ ...selection, direction: 'purchase' }); setMenu(null); }} />
          <OptionCheck checked={selection.direction === 'sold'} label="Bán ra" onChange={() => { onSelectionChange({ ...selection, direction: 'sold' }); setMenu(null); }} />
        </div> : null}
      </div>
      <div className="select-wrap artifact-format-select">
        <button className="compact-select" type="button" disabled={lifecycle.active} aria-expanded={menu === 'formats'} onClick={() => setMenu(menu === 'formats' ? null : 'formats')}>{kinds.length ? kinds.map((kind) => kind.toUpperCase()).join(' + ') : 'Chọn định dạng'} <i className="chevron" /></button>
        {menu === 'formats' ? <div className="figma-option-menu artifact-format-menu" aria-label="Định dạng tải xuống">{(['xml', 'html', 'pdf'] as const).map((kind) => <OptionCheck key={kind} checked={kinds.includes(kind)} label={kind.toUpperCase()} onChange={() => toggleKind(kind)} />)}</div> : null}
      </div>
      <StorageFolderPicker className="invoice-export-folder" value={folder} onChange={onFolder} onBrowse={chooseFolder} ariaLabel="Thư mục lưu trữ XML HTML PDF" />
    </section>
    <section className="artifact-account-content">
      <div className="artifact-account-filters filters"><div className="filters-left"><label className="search-box"><img src={searchIcon} alt="" /><input aria-label="Tìm kiếm tài khoản tải xuống" placeholder="Tìm kiếm MST, Tên công ty..." value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} /></label><select className="status-filter" aria-label="Lọc trạng thái tải xuống" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value as RowStatus | ''); setPage(1); }}><option value="">Tất cả trạng thái</option><option value="checking">Đang kiểm tra</option><option value="ready">Sẵn sàng tải</option><option value="not_ready">Chưa đồng bộ</option><option value="downloading">Đang tải</option><option value="completed">Hoàn thành</option><option value="error">Lỗi tài khoản</option><option value="stopped">Đã dừng</option></select></div><div className="invoice-filter-actions"><button className="sync-button artifact-download-button" type="button" disabled={lifecycle.active || !selectedConnectionIds.length || !kinds.length || snapshotState !== 'ready'} onClick={() => void startDownload()}><DownloadIcon />Tải xuống</button><button className="stop-button artifact-global-stop" type="button" disabled={!lifecycle.active} onClick={() => void lifecycle.stop()}><StopIcon />Dừng tải</button></div></div>
      {lifecycle.status?.current_account_id ? <div className="artifact-progress-cards" data-count={Object.keys(lifecycle.status.formats).length}>{kinds.map((kind) => <ProgressCard key={kind} kind={kind} lifecycle={lifecycle} />)}</div> : null}
      <div className="artifact-account-table data-card">
        <div className="artifact-account-row artifact-account-row--head table-header table-grid"><button className="selection-button" type="button" aria-label="Chọn tất cả tài khoản đã lọc" onClick={() => onSelectAccounts(selectedFiltered.length === filteredIds.length ? selectedConnectionIds.filter((id) => !filteredIds.includes(id)) : [...new Set([...selectedConnectionIds, ...filteredIds])])}><SelectionBox checked={filteredIds.length > 0 && selectedFiltered.length === filteredIds.length} indeterminate={selectedFiltered.length > 0 && selectedFiltered.length < filteredIds.length} /></button><span>MST</span><span>Tên công ty</span><span>Số lượng</span><span>Trạng thái</span><span>Tiến trình</span><span>Tác vụ</span></div>
        <div className="artifact-account-body table-body">{pageRows.map(({ account, snapshot, status, failureCount }) => {
          const formats = lifecycle.status?.current_account_id === account.connection_id ? lifecycle.status.formats : null;
          const progress = formats ? Math.round(Object.values(formats).reduce((sum, item) => sum + (item?.percent ?? 0), 0) / Math.max(1, Object.keys(formats).length)) : status === 'completed' ? 100 : 0;
          const statusText = status === 'checking' ? 'Đang kiểm tra'
            : status === 'ready' ? 'Sẵn sàng tải'
              : status === 'not_ready' ? missingCoverageText(snapshot, selection.dateFrom, selection.dateTo)
                : status === 'downloading' ? 'Đang tải'
                  : status === 'completed' ? 'Hoàn thành'
                    : status === 'error' ? 'Lỗi tài khoản' : 'Đã dừng';
          return <div className="artifact-account-row table-row table-grid" data-status={status} key={account.connection_id}><button className="selection-button" type="button" aria-label={`Chọn ${account.username}`} onClick={() => onSelectAccount(account.connection_id)}><SelectionBox checked={selectedConnectionIds.includes(account.connection_id)} /></button><span>{account.username}</span><strong title={account.company_name ?? ''}>{account.company_name || '—'}</strong><Quantity snapshot={snapshot} /><span className="artifact-account-status" data-status={status}>{statusText}</span><span className="artifact-row-progress"><i><b style={{ width: `${progress}%` }} /></i><em>{progress}%</em></span><span className="artifact-row-action row-action-group">{failureCount > 0 ? <button className="row-result-button" type="button" onClick={() => setFailureAccountId(account.connection_id)}>Danh sách hóa đơn lỗi</button> : <span className="row-action-placeholder">—</span>}</span></div>;
        })}</div>
        {snapshotState === 'loading' && !pageRows.length ? <div className="artifact-table-state">Đang kiểm tra dữ liệu cục bộ...</div> : null}{snapshotState === 'error' ? <div className="artifact-table-state">Không thể kiểm tra trạng thái tải xuống.</div> : null}{snapshotState === 'ready' && !pageRows.length ? <div className="artifact-table-state">Không có tài khoản phù hợp.</div> : null}
      </div>
      <footer className="pagination"><span>{`Hiển thị ${filteredRows.length ? start + 1 : 0}–${end} trên tổng ${filteredRows.length} tài khoản`}</span><div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>‹</button>{tokens.map((token, index) => token === 'ellipsis' ? <span key={`ellipsis-${index}`}>...</span> : <button type="button" key={token} data-active={currentPage === token} onClick={() => setPage(token)}>{token}</button>)}<button type="button" aria-label="Trang sau" disabled={currentPage === totalPages} onClick={() => setPage(currentPage + 1)}>›</button></div></footer>
    </section>
    {(feedback || lifecycle.message) ? <NoticeDialog kind={lifecycle.status?.status === 'completed' ? 'success' : 'notice'} message={feedback || lifecycle.message || ''} onClose={() => { setFeedback(null); lifecycle.clearMessage(); }} /> : null}
  </section>;
}
