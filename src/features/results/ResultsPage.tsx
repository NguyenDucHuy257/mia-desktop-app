import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import { paginationTokens } from '../../components/pagination-utils';
import previousIcon from '../../assets/figma/artifact-previous.svg';
import nextIcon from '../../assets/figma/artifact-next.svg';
import backIcon from '../../assets/figma/back.png';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import type { ColumnFilters, DetailResult, LocalResultPage, OverviewResult, ResultExclusion, ResultFilterState, ResultQuery } from '../../lib/runtime-bridge';
import type { InvoiceQueryType } from '../../lib/api/contracts';
import { formatSourceJobProgress } from '../jobs/job-progress-presentation';
import type { BatchItem } from '../jobs/use-batch-job-lifecycle';
import { resultExportErrorMessage } from './result-export-errors';
import { ResultExportProgressBar } from './ResultExportProgressBar';
import type { ResultExportLifecycle } from './use-result-export-lifecycle';
import { ColumnFilterPopover } from './ColumnFilterPopover';
import { formatResultCell, formatVietnameseNumber, MONETARY_FIELDS } from './result-presentation';
import { emptyInvoiceSelection, exclusionFromSelection, invoiceSelected, selectionCount, toggleInvoice } from './result-selection';
import '../../styles/results-enhancements.css';
import '../../styles/results-luxury.css';

type ResultMode = 'overview' | 'details';
type ResultItem = OverviewResult | DetailResult;

const DEFAULT_RANGE = { dateFrom: '2023-10-01', dateTo: '2023-10-31' };
const PAGE_SIZE = 50;
const TERMINAL_JOB_STATUSES = new Set(['completed', 'completed_with_warning', 'failed', 'cancelled', 'abandoned']);

