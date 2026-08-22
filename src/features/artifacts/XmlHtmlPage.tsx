import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import { paginationTokens } from '../../components/pagination-utils';
import { StorageFolderPicker } from '../../components/StorageFolderPicker';
import { NoticeDialog } from '../../components/NoticeDialog';
import previousIcon from '../../assets/figma/artifact-previous.svg';
import nextIcon from '../../assets/figma/artifact-next.svg';
import syncIcon from '../../assets/figma/sync.png';
import checkIcon from '../../assets/figma/check.svg';
import type { AccountConnection, InvoiceDirection, InvoiceQueryType } from '../../lib/api/contracts';
import type { LocalResultPage, OverviewResult } from '../../lib/runtime-bridge';
import { formatSourceJobProgress } from '../jobs/job-progress-presentation';
import type { InvoiceArtifactKind, XmlHtmlDownloadLifecycle } from './use-xml-html-download-lifecycle';
import '../../styles/xml-html.css';

const PAGE_SIZE = 50;
const DEFAULT_RANGE = { dateFrom: '2023-10-01', dateTo: '2023-10-31' };
export const XML_HTML_GRID = '55px 130px 130px 130px 160px minmax(300px, 1fr) 150px 140px 150px';

function text(value: unknown) { return value === null || value === undefined ? '' : String(value); }
function money(value: unknown) {
  const amount = Number(value);
  return Number.isFinite(amount) ? new Intl.NumberFormat('vi-VN').format(amount) : text(value);
}
export function artifactRowKey(row: OverviewResult, queryType: InvoiceQueryType) {
  const f = row.fields;
  return [row.direction, queryType, text(f.nbmst), text(f.khhdon), text(f.shdon), text(f.khmshdon)].join('|');
}
export type XmlHtmlRowStatus = 'Chưa xử lý' | 'Đang tải' | 'Hoàn tất' | 'Lỗi';
export function resolveArtifactRowStatus(value: {
  key: string;
  selectedKinds: InvoiceArtifactKind[];
  persistedComplete: boolean;
  completedKeys: Record<InvoiceArtifactKind, Set<string>>;
  batchRelevant: boolean;
  terminal: boolean;
  downloadActive: boolean;
  activeKeys: Set<string>;
}): XmlHtmlRowStatus {
  const batchComplete = value.selectedKinds.every((kind) => value.completedKeys[kind].has(value.key));
  if (value.persistedComplete || batchComplete) return 'Hoàn tất';
  if (value.batchRelevant && value.terminal) return 'Lỗi';
  if (value.batchRelevant && value.downloadActive && value.activeKeys.has(value.key)) return 'Đang tải';
  return 'Chưa xử lý';
}
export function artifactCounterText(
  kinds: InvoiceArtifactKind[], total: number,
  completedKeys: Record<InvoiceArtifactKind, Set<string>>,
) {
  return kinds.map((kind) => `${kind.toUpperCase()} ${completedKeys[kind].size}/${total}`).join(' · ');
}
function sourceFileStem(row: OverviewResult) {
  return [row.fields.khmshdon, row.fields.khhdon, row.fields.shdon, row.fields.nbmst]
    .map((value) => text(value).trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')).join('_');
}

function StopIcon() {
  return <svg className="stop-button-icon" viewBox="0 0 18 18" aria-hidden="true"><rect x="4" y="4" width="10" height="10" rx="1.5" /></svg>;
}
function DownloadIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m0 0 4-4m-4 4-4-4M5 16v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" /></svg>;
}

