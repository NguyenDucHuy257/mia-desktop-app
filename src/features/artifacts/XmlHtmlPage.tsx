import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import { paginationTokens } from '../../components/pagination-utils';
import { StorageFolderPicker } from '../../components/StorageFolderPicker';
import { NoticeDialog } from '../../components/NoticeDialog';
import previousIcon from '../../assets/figma/artifact-previous.svg';
import nextIcon from '../../assets/figma/artifact-next.svg';
import type { AccountConnection, InvoiceDirection, InvoiceQueryType } from '../../lib/api/contracts';
import type { LocalResultPage, OverviewResult } from '../../lib/runtime-bridge';
import type { InvoiceArtifactKind, XmlHtmlDownloadLifecycle } from './use-xml-html-download-lifecycle';
import '../../styles/xml-html.css';

const PAGE_SIZE = 50;
const DEFAULT_RANGE = { dateFrom: '2023-10-01', dateTo: '2023-10-31' };

function text(value: unknown) { return value === null || value === undefined ? '' : String(value); }
function money(value: unknown) {
  const amount = Number(value);
  return Number.isFinite(amount) ? new Intl.NumberFormat('vi-VN').format(amount) : text(value);
}
function rowKey(row: OverviewResult, queryType: InvoiceQueryType) {
  const f = row.fields;
  return [row.direction, queryType, text(f.nbmst), text(f.khhdon), text(f.shdon), text(f.khmshdon)].join('|');
}
function sourceFileStem(row: OverviewResult) {
  return [row.fields.khmshdon, row.fields.khhdon, row.fields.shdon, row.fields.nbmst]
    .map((value) => text(value).trim().replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')).join('_');
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

  useEffect(() => {
    if (!connectionId && accounts[0]) setConnectionId(accounts[0].connection_id);
  }, [accounts, connectionId]);
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
          setItems(result.items);
          setPagination(result.pagination);
          setTotalCount(result.total_count ?? result.items.length);
          setPage(current);
          setState('ready');
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
          connection_ids: [connectionId], kind, direction: direction || null,
          query_type: queryType,
          search: debouncedSearch, cursor, limit: 200, date_from: dateFrom, date_to: dateTo,
        });
        for (const item of result.items) found.add(`${kind}:${item.filename}`);
        cursor = result.pagination.has_more ? result.pagination.next_cursor : null;
      } while (cursor);
    }));
    setAvailableFiles(found);
  }, [connectionId, dateFrom, dateTo, debouncedSearch, direction, kinds]);

  useEffect(() => {
    const token = ++generation.current;
    cache.current.clear(); cursors.current.clear(); cursors.current.set(1, null); setPage(1);
    void loadPage(1, token); void refreshArtifacts();
  }, [loadPage, refreshArtifacts]);
  useEffect(() => {
    if (!lifecycle.active || lifecycle.request?.connectionId === connectionId) void refreshArtifacts();
  }, [connectionId, lifecycle.active, lifecycle.copyProgress.processed, lifecycle.job?.event_sequence, lifecycle.request?.connectionId, refreshArtifacts]);

  function toggleKind(kind: InvoiceArtifactKind) {
    setKinds((current) => current.includes(kind) ? current.filter((value) => value !== kind) : [...current, kind]);
  }
  async function chooseFolder() { const selected = await window.miaRuntime?.artifacts.selectDirectory(); if (selected) onFolder(selected); }
  function start() {
    if (!connectionId) { setFeedback('Vui lòng chọn tài khoản.'); return; }
    if (!folder.trim()) { setFeedback('Vui lòng chọn thư mục lưu trữ.'); return; }
    if (!kinds.length) { setFeedback('Vui lòng chọn XML, HTML hoặc cả hai.'); return; }
    if (totalCount === 0) { setFeedback('Không tồn tại hóa đơn phù hợp để tải XML/HTML.'); return; }
    setFeedback('');
    lifecycle.start({ connectionId, dateFrom, dateTo, direction: direction || null, queryType, search: debouncedSearch, kinds, destination: folder, totalRows: totalCount });
  }

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));
  const tokens = paginationTokens(totalPages, page);
  const currentArtifact = lifecycle.job?.current_artifact;
  const copyKey = lifecycle.copyProgress.artifact_key;

  function progressFor(row: OverviewResult) {
    const key = rowKey(row, queryType);
    const stem = sourceFileStem(row);
    const complete = kinds.every((kind) => availableFiles.has(`${kind}:${stem}.${kind}`));
    if (complete) return 'Hoàn thành';
    if (lifecycle.request?.connectionId === connectionId && ['completed', 'failed'].includes(lifecycle.phase)) return 'Lỗi';
    if (!lifecycle.active || lifecycle.request?.connectionId !== connectionId) return 'Chưa tải';
    if (currentArtifact && key === [currentArtifact.direction, currentArtifact.query_type, currentArtifact.nbmst, currentArtifact.khhdon, currentArtifact.shdon, currentArtifact.khmshdon].join('|')) return 'Đang tải từ nguồn';
    if (copyKey === key) return 'Đang lưu vào thư mục';
    return 'Đang chờ';
  }

  return <section className="xml-html-page" aria-labelledby="xml-html-title">
    <header><div><h1 id="xml-html-title">XML/HTML</h1><p>Tra cứu hóa đơn đã đồng bộ và tải artifact bằng crawler nguồn.</p></div></header>
    <div className="xml-html-filters">
      <DateRangePicker dateFrom={dateFrom} dateTo={dateTo} onChange={(from, to) => { setDateFrom(from); setDateTo(to); }} />
      <select aria-label="Chọn công ty" value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>{accounts.map((account) => <option key={account.connection_id} value={account.connection_id}>{account.company_name || account.username}</option>)}</select>
      <select aria-label="Loại hóa đơn" value={queryType} onChange={(event) => setQueryType(event.target.value as InvoiceQueryType)}><option value="query">Hóa đơn điện tử</option><option value="sco-query">Máy tính tiền</option></select>
      <select aria-label="Lọc mua bán" value={direction} onChange={(event) => setDirection(event.target.value as InvoiceDirection | '')}><option value="">Mua vào và Bán ra</option><option value="purchase">Mua vào</option><option value="sold">Bán ra</option></select>
      <input aria-label="Tìm kiếm hóa đơn" placeholder="Số HĐ, ký hiệu, MST..." value={search} onChange={(event) => setSearch(event.target.value)} />
    </div>
    <div className="xml-html-actions">
      <StorageFolderPicker value={folder} onChange={onFolder} onBrowse={chooseFolder} ariaLabel="Thư mục lưu trữ XML/HTML" />
      <div className="xml-html-kind-options" aria-label="Định dạng tải xuống">{(['xml', 'html'] as const).map((kind) => <label key={kind} data-active={kinds.includes(kind)}><input type="checkbox" checked={kinds.includes(kind)} onChange={() => toggleKind(kind)} /><span>{kind.toUpperCase()}</span></label>)}</div>
      <button className="xml-html-download" type="button" disabled={lifecycle.active} onClick={start}>Tải xuống kết quả</button>
    </div>
    {lifecycle.active && lifecycle.request?.connectionId === connectionId ? <div className="xml-html-progress" role="status"><div><strong>{lifecycle.phase === 'copy' ? 'Đang lưu XML/HTML' : lifecycle.phase === 'stopping' ? 'Đang dừng tải' : 'Đang tải XML/HTML'}</strong><b>{Math.round(lifecycle.percent)}%</b></div><span><i style={{ width: `${lifecycle.percent}%` }} /></span><small>{lifecycle.phase === 'copy' ? `${lifecycle.copyProgress.processed}/${lifecycle.copyProgress.total} file` : `${totalCount} hóa đơn trong bộ lọc`}</small>{lifecycle.phase === 'source' ? <button type="button" onClick={() => void lifecycle.stop()}>Dừng tải</button> : null}</div> : null}
    {state === 'error' ? <div className="xml-html-state">Không thể đọc dữ liệu hóa đơn đã đồng bộ.</div> : null}
    {state === 'loading' && !items.length ? <div className="xml-html-state">Đang tải...</div> : null}
    {state === 'ready' && !items.length ? <div className="xml-html-state">Không tồn tại hóa đơn trong thời gian này.</div> : null}
    {items.length ? <div className="xml-html-table" tabIndex={0} aria-label="Danh sách hóa đơn XML HTML"><div className="xml-html-row xml-html-row--head"><span>STT</span><span>Ngày hóa đơn</span><span>Ký hiệu</span><span>Số hóa đơn</span><span>MST đối tác</span><span>Đối tác</span><span>Tổng tiền</span><span>Trạng thái</span><span>Tiến trình</span></div>{items.map((row, index) => { const f = row.fields; const purchase = row.direction === 'purchase'; return <div className="xml-html-row" key={`${row.direction}:${row.row_id}`}><span>{(page - 1) * PAGE_SIZE + index + 1}</span><span>{text(f.tdlap || f.nlap)}</span><span>{text(f.khhdon)}</span><span>{text(f.shdon)}</span><span>{text(purchase ? f.nbmst : f.nmmst)}</span><span title={text(purchase ? f.nbten : f.nmten)}>{text(purchase ? f.nbten : f.nmten)}</span><span>{money(f.tgtttbso || f.tgtttbchu)}</span><span>{text(f.tthai)}</span><span data-progress={progressFor(row)}>{progressFor(row)}</span></div>; })}</div> : null}
    {(items.length > 0 || page > 1) ? <footer className="artifact-pager"><span>Tổng {totalCount} hàng · tối đa {PAGE_SIZE} hàng/trang</span><div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={page === 1 || state === 'loading'} onClick={() => void loadPage(page - 1)}><img src={previousIcon} alt="" /></button>{tokens.map((token, index) => token === 'ellipsis' ? <span key={`e-${index}`}>...</span> : <button type="button" key={token} data-active={page === token} onClick={() => void loadPage(token)}>{token}</button>)}<button type="button" aria-label="Trang sau" disabled={!pagination?.has_more || state === 'loading'} onClick={() => void loadPage(page + 1)}><img src={nextIcon} alt="" /></button></div></footer> : null}
    {(feedback || lifecycle.message) ? <NoticeDialog kind={lifecycle.phase === 'completed' ? 'success' : 'notice'} message={feedback || lifecycle.message || ''} onClose={() => { setFeedback(''); lifecycle.clearMessage(); }} /> : null}
  </section>;
}