export function ResultsPage({ connectionId, exportFolder, initialDateFrom, initialDateTo, crawlItem, resultExports, onBack }: {
  connectionId: string;
  exportFolder: string;
  initialDateFrom?: string;
  initialDateTo?: string;
  crawlItem?: BatchItem;
  resultExports: ResultExportLifecycle;
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
  const [filtersByMode, setFiltersByMode] = useState<Record<ResultMode, ResultFilterState>>({
    overview: { search: '', column_filters: {} },
    details: { search: '', column_filters: {} },
  });
  const search = filtersByMode[mode].search;
  const columnFilters = filtersByMode[mode].column_filters;
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [items, setItems] = useState<ResultItem[]>([]);
  const [columns, setColumns] = useState<string[]>([]);
  const [columnLabels, setColumnLabels] = useState<Record<string, string>>({});
  const [columnTypes, setColumnTypes] = useState<Record<string, 'text' | 'number' | 'percent'>>({});
  const [pagination, setPagination] = useState<LocalResultPage<ResultItem>['pagination'] | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [aggregate, setAggregate] = useState<NonNullable<LocalResultPage<ResultItem>['aggregate']> | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [exportOpen, setExportOpen] = useState(false);
  const [exportScopes, setExportScopes] = useState<ResultMode[]>(['overview', 'details']);
  const [feedback, setFeedback] = useState('');
  const [exclusion, setExclusion] = useState<ResultExclusion>({ keys: [], rules: [] });
  const [selection, setSelection] = useState(emptyInvoiceSelection);
  const [confirmExclusion, setConfirmExclusion] = useState(false);
  const generation = useRef(0);
  const pageCache = useRef(new Map<number, LocalResultPage<ResultItem>>());
  const cursorByPage = useRef(new Map<number, string | null>([[1, null]]));
  const exportRoot = useRef<HTMLDivElement>(null);

  const crawlJob = crawlItem?.status ?? crawlItem?.record;
  const crawlStatus = crawlJob?.status;
  const crawlPhase = crawlItem?.phase;
  const crawlActive = Boolean(
    crawlPhase === 'queued'
    || crawlPhase === 'starting'
    || crawlPhase === 'stopping'
    || (crawlStatus && !TERMINAL_JOB_STATUSES.has(crawlStatus)),
  );
  const crawlPercent = Math.max(0, Math.min(100, Number(crawlJob?.overall_percent ?? 0)));
  const crawlLabel = crawlPhase === 'queued'
    ? 'Chờ đến lượt xử lý…'
    : crawlPhase === 'starting'
      ? 'Đang tạo tác vụ đồng bộ…'
      : crawlPhase === 'stopping'
        ? 'Đang dừng đồng bộ…'
        : crawlJob
          ? formatSourceJobProgress(crawlJob)
          : 'Đang đồng bộ dữ liệu…';
  const crawlFingerprint = crawlJob
    ? JSON.stringify([
      crawlPhase,
      crawlStatus,
      crawlJob.event_sequence,
      crawlJob.message,
      crawlJob.overall_percent,
      crawlJob.scope_progress?.scope,
      crawlJob.scope_progress?.processed,
      crawlJob.scope_progress?.total,
    ])
    : '';

  const requestPage = useCallback(async (cursor: string | null) => {
    const bridge = window.miaRuntime?.results;
    if (!bridge || !connectionId) throw new Error('results_runtime_unavailable');
    const query = {
      connection_id: connectionId,
      cursor,
      limit: PAGE_SIZE,
      search: debouncedSearch,
      column_filters: columnFilters,
      exclusion,
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
  }, [columnFilters, connectionId, dateFrom, dateTo, debouncedSearch, direction, exclusion, mode, queryType]);

  const applyPage = useCallback((result: LocalResultPage<ResultItem>, targetPage: number) => {
    setItems(result.items);
    setColumns(result.columns ?? collectColumns(result.items));
    setColumnLabels(result.column_labels ?? {});
    setColumnTypes(result.column_types ?? {});
    setPagination(result.pagination);
    setTotalCount(typeof result.total_count === 'number' ? result.total_count : null);
    setAggregate(result.aggregate ?? null);
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

  useEffect(() => { setSelection(emptyInvoiceSelection()); }, [mode, debouncedSearch, columnFilters, direction, queryType, dateFrom, dateTo]);

  useEffect(() => {
    const token = generation.current + 1;
    generation.current = token;
    pageCache.current.clear();
    cursorByPage.current.clear();
    cursorByPage.current.set(1, null);
    setItems([]);
    setColumns([]);
    setColumnLabels({});
    setColumnTypes({});
    setPagination(null);
    setTotalCount(null);
    setAggregate(null);
    setPageNumber(1);
    void loadPage(1, token);
  }, [loadPage]);

  // The result view is allowed while the source job is still running. Refresh
  // from persisted SQLite whenever the polled source progress changes so rows
  // committed after the user opened this tab become visible without remounting.
  // A terminal transition triggers one final refresh as well.
  useEffect(() => {
    if (!crawlFingerprint) return;
    const token = generation.current + 1;
    generation.current = token;
    pageCache.current.clear();
    cursorByPage.current.clear();
    cursorByPage.current.set(1, null);
    void loadPage(pageNumber, token);
  // pageNumber is intentionally not a dependency: clicking a page already calls
  // loadPage; this effect is driven only by source progress revisions.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crawlFingerprint]);

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
    if (value === 'sco-query' && direction === '') setDirection('purchase');
  }

  function setSearch(value: string) {
    setFiltersByMode(current => ({
      ...current,
      [mode]: { ...current[mode], search: value },
    }));
  }

  function setColumnFilter(column: string, rule?: ColumnFilters[string]) {
    setFiltersByMode(current => {
      const column_filters = { ...current[mode].column_filters };
      if (rule && (rule.values || rule.search || rule.operator)) column_filters[column] = rule;
      else delete column_filters[column];
      return { ...current, [mode]: { ...current[mode], column_filters } };
    });
  }

  const currentResultQuery = useCallback((scope: ResultMode = mode): ResultQuery => ({
    connection_id: connectionId,
    cursor: null,
    limit: PAGE_SIZE,
    search: filtersByMode[scope].search.trim(),
    column_filters: filtersByMode[scope].column_filters,
    direction: direction || null,
    query_type: queryType,
    date_from: dateFrom,
    date_to: dateTo,
  }), [connectionId, dateFrom, dateTo, direction, filtersByMode, mode, queryType]);

  const loadFacet = useCallback(async (column: string) => {
    const bridge = window.miaRuntime?.results;
    if (!bridge) throw new Error('results_runtime_unavailable');
    return bridge.facets({ ...currentResultQuery(), kind: mode, column, facet_limit: 250 });
  }, [currentResultQuery, mode]);

  function confirmSelectedExclusion() {
    setExclusion(current => exclusionFromSelection(current, selection, mode, currentResultQuery()));
    setSelection(emptyInvoiceSelection());
    setConfirmExclusion(false);
    setFeedback('Đã loại các hóa đơn đã chọn khỏi tổng và file Excel trong phiên Kết quả này.');
  }

  async function exportResults() {
    if (!connectionId || exportScopes.length === 0) return;
    if (!exportFolder.trim()) {
      setFeedback('Vui lòng chọn thư mục lưu trữ ở tab Hóa đơn trước khi tải kết quả.');
      setExportOpen(false);
      return;
    }
    if (resultExports.active) {
      setFeedback(resultExports.owner === 'bulk'
        ? 'Đang tải kết quả tất cả ở màn Hóa đơn. Hãy chờ tác vụ đó hoàn tất.'
        : 'Đang tạo file Excel này. Hãy chờ tác vụ hiện tại hoàn tất.');
      return;
    }

    const exportSearch = search.trim();
    const bridge = window.miaRuntime?.results;
    if (bridge) {
      const availability = await Promise.all(exportScopes.map(async (scope) => {
        if (
          scope === mode
          && state === 'ready'
          && debouncedSearch === exportSearch
          && totalCount !== null
        ) return totalCount > 0;
        try {
          const result = await bridge[scope]({
            connection_id: connectionId,
            cursor: null,
            limit: 1,
            search: filtersByMode[scope].search.trim(),
            column_filters: filtersByMode[scope].column_filters,
            exclusion,
            direction: direction || null,
            query_type: queryType,
            date_from: dateFrom,
            date_to: dateTo,
          }) as LocalResultPage<ResultItem>;
          return typeof result.total_count === 'number'
            ? result.total_count > 0
            : result.items.length > 0;
        } catch {
          return null;
        }
      }));
      if (availability.length > 0 && availability.every((value) => value === false)) {
        setFeedback(exportSearch
          ? 'Không tồn tại hóa đơn phù hợp với lựa chọn hiện tại.'
          : 'Không tồn tại hóa đơn trong thời gian này.');
        setExportOpen(false);
        return;
      }
    }

    setFeedback('');
    diagnosticLog('results_export_requested', {
      connection_id: connectionId,
      scopes: exportScopes,
      date_from: dateFrom,
      date_to: dateTo,
      direction: direction || null,
      query_type: queryType,
      destination_configured: true,
    });
    try {
      const summary = await resultExports.run('results', [{
        destination: exportFolder,
        connection_ids: [connectionId],
        kinds: ['excel'],
        result_scopes: exportScopes,
        date_from: dateFrom,
        date_to: dateTo,
        direction: direction || null,
        query_type: queryType,
        search: exportSearch,
        result_filters: filtersByMode,
        exclusion,
      }]);
      if (summary.failures.length) {
        const error = summary.failures[0].error;
        diagnosticLog('results_export_failed', { connection_id: connectionId, scopes: exportScopes, code: (error as { code?: string })?.code }, 'error');
        setFeedback(resultExportErrorMessage(error, dateFrom, dateTo, exportScopes, exportSearch));
        return;
      }
      diagnosticLog('results_export_completed', { connection_id: connectionId, scopes: exportScopes, file_count: summary.count });
      setFeedback(`Đã tạo ${summary.count} file Excel trong thư mục lưu trữ.`);
      setExportOpen(false);
    } catch (error) {
      diagnosticLog('results_export_failed', { connection_id: connectionId, scopes: exportScopes, code: (error as { code?: string })?.code }, 'error');
      setFeedback(resultExportErrorMessage(error, dateFrom, dateTo, exportScopes, exportSearch));
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
    () => ['42px', ...columns.map((column) => columnWidth(column, columnLabels[column]))].join(' '),
    [columnLabels, columns],
  );
  const resultExportWorking = resultExports.active && resultExports.owner === 'results';
  const selectedInvoiceCount = selectionCount(selection, aggregate?.invoice_count ?? 0);
  const visibleSelectableKeys = [...new Set(items.filter(item => !item.excluded).map(item => item.invoice_key))];
  const headerChecked = selection.allMatching || (
    visibleSelectableKeys.length > 0 && visibleSelectableKeys.every(key => invoiceSelected(selection, key))
  );
  const headerIndeterminate = selectedInvoiceCount > 0 && !headerChecked;

  return <section className="results-page results-page--figma" aria-label="Kết quả hóa đơn">
    <button className="results-back" type="button" onClick={onBack}><img src={backIcon} alt="" /> Quay lại Quản lý HDDT</button>
    <header className="results-header results-header--figma">
      <div>
        <h1>Kết quả hóa đơn</h1>
        <p>Cột và tiêu đề được đọc trực tiếp từ mẫu Excel gốc của crawler nguồn.</p>
      </div>
      <div className="results-export" ref={exportRoot}>
        <button
          className="results-export-trigger primary-download-button"
          type="button"
          aria-expanded={exportOpen}
          disabled={resultExports.active}
          title={resultExports.active && resultExports.owner === 'bulk' ? 'Đang tải kết quả tất cả ở màn Hóa đơn.' : undefined}
          onClick={() => setExportOpen((value) => !value)}
        >
          <svg className="results-export-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m0 0 4-4m-4 4-4-4M5 16v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" /></svg>
          <span>{resultExportWorking ? `Đang tạo Excel… ${Math.round(resultExports.percent)}%` : 'Tải xuống kết quả'}</span>
        </button>
        {exportOpen ? <div className="results-export-popover" role="dialog" aria-label="Chọn nội dung tải xuống">
          <strong>Nội dung file Excel</strong>
          <label><input type="checkbox" checked={exportScopes.includes('overview')} disabled={resultExports.active} onChange={() => toggleExportScope('overview')} /> Tổng quan</label>
          <label><input type="checkbox" checked={exportScopes.includes('details')} disabled={resultExports.active} onChange={() => toggleExportScope('details')} /> Chi tiết</label>
          <small>Lưu tại: {exportFolder || 'Chưa chọn thư mục'}</small>
          <button type="button" disabled={resultExports.active || exportScopes.length === 0} onClick={() => void exportResults()}>{resultExportWorking ? `Đang tạo Excel... ${Math.round(resultExports.percent)}%` : 'Tải xuống'}</button>
          {resultExportWorking ? <ResultExportProgressBar lifecycle={resultExports} /> : null}
        </div> : null}
      </div>
    </header>

    <div className="results-tabs results-tabs--figma" role="tablist" aria-label="Loại kết quả">
      <button role="tab" aria-selected={mode === 'overview'} data-active={mode === 'overview'} onClick={() => setMode('overview')}>Tổng quan</button>
      <button role="tab" aria-selected={mode === 'details'} data-active={mode === 'details'} onClick={() => setMode('details')}>Chi tiết</button>
    </div>

    <div className="results-filters results-filters--figma">
      <div className="results-exclusion-control">
        <button className="stop-button results-exclude-button" type="button" disabled={selectedInvoiceCount === 0} onClick={() => setConfirmExclusion(true)}>
          Loại khỏi tải xuống ({selectedInvoiceCount})
        </button>
        {confirmExclusion ? <div className="results-exclude-confirm" role="dialog" aria-label="Xác nhận loại hóa đơn">
          <strong>Loại {selectedInvoiceCount} hóa đơn khỏi file tải xuống?</strong>
          <span>Dữ liệu nguồn không bị xóa hoặc thay đổi.</span>
          <div><button type="button" onClick={() => setConfirmExclusion(false)}>Hủy</button><button type="button" onClick={confirmSelectedExclusion}>Xác nhận</button></div>
        </div> : null}
      </div>
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

    {crawlActive ? <div className="results-crawl-status" role="status" aria-live="polite">
      <strong>Đang đồng bộ</strong>
      <span>{crawlLabel}</span>
      <div className="results-crawl-track" aria-hidden="true"><span style={{ width: `${crawlPercent}%` }} /></div>
      <em>{Math.round(crawlPercent)}%</em>
    </div> : null}

    {feedback ? <div className="results-feedback" role="status">{feedback}</div> : null}
    {state === 'error' ? <div className="results-state" role="alert">Không thể tải kết quả.<button onClick={() => void loadPage(pageNumber)}>Thử lại</button></div> : null}
    {state === 'loading' && items.length === 0 ? <div className="results-state" role="status">Đang tải...</div> : null}
    {state === 'ready' && items.length === 0 ? <div className="results-state results-empty">{crawlActive ? 'Chưa có hóa đơn đã ghi vào DB trong lựa chọn này. Tiến trình đồng bộ vẫn đang chạy.' : 'Không tồn tại hóa đơn trong thời gian này.'}</div> : null}
    {items.length && columns.length ? <div className="results-table results-table--figma results-table--excel-schema" tabIndex={0} aria-label="Bảng dữ liệu theo mẫu Excel nguồn">
      <div className="results-row results-row--header" style={{ gridTemplateColumns }}>
        <span className="results-checkbox-cell"><input
          type="checkbox"
          aria-label="Chọn tất cả hóa đơn phù hợp bộ lọc trên mọi trang"
          aria-checked={headerIndeterminate ? 'mixed' : headerChecked}
          ref={node => { if (node) node.indeterminate = headerIndeterminate; }}
          checked={headerChecked}
          onChange={() => setSelection(headerChecked ? emptyInvoiceSelection() : { allMatching: true, selected: new Set(), deselected: new Set() })}
        /></span>
        {columns.map((column) => {
          const label = columnLabels[column] || column;
          return <span className="results-header-cell" key={column} title={label}>{label}<ColumnFilterPopover
            column={column}
            label={label}
            active={columnFilters[column]}
            loadValues={() => loadFacet(column)}
            onApply={rule => setColumnFilter(column, rule)}
            onClear={() => setColumnFilter(column)}
          /></span>;
        })}
      </div>
      {items.map((item, rowIndex) => <div className="results-row" style={{ gridTemplateColumns }} key={resultKey(item)}>
        <span className="results-checkbox-cell"><input type="checkbox" aria-label={`Chọn hóa đơn ${item.invoice_key}`} disabled={item.excluded} checked={item.excluded || invoiceSelected(selection, item.invoice_key)} onChange={() => setSelection(current => toggleInvoice(current, item.invoice_key))} /></span>
        {columns.map((column) => {
          const rawValue = column === 'stt' && (item.fields[column] === null || item.fields[column] === undefined)
            ? (pageNumber - 1) * PAGE_SIZE + rowIndex + 1
            : item.fields[column];
          const display = formatResultCell(column, rawValue, columnTypes[column]);
          return <span key={column} title={display}>{display}</span>;
        })}
      </div>)}
      {aggregate ? <div className="results-row results-row--total" style={{ gridTemplateColumns }}>
        <span className="results-checkbox-cell" />
        {columns.map(column => {
          const rawValue = column === 'stt'
            ? 'Tổng'
            : column === 'khmshdon'
              ? `${formatVietnameseNumber(aggregate.invoice_count)} HĐ`
              : MONETARY_FIELDS.has(column) ? aggregate.totals[column] : '';
          const display = rawValue === '' ? '' : formatResultCell(column, rawValue, columnTypes[column]);
          return <span key={column} title={display}>{display}</span>;
        })}
      </div> : null}
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
  return `${item.invoice_key || item.direction}-${item.row_id}`;
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
