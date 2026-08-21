import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import downloadIcon from '../../assets/figma/artifact-download.svg';
import previousIcon from '../../assets/figma/artifact-previous.svg';
import nextIcon from '../../assets/figma/artifact-next.svg';
import backIcon from '../../assets/figma/back.png';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import type { DetailResult, LocalResultPage, OverviewResult } from '../../lib/runtime-bridge';
import '../../styles/results-enhancements.css';

type ResultMode = 'overview' | 'details';
type ResultItem = OverviewResult | DetailResult;
type PageToken = number | 'ellipsis';

const DEFAULT_RANGE = { dateFrom: '2023-10-01', dateTo: '2023-10-31' };
const PAGE_SIZE = 50;

const COLUMN_LABELS: Record<string, string> = {
  id: 'ID',
  company_tax_code: 'MST doanh nghiệp',
  direction: 'Loại',
  query_type: 'Loại truy vấn',
  invoice_category: 'Nhóm hóa đơn',
  nbmst: 'MST người bán',
  khhdon: 'Ký hiệu HĐ',
  shdon: 'Số hóa đơn',
  khmshdon: 'Mẫu số',
  nlap: 'Ngày lập',
  nlap_date: 'Ngày lập chuẩn',
  detail_fetched: 'Đã có chi tiết',
  stt: 'STT',
  created_at: 'Tạo lúc',
  updated_at: 'Cập nhật lúc',
};

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
  const [dateFrom, setDateFrom] = useState(initialRange.dateFrom);
  const [dateTo, setDateTo] = useState(initialRange.dateTo);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [items, setItems] = useState<ResultItem[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
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
      has_search: Boolean(debouncedSearch),
      cursor: Boolean(cursor),
    });
    const result = await bridge[mode](query) as LocalResultPage<ResultItem>;
    diagnosticLog('results_response', {
      connection_id: connectionId,
      mode,
      row_count: result.items.length,
      has_more: result.pagination.has_more,
      duration_ms: Math.round(performance.now() - started),
    });
    return result;
  }, [connectionId, dateFrom, dateTo, debouncedSearch, direction, mode]);

  const applyPage = useCallback((result: LocalResultPage<ResultItem>, targetPage: number) => {
    setItems(result.items);
    setColumns(result.columns ?? collectColumns(result.items));
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
        if (result.pagination.has_more && nextCursor) {
          cursorByPage.current.set(current + 1, nextCursor);
        }
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
        code: (error as { code?: string })?.code,
      }, 'error');
      if (token === generation.current) setState('error');
    }
  }, [applyPage, connectionId, dateFrom, dateTo, mode, requestPage]);

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
    () => columns.map(columnWidth).join(' '),
    [columns],
  );

  return <section className="results-page results-page--figma" aria-label="Kết quả hóa đơn">
    <button className="results-back" type="button" onClick={onBack}><img src={backIcon} alt="" /> Quay lại Quản lý HDDT</button>
    <header className="results-header results-header--figma">
      <div>
        <h1>Kết quả hóa đơn</h1>
        <p>Dữ liệu được đọc trực tiếp từ SQLite của crawler nguồn; không hiển thị cột raw/path.</p>
      </div>
      <div className="results-export" ref={exportRoot}>
        <button className="results-export-trigger" type="button" aria-expanded={exportOpen} onClick={() => setExportOpen((value) => !value)}><img src={downloadIcon} alt="" /> Tải xuống kết quả</button>
        {exportOpen ? <div className="results-export-popover" role="dialog" aria-label="Chọn nội dung tải xuống">
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
      <select aria-label="Lọc mua bán" value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}>
        <option value="">Mua vào và bán ra</option>
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
    {items.length && columns.length ? <div className="results-table results-table--figma">
      <div className="results-row results-row--header" style={{ gridTemplateColumns }}>
        {columns.map((column) => <span key={column} title={column}>{columnLabel(column)}</span>)}
      </div>
      {items.map((item) => <div className="results-row" style={{ gridTemplateColumns }} key={resultKey(item)}>
        {columns.map((column) => {
          const display = formatCell(column, item.fields[column]);
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

function columnLabel(column: string) {
  return COLUMN_LABELS[column] ?? column;
}

function columnWidth(column: string) {
  if (column === 'id' || column === 'stt') return '72px';
  if (column === 'direction') return '96px';
  if (column === 'query_type' || column === 'invoice_category') return '122px';
  if (column === 'detail_fetched') return '110px';
  if (column === 'shdon' || column === 'khmshdon') return '126px';
  if (column === 'khhdon' || column === 'nlap_date') return '148px';
  if (column.includes('mst')) return '154px';
  if (column === 'created_at' || column === 'updated_at' || column === 'nlap') return '190px';
  return 'minmax(150px, 220px)';
}

function formatCell(column: string, value: unknown) {
  if (column === 'direction') {
    if (value === 'purchase') return 'Mua vào';
    if (value === 'sold') return 'Bán ra';
  }
  if (column === 'query_type') {
    if (value === 'query') return 'HĐĐT';
    if (value === 'sco-query') return 'Máy tính tiền';
  }
  if (column === 'detail_fetched') return value ? 'Có' : 'Chưa';
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function paginationTokens(totalPages: number, currentPage: number): PageToken[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  if (currentPage <= 4) return [1, 2, 3, 4, 5, 'ellipsis', totalPages];
  if (currentPage >= totalPages - 3) return [1, 'ellipsis', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
  return [1, 'ellipsis', currentPage - 1, currentPage, currentPage + 1, 'ellipsis', totalPages];
}
