import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import previousIcon from '../../assets/figma/artifact-previous.svg';
import nextIcon from '../../assets/figma/artifact-next.svg';
import backIcon from '../../assets/figma/back.png';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import type { DetailResult, LocalResultPage, OverviewResult } from '../../lib/runtime-bridge';
import type { InvoiceQueryType } from '../../lib/api/contracts';
import '../../styles/results-enhancements.css';
import '../../styles/results-luxury.css';

type ResultMode = 'overview' | 'details';
type ResultItem = OverviewResult | DetailResult;
type PageToken = number | 'ellipsis';

const DEFAULT_RANGE = { dateFrom: '2023-10-01', dateTo: '2023-10-31' };
const PAGE_SIZE = 50;

export function ResultsPage({ connectionId, exportFolder, initialDateFrom, initialDateTo, onBack }: {
  connectionId: string;
  exportFolder: string;
  initialDateFrom?: string;
  initialDateTo?: string;
  onBack(): void;
}) {
  const initialRange = useRef(
    initialDateFrom && initialDateTo
      ? { dateFrom: initialDateFrom, dateTo: initialDateTo }
      : readLastSyncDateRange() ?? DEFAULT_RANGE,
  ).current;
  const [mode, setMode] = useState<ResultMode>('overview');
  const [direction, setDirection] = useState<'purchase' | 'sold' | ''>('');
  const [queryType, setQueryType] = useState<InvoiceQueryType>('query');
  const [dateFrom, setDateFrom] = useState(initialRange.dateFrom);
  const [dateTo, setDateTo] = useState(initialRange.dateTo);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [items, setItems] = useState<ResultItem[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [columnLabels, setColumnLabels] = useState<Record<string, string>>({});
  const [pagination, setPagination] = useState<LocalResultPage<ResultItem>['pagination'] | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [exportOpen, setExportOpen] = useState(false);
  const [exportScopes, setExportScopes] = useState<ResultMode[]>(['overview', 'details']);
  const [exportState, setExportState] = useState<'idle' | 'working'>('idle');
  const [feedback, setFeedback] = useState('');
  const generation = useRef(0);
  const pageCache = useRef(new Map<number, LocalResultPage<ResultItem>>());
  const cursorByPage = useRef(new Map<number, string | null>([[1, null]]));
  const exportRoot = useRef<HTMLDivElement>(null);

  const requestPage = useCallback(async (cursor: string | null) => {
    const bridge = window.miaRuntime?.results;
    if (!bridge || !connectionId) throw new Error('results_runtime_unavailable');
    const query = {
      connection_id: connectionId,
      cursor,
      limit: PAGE_SIZE,
      search: debouncedSearch,
      direction: direction || null,
      query_type: queryType,
      date_from: dateFrom,
      date_to: dateTo,
    };
    const started = performance.now();
    diagnosticLog('results_request', {
      connection_id: connectionId,
      mode,
      date_from: dateFrom,
      date_to: dateTo,
      direction: direction || null,
      query_type: queryType,
      has_search: Boolean(debouncedSearch),
      cursor: Boolean(cursor),
    });
    const result = await bridge[mode](query) as LocalResultPage<ResultItem>;
    diagnosticLog('results_response', {
      connection_id: connectionId,
      mode,
      query_type: queryType,
      row_count: result.items.length,
      has_more: result.pagination.has_more,
      duration_ms: Math.round(performance.now() - started),
    });
    return result;
  }, [connectionId, dateFrom, dateTo, debouncedSearch, direction, mode, queryType]);

  const applyPage = useCallback((result: LocalResultPage<ResultItem>, targetPage: number) => {
    setItems(result.items);
    setColumns(result.columns ?? collectColumns(result.items));
    setColumnLabels(result.column_labels ?? {});
    setPagination(result.pagination);
    setTotalCount(typeof result.total_count === 'number' ? result.total_count : null);
    setPageNumber(targetPage);
    setState('ready');
  }, []);

  const loadPage = useCallback(async (targetPage: number, token = generation.current) => {
    if (targetPage < 1) return;
    setState('loading');
    try {
      for (let current = 1; current <= targetPage; current += 1) {
        if (token !== generation.current) return;
        let result = pageCache.current.get(current);
        if (!result) {
          if (!cursorByPage.current.has(current)) return;
          result = await requestPage(cursorByPage.current.get(current) ?? null);
          if (token !== generation.current) return;
          pageCache.current.set(current, result);
        }
        const nextCursor = result.pagination.next_cursor;
        if (result.pagination.has_more && nextCursor) cursorByPage.current.set(current + 1, nextCursor);
        if (current === targetPage) {
          applyPage(result, current);
          return;
        }
        if (!result.pagination.has_more || !nextCursor) {
          applyPage(result, current);
          return;
        }
      }
    } catch (error) {
      diagnosticLog('results_failed', {
        connection_id: connectionId,
        mode,
        date_from: dateFrom,
        date_to: dateTo,
        query_type: queryType,
        code: (error as { code?: string })?.code,
      }, 'error');
      if (token === generation.current) setState('error');
    }
  }, [applyPage, connectionId, dateFrom, dateTo, mode, queryType, requestPage]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const token = generation.current + 1;
    generation.current = token;
    pageCache.current.clear();
    cursorByPage.current.clear();
    cursorByPage.current.set(1, null);
    setItems([]);
    setColumns([]);
    setColumnLabels({});
    setPagination(null);
    setTotalCount(null);
    setPageNumber(1);
    void loadPage(1, token);
  }, [loadPage]);

  useEffect(() => {
    if (!exportOpen) return;
    const close = (event: PointerEvent) => {
      if (event.target instanceof Node && !exportRoot.current?.contains(event.target)) setExportOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setExportOpen(false); };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', escape);
    };
  }, [exportOpen]);

  function toggleExportScope(scope: ResultMode) {
    setExportScopes((current) => current.includes(scope)
      ? current.filter((item) => item !== scope)
      : [...current, scope]);
  }

  function changeQueryType(value: InvoiceQueryType) {
    setQueryType(value);
    // Source has direction-specific cash-register templates. Keep the table on
    // one exact template instead of inventing a union schema.
    if (value === 'sco-query' && direction === '') setDirection('purchase');
  }

  async function exportResults() {
    const artifacts = window.miaRuntime?.artifacts;
    if (!artifacts || !connectionId || exportScopes.length === 0) return;
    if (!exportFolder.trim()) {
      setFeedback('Vui lòng chọn thư mục lưu trữ ở tab Hóa đơn trước khi tải kết quả.');
      setExportOpen(false);
      return;
    }
    setFeedback('');
    setExportState('working');
    diagnosticLog('results_export_requested', {
      connection_id: connectionId,
      scopes: exportScopes,
      date_from: dateFrom,
      date_to: dateTo,
      direction: direction || null,
      destination_configured: true,
    });
    try {
      const result = await artifacts.export({
        destination: exportFolder,
        connection_ids: [connectionId],
        kinds: ['excel'],
        result_scopes: exportScopes,
        date_from: dateFrom,
        date_to: dateTo,
        direction: direction || null,
        search: search.trim(),
      });
      diagnosticLog('results_export_completed', { connection_id: connectionId, scopes: exportScopes, file_count: result.count });
      setFeedback(`Đã tạo ${result.count} file Excel trong thư mục lưu trữ.`);
      setExportOpen(false);
    } catch (error) {
      diagnosticLog('results_export_failed', { connection_id: connectionId, scopes: exportScopes, code: (error as { code?: string })?.code }, 'error');
      setFeedback('Không thể tạo file Excel. Vui lòng kiểm tra dữ liệu và thư mục lưu trữ.');
    } finally {
      setExportState('idle');
    }
  }

  const exactTotalPages = totalCount !== null ? Math.max(1, Math.ceil(totalCount / PAGE_SIZE)) : null;
  const knownLastPage = Math.max(
    pageNumber,
    ...Array.from(pageCache.current.keys()),
    pagination?.has_more ? pageNumber + 1 : pageNumber,
  );
  const displayedPageCount = exactTotalPages ?? knownLastPage;
  const pageTokens = useMemo(
    () => paginationTokens(displayedPageCount, pageNumber),
    [displayedPageCount, pageNumber],
  );
  const gridTemplateColumns = useMemo(
    () => columns.map((column) => columnWidth(column, columnLabels[column])).join(' '),
    [columnLabels, columns],
  );

  return <section className="results-page results-page--figma" aria-label="Kết quả hóa đơn">
    <button className="results-back" type="button" onClick={onBack}><img src={backIcon} alt="" /> Quay lại Quản lý HDDT</button>
    <header className="results-header results-header--figma">
      <div>
        <h1>Kết quả hóa đơn</h1>
        <p>Cột và tiêu đề được đọc trực tiếp từ mẫu Excel gốc của crawler nguồn.</p>
      </div>
      <div className="results-export" ref={exportRoot}>
        <button className="results-export-trigger results-export-trigger--gold" type="button" aria-expanded={exportOpen} onClick={() => setExportOpen((value) => !value)}>
          <svg className="results-export-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m0 0 4-4m-4 4-4-4M5 16v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" /></svg>
          <span>Tải xuống kết quả</span>
        </button>
        {exportOpen ? <div className="results-export-popover results-export-popover--gold" role="dialog" aria-label="Chọn nội dung tải xuống">
          <strong>Nội dung file Excel</strong>
          <label><input type="checkbox" checked={exportScopes.includes('overview')} onChange={() => toggleExportScope('overview')} /> Tổng quan</label>
          <label><input type="checkbox" checked={exportScopes.includes('details')} onChange={() => toggleExportScope('details')} /> Chi tiết</label>
          <small>Lưu tại: {exportFolder || 'Chưa chọn thư mục'}</small>
          <button type="button" disabled={exportState === 'working' || exportScopes.length === 0} onClick={() => void exportResults()}>{exportState === 'working' ? 'Đang tạo Excel...' : 'Tải xuống'}</button>
        </div> : null}
      </div>
    </header>

    <div className="results-tabs results-tabs--figma" role="tablist" aria-label="Loại kết quả">
      <button role="tab" aria-selected={mode === 'overview'} data-active={mode === 'overview'} onClick={() => setMode('overview')}>Tổng quan</button>
      <button role="tab" aria-selected={mode === 'details'} data-active={mode === 'details'} onClick={() => setMode('details')}>Chi tiết</button>
    </div>

    <div className="results-filters results-filters--figma">
      <DateRangePicker className="results-date-range" dateFrom={dateFrom} dateTo={dateTo} fromLabel="Từ ngày xem" toLabel="Đến ngày xem" onChange={(from, to) => { setDateFrom(from); setDateTo(to); }} />
      <select aria-label="Loại hóa đơn" value={queryType} onChange={(event) => changeQueryType(event.target.value as InvoiceQueryType)}>
        <option value="query">Hóa đơn điện tử</option>
        <option value="sco-query">Máy tính tiền</option>
      </select>
      <select aria-label="Lọc mua bán" value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}>
        <option value="" disabled={queryType === 'sco-query'}>Mua vào và bán ra</option>
        <option value="purchase">Mua vào</option>
        <option value="sold">Bán ra</option>
      </select>
      <div className="results-search-wrap">
        <input aria-label="Tìm kiếm kết quả" placeholder="Lọc theo số HĐ, tên KH..." value={search} onChange={(event) => setSearch(event.target.value)} />
        {search ? <button type="button" aria-label="Xóa tìm kiếm" onClick={() => setSearch('')}>×</button> : null}
      </div>
    </div>

    {feedback ? <div className="results-feedback" role="status">{feedback}</div> : null}
    {state === 'error' ? <div className="results-state" role="alert">Không thể tải kết quả.<button onClick={() => void loadPage(pageNumber)}>Thử lại</button></div> : null}
    {state === 'loading' && items.length === 0 ? <div className="results-state" role="status">Đang tải...</div> : null}
    {state === 'ready' && items.length === 0 ? <div className="results-state results-empty">Không tồn tại hóa đơn trong thời gian này.</div> : null}
    {items.length && columns.length ? <div className="results-table results-table--figma results-table--excel-schema" tabIndex={0} aria-label="Bảng dữ liệu theo mẫu Excel nguồn">
      <div className="results-row results-row--header" style={{ gridTemplateColumns }}>
        {columns.map((column) => {
          const label = columnLabels[column] || column;
          return <span key={column} title={label}>{label}</span>;
        })}
      </div>
      {items.map((item, rowIndex) => <div className="results-row" style={{ gridTemplateColumns }} key={resultKey(item)}>
        {columns.map((column) => {
          const rawValue = column === 'stt' && (item.fields[column] === null || item.fields[column] === undefined)
            ? (pageNumber - 1) * PAGE_SIZE + rowIndex + 1
            : item.fields[column];
          const display = formatCell(rawValue);
          return <span key={column} title={display}>{display}</span>;
        })}
      </div>)}
    </div> : null}

    {(items.length > 0 || pageNumber > 1) ? <footer className="results-pager artifact-pager">
      <span>{totalCount !== null ? `Tổng ${totalCount} hàng · tối đa ${PAGE_SIZE} hàng/trang` : `Trang ${pageNumber} · tối đa ${PAGE_SIZE} hàng/trang`}</span>
      <div>
        <span>Chọn trang:</span>
        <button type="button" aria-label="Trang trước" disabled={pageNumber === 1 || state === 'loading'} onClick={() => void loadPage(pageNumber - 1)}><img src={previousIcon} alt="" /></button>
        {pageTokens.map((token, index) => token === 'ellipsis'
          ? <span className="results-page-ellipsis" key={`ellipsis-${index}`}>...</span>
          : <button type="button" key={token} data-active={pageNumber === token} disabled={state === 'loading'} onClick={() => void loadPage(token)}>{token}</button>)}
        <button type="button" aria-label="Trang sau" disabled={!pagination?.has_more || state === 'loading'} onClick={() => void loadPage(pageNumber + 1)}><img src={nextIcon} alt="" /></button>
      </div>
    </footer> : null}
  </section>;
}