export function XmlHtmlPage({ accounts, selectedConnectionIds, folder, onFolder, lifecycle }: {
  accounts: AccountConnection[];
  selectedConnectionIds: string[];
  folder: string;
  onFolder(value: string): void;
  lifecycle: XmlHtmlDownloadLifecycle;
}) {
  const initialRange = useRef(readLastSyncDateRange() ?? DEFAULT_RANGE).current;
  const [connectionId, setConnectionId] = useState(selectedConnectionIds[0] ?? accounts[0]?.connection_id ?? '');
  const [dateFrom, setDateFrom] = useState(initialRange.dateFrom);
  const [dateTo, setDateTo] = useState(initialRange.dateTo);
  const [direction, setDirection] = useState<InvoiceDirection | ''>('');
  const [queryType, setQueryType] = useState<InvoiceQueryType>('query');
  const [forceRefresh, setForceRefresh] = useState(false);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [kinds, setKinds] = useState<InvoiceArtifactKind[]>(['xml', 'html']);
  const [items, setItems] = useState<OverviewResult[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [pagination, setPagination] = useState<LocalResultPage<OverviewResult>['pagination'] | null>(null);
  const [page, setPage] = useState(1);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [availableFiles, setAvailableFiles] = useState(new Set<string>());
  const [feedback, setFeedback] = useState('');
  const cache = useRef(new Map<number, LocalResultPage<OverviewResult>>());
  const cursors = useRef(new Map<number, string | null>([[1, null]]));
  const generation = useRef(0);

  useEffect(() => { if (!connectionId && accounts[0]) setConnectionId(accounts[0].connection_id); }, [accounts, connectionId]);
  useEffect(() => { const timer = setTimeout(() => setDebouncedSearch(search.trim()), 250); return () => clearTimeout(timer); }, [search]);

  const query = useMemo(() => ({
    connection_id: connectionId, limit: PAGE_SIZE, search: debouncedSearch,
    direction: direction || null, query_type: queryType, date_from: dateFrom, date_to: dateTo,
  }), [connectionId, dateFrom, dateTo, debouncedSearch, direction, queryType]);

  const loadPage = useCallback(async (target: number, token = generation.current) => {
    if (!connectionId || target < 1) { setItems([]); setTotalCount(0); setState('ready'); return; }
    setState('loading');
    try {
      for (let current = 1; current <= target; current += 1) {
        let result = cache.current.get(current);
        if (!result) {
          const cursor = cursors.current.get(current);
          if (cursor === undefined) return;
          result = await window.miaRuntime!.results.overview({ ...query, cursor });
          if (token !== generation.current) return;
          cache.current.set(current, result);
        }
        if (result.pagination.has_more && result.pagination.next_cursor) cursors.current.set(current + 1, result.pagination.next_cursor);
        if (current === target || !result.pagination.has_more) {
          setItems(result.items); setPagination(result.pagination);
          setTotalCount(result.total_count ?? result.items.length); setPage(current); setState('ready');
          return;
        }
      }
    } catch { if (token === generation.current) setState('error'); }
  }, [connectionId, query]);

  const refreshArtifacts = useCallback(async () => {
    if (!connectionId || !window.miaRuntime?.artifacts) return;
    const found = new Set<string>();
    await Promise.all(kinds.map(async (kind) => {
      let cursor: string | null = null;
      do {
        const result = await window.miaRuntime!.artifacts.list({
          connection_ids: [connectionId], kind, direction: direction || null, query_type: queryType,
          search: debouncedSearch, cursor, limit: 200, date_from: dateFrom, date_to: dateTo,
        });
        for (const item of result.items) found.add(`${kind}:${item.filename}`);
        cursor = result.pagination.has_more ? result.pagination.next_cursor : null;
      } while (cursor);
    }));
    setAvailableFiles(found);
  }, [connectionId, dateFrom, dateTo, debouncedSearch, direction, kinds, queryType]);

  const resetAndLoad = useCallback(() => {
    const token = ++generation.current;
    cache.current.clear(); cursors.current.clear(); cursors.current.set(1, null); setPage(1);
    void loadPage(1, token); void refreshArtifacts();
  }, [loadPage, refreshArtifacts]);
  useEffect(resetAndLoad, [resetAndLoad, lifecycle.syncRevision]);
  useEffect(() => {
    if (!lifecycle.active || lifecycle.request?.connectionId === connectionId) void refreshArtifacts();
  }, [connectionId, lifecycle.active, lifecycle.copyProgress.processed, lifecycle.job?.event_sequence, lifecycle.request?.connectionId, refreshArtifacts]);

  function toggleKind(kind: InvoiceArtifactKind) {
    setKinds((current) => current.includes(kind) ? current.filter((value) => value !== kind) : [...current, kind]);
  }
  async function chooseFolder() { const selected = await window.miaRuntime?.artifacts.selectDirectory(); if (selected) onFolder(selected); }
  function syncOverview() {
    if (!connectionId) { setFeedback('Vui lòng chọn tài khoản.'); return; }
    lifecycle.startSync({ connectionId, dateFrom, dateTo, direction: direction || null, queryType, forceRefresh });
  }
  function startDownload() {
    if (!connectionId) { setFeedback('Vui lòng chọn tài khoản.'); return; }
    if (!folder.trim()) { setFeedback('Vui lòng chọn thư mục lưu trữ.'); return; }
    if (!kinds.length) { setFeedback('Vui lòng chọn XML, HTML hoặc cả hai.'); return; }
    if (totalCount === 0) {
      setFeedback('Chưa có dữ liệu hóa đơn trong khoảng thời gian này. Vui lòng Đồng bộ dữ liệu trước.');
      return;
    }
    setFeedback('');
    lifecycle.start({ connectionId, dateFrom, dateTo, direction: direction || null, queryType, search: debouncedSearch, kinds, destination: folder, totalRows: totalCount });
  }

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));
  const tokens = paginationTokens(totalPages, page);
  const currentArtifact = lifecycle.job?.current_artifact;
  const currentKey = currentArtifact
    ? [currentArtifact.direction, currentArtifact.query_type, currentArtifact.nbmst, currentArtifact.khhdon, currentArtifact.shdon, currentArtifact.khmshdon].join('|')
    : null;
  const copyKey = lifecycle.copyProgress.artifact_key;
  const batchOnCurrentAccount = lifecycle.request?.connectionId === connectionId;
  const selectedBatchKinds = batchOnCurrentAccount ? lifecycle.request?.kinds ?? kinds : kinds;

  function progressFor(row: OverviewResult) {
    const key = artifactRowKey(row, queryType);
    const stem = sourceFileStem(row);
    const persistedComplete = selectedBatchKinds.every((kind) => availableFiles.has(`${kind}:${stem}.${kind}`));
    return resolveArtifactRowStatus({
      key, selectedKinds: selectedBatchKinds, persistedComplete,
      completedKeys: lifecycle.completedKeys, batchRelevant: batchOnCurrentAccount,
      terminal: ['completed', 'failed'].includes(lifecycle.phase),
      downloadActive: lifecycle.downloadActive,
      activeKeys: new Set([currentKey, copyKey].filter((value): value is string => Boolean(value))),
    });
  }

  const progressKinds = lifecycle.request?.kinds ?? kinds;
  const progressTotal = lifecycle.request?.totalRows ?? totalCount;
  const counterText = artifactCounterText(progressKinds, progressTotal, lifecycle.completedKeys);
  const downloadTitle = progressKinds.length === 1 ? `Đang tải ${progressKinds[0].toUpperCase()}` : 'Đang tải XML/HTML';
  const syncLabel = lifecycle.syncActive && lifecycle.job ? formatSourceJobProgress(lifecycle.job) : '';
  const filterSettled = debouncedSearch === search.trim();

  return <section className="xml-html-page" aria-labelledby="xml-html-title">
    <header><div><h1 id="xml-html-title">XML/HTML</h1><p>Tra cứu XML/HTML các hóa đơn đã/chưa đồng bộ</p></div></header>

    <section className="xml-html-control-block" aria-label="Thiết lập XML HTML">
      <div className="xml-html-control-row">
        <DateRangePicker dateFrom={dateFrom} dateTo={dateTo} onChange={(from, to) => { setDateFrom(from); setDateTo(to); }} />
        <select className="xml-html-company" aria-label="Chọn công ty" title={accounts.find((account) => account.connection_id === connectionId)?.company_name || ''} value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>{accounts.map((account) => <option key={account.connection_id} value={account.connection_id}>{account.company_name || account.username}</option>)}</select>
        <select className="xml-html-query-type" aria-label="Loại hóa đơn" value={queryType} onChange={(event) => setQueryType(event.target.value as InvoiceQueryType)}><option value="query">Hóa đơn điện tử</option><option value="sco-query">Máy tính tiền</option></select>
        <select className="xml-html-direction" aria-label="Lọc mua bán" value={direction} onChange={(event) => setDirection(event.target.value as InvoiceDirection | '')}><option value="">Mua vào và Bán ra</option><option value="purchase">Mua vào</option><option value="sold">Bán ra</option></select>
        <label className="xml-html-refresh"><input type="checkbox" checked={forceRefresh} onChange={(event) => setForceRefresh(event.target.checked)} aria-label="Tải mới dữ liệu" /><span data-checked={forceRefresh}>{forceRefresh ? <img src={checkIcon} alt="" /> : null}</span>Tải mới dữ liệu</label>
        <button className="xml-html-sync" type="button" disabled={lifecycle.active} onClick={syncOverview}><img src={syncIcon} alt="" />{lifecycle.syncActive ? 'Đang đồng bộ…' : 'Đồng bộ dữ liệu'}</button>
      </div>
      <div className="xml-html-control-row xml-html-control-row--storage">
        <StorageFolderPicker value={folder} onChange={onFolder} onBrowse={chooseFolder} ariaLabel="Thư mục lưu trữ XML/HTML" />
        <div className="xml-html-kind-options" aria-label="Định dạng tải xuống">{(['xml', 'html'] as const).map((kind) => <label key={kind} data-active={kinds.includes(kind)}><input type="checkbox" checked={kinds.includes(kind)} onChange={() => toggleKind(kind)} /><span>{kind.toUpperCase()}</span></label>)}</div>
        <input className="xml-html-search" aria-label="Tìm kiếm hóa đơn" placeholder="Số HĐ, ký hiệu, MST..." value={search} onChange={(event) => setSearch(event.target.value)} />
      </div>
    </section>

    <div className="xml-html-download-actions">
      <button className="primary-download-button xml-html-download" type="button" disabled={lifecycle.active || kinds.length === 0 || !filterSettled} onClick={startDownload}><DownloadIcon />Tải xuống kết quả</button>
      <button className="stop-button xml-html-stop" type="button" disabled={!lifecycle.canStop} onClick={() => void lifecycle.stop()}><StopIcon />{lifecycle.phase === 'stopping' ? 'Đang dừng…' : 'Dừng tải'}</button>
    </div>

    {lifecycle.syncActive ? <div className="xml-html-sync-progress" role="status"><strong>Đang đồng bộ dữ liệu</strong><span>{syncLabel}</span><i><b style={{ width: `${lifecycle.sourcePercent}%` }} /></i><em>{Math.round(lifecycle.sourcePercent)}%</em></div> : null}
    {lifecycle.downloadActive && batchOnCurrentAccount ? <div className="xml-html-progress" role="status"><div><strong>{downloadTitle}</strong><b>{Math.round(lifecycle.percent)}%</b></div><small>{counterText}</small><span><i style={{ width: `${lifecycle.percent}%` }} /></span></div> : null}

    {state === 'error' ? <div className="xml-html-state">Không thể đọc dữ liệu hóa đơn đã đồng bộ.</div> : null}
    {state === 'loading' && !items.length ? <div className="xml-html-state">Đang tải...</div> : null}
    {state === 'ready' && !items.length ? <div className="xml-html-state">Không tồn tại hóa đơn trong thời gian này.</div> : null}
    {items.length ? <div className="xml-html-table" tabIndex={0} aria-label="Danh sách hóa đơn XML HTML">
      <div className="xml-html-row xml-html-row--head" style={{ gridTemplateColumns: XML_HTML_GRID }}><span>STT</span><span>Ngày hóa đơn</span><span>Ký hiệu</span><span>Số hóa đơn</span><span>MST đối tác</span><span>Đối tác</span><span>Tổng tiền</span><span>Trạng thái</span><span>Tiến trình</span></div>
      {items.map((row, index) => { const f = row.fields; const purchase = row.direction === 'purchase'; const partner = text(purchase ? f.nbten : f.nmten); const values = [(page - 1) * PAGE_SIZE + index + 1, text(f.tdlap || f.nlap), text(f.khhdon), text(f.shdon), text(purchase ? f.nbmst : f.nmmst), partner, money(f.tgtttbso || f.tgtttbchu), text(f.tthai), progressFor(row)]; return <div className="xml-html-row" style={{ gridTemplateColumns: XML_HTML_GRID }} key={`${row.direction}:${row.row_id}`}>{values.map((value, cell) => <span key={cell} className={cell === 6 ? 'xml-html-amount' : undefined} data-progress={cell === 8 ? String(value) : undefined} title={String(value)}>{value}</span>)}</div>; })}
    </div> : null}
    {(items.length > 0 || page > 1) ? <footer className="artifact-pager"><span>Tổng {totalCount} hàng · tối đa {PAGE_SIZE} hàng/trang</span><div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={page === 1 || state === 'loading'} onClick={() => void loadPage(page - 1)}><img src={previousIcon} alt="" /></button>{tokens.map((token, index) => token === 'ellipsis' ? <span key={`e-${index}`}>...</span> : <button type="button" key={token} data-active={page === token} onClick={() => void loadPage(token)}>{token}</button>)}<button type="button" aria-label="Trang sau" disabled={!pagination?.has_more || state === 'loading'} onClick={() => void loadPage(page + 1)}><img src={nextIcon} alt="" /></button></div></footer> : null}
    {(feedback || lifecycle.message) ? <NoticeDialog kind={lifecycle.phase === 'completed' && lifecycle.failedCount === 0 ? 'success' : 'notice'} message={feedback || lifecycle.message || ''} onClose={() => { setFeedback(''); lifecycle.clearMessage(); }} /> : null}
  </section>;
}