function collectColumns(items: ResultItem[]) {
  const columns: string[] = [];
  const seen = new Set<string>();
  for (const item of items) {
    for (const key of Object.keys(item.fields)) {
      if (!seen.has(key)) {
        seen.add(key);
        columns.push(key);
      }
    }
  }
  return columns;
}

function resultKey(item: ResultItem) {
  return `${item.direction}-${item.row_id}`;
}

function columnWidth(column: string, label = '') {
  if (column === 'stt') return '66px';
  if (['khmshdon', 'khhdon', 'shdon', 'dvtte', 'tgia', 'tthai'].includes(column)) return '150px';
  if (['tdlap', 'ntao', 'nky'].includes(column)) return '170px';
  if (column.includes('mst') || column === 'nmcmnd' || column === 'mhdon') return '180px';
  if (['nbten', 'nmten', 'ten', 'nbdchi', 'nmdchi', 'url'].includes(column)) return '260px';
  if (['tgtcthue', 'tgtthue', 'ttcktmai', 'tgtphi', 'tgtttbso', 'dgia', 'thtien', 'tthue'].includes(column)) return '180px';
  return `${Math.max(150, Math.min(260, label.length * 8 + 36))}px`;
}

function formatCell(value: unknown) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Có' : 'Không';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function paginationTokens(totalPages: number, currentPage: number): PageToken[] {
  if (totalPages <= 5) return Array.from({ length: totalPages }, (_, index) => index + 1);
  const candidates = new Set([1, totalPages, currentPage - 1, currentPage, currentPage + 1]);
  const pages = [...candidates].filter((value) => value >= 1 && value <= totalPages).sort((a, b) => a - b);
  const tokens: PageToken[] = [];
  pages.forEach((value, index) => {
    if (index > 0 && value - pages[index - 1] > 1) tokens.push('ellipsis');
    tokens.push(value);
  });
  return tokens;
}
