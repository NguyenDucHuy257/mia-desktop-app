import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import { paginationTokens } from '../../components/pagination-utils';
import previousIcon from '../../assets/figma/artifact-previous.svg';
import nextIcon from '../../assets/figma/artifact-next.svg';
import backIcon from '../../assets/figma/back.png';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import type { ColumnFilters, DetailResult, LocalResultPage, OverviewResult, ReconciliationResult, ReconciliationSummary, ResultExclusion, ResultFilterState, ResultQuery } from '../../lib/runtime-bridge';
import type { InvoiceQueryType } from '../../lib/api/contracts';
import { formatSourceJobProgress } from '../jobs/job-progress-presentation';
import type { BatchItem } from '../jobs/use-batch-job-lifecycle';
import { resultExportErrorMessage } from './result-export-errors';
import { ResultExportProgressBar } from './ResultExportProgressBar';
import { coalesceResultRequest, requestResultWithRetry } from './result-request-policy';
import type { ResultExportLifecycle } from './use-result-export-lifecycle';
import { ColumnFilterPopover } from './ColumnFilterPopover';
import { formatResultCell, formatVietnameseNumber, getDifferenceClass } from './result-presentation';
import { emptyInvoiceSelection, exclusionFromSelection, invoiceSelected, selectionCount, toggleInvoice } from './result-selection';
import '../../styles/results-enhancements.css';
import '../../styles/results-luxury.css';

type ResultMode = 'overview' | 'details' | 'reconciliation';
type ResultExportScope = 'overview' | 'details' | 'reconciliation';
type ResultItem = OverviewResult | DetailResult | ReconciliationResult;

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
    reconciliation: { search: '', column_filters: {} },
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
  const [reconciliation, setReconciliation] = useState<ReconciliationSummary | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [exportOpen, setExportOpen] = useState(false);
  const [exportScopes, setExportScopes] = useState<ResultExportScope[]>(['overview', 'details']);
  const [feedback, setFeedback] = useState('');
  const [exclusion, setExclusion] = useState<ResultExclusion>({ keys: [], rules: [] });
  const [selection, setSelection] = useState(emptyInvoiceSelection);
  const [confirmExclusion, setConfirmExclusion] = useState(false);
  const generation = useRef(0);
  const inFlightPages = useRef(new Map<string, Promise<LocalResultPage<ResultItem>>>());
  const pageCache = useRef(new Map<number, LocalResultPage<ResultItem>>());
  const cursorByPage = useRef(new Map<number, string | null>([[1, null]]));
  const exportRoot = useRef<HTMLDivElement>(null);
  const schemaContext = `${mode}|${direction}|${queryType}|${dateFrom}|${dateTo}`;
  const previousSchemaContext = useRef(schemaContext);
  const hasActiveFilter = Boolean(debouncedSearch || Object.keys(columnFilters).length);

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
      sort: filtersByMode[mode].sort,
      exclusion,
      direction: direction || null,
      query_type: queryType,
      date_from: dateFrom,
      date_to: dateTo,
    };
    const requestKey = JSON.stringify([mode, query]);
    return coalesceResultRequest(inFlightPages.current, requestKey, () => (
      requestResultWithRetry(
        () => bridge[mode](query) as Promise<LocalResultPage<ResultItem>>,
        attempt => {
          diagnosticLog(
            attempt.outcome === 'ok' ? 'results_response' : 'results_request_attempt',
            {
              connection_id: connectionId,
              endpoint: `results.${mode}`,
              mode,
              attempt: attempt.attempt,
              outcome: attempt.outcome,
              duration_ms: attempt.durationMs,
              error_type: attempt.errorType,
              code: attempt.code,
              status: attempt.status,
              date_from: dateFrom,
              date_to: dateTo,
              direction: direction || null,
              query_type: queryType,
              has_search: Boolean(debouncedSearch),
              cursor: Boolean(cursor),
            },
            attempt.outcome === 'failed' ? 'error' : attempt.outcome === 'retry' ? 'warn' : 'info',
          );
        },
      )
    ));
  }, [columnFilters, connectionId, dateFrom, dateTo, debouncedSearch, direction, exclusion, filtersByMode, mode, queryType]);

  const applyPage = useCallback((result: LocalResultPage<ResultItem>, targetPage: number) => {
    setItems(result.items);
    setColumns(result.columns ?? collectColumns(result.items));
    setColumnLabels(result.column_labels ?? {});
    setColumnTypes(result.column_types ?? {});
    setPagination(result.pagination);
    setTotalCount(typeof result.total_count === 'number' ? result.total_count : null);
    setAggregate(result.aggregate ?? null);
    if (result.reconciliation) setReconciliation(result.reconciliation);
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
    if (previousSchemaContext.current !== schemaContext) {
      previousSchemaContext.current = schemaContext;
      setColumns([]);
      setColumnLabels({});
      setColumnTypes({});
    }
    setPagination(null);
    setTotalCount(null);
    setAggregate(null);
    setPageNumber(1);
    void loadPage(1, token);
  }, [loadPage, schemaContext]);

  useEffect(() => {
    const bridge = window.miaRuntime?.results;
    if (!bridge || typeof bridge.reconciliation !== 'function' || !connectionId) return;
    // Reconciliation scans the complete overview/detail dataset. Running that
    // scan for every progress tick keeps long-lived SQLite read transactions
    // open while the crawler is trying to commit the next detail. On Windows
    // this can starve the writer until SQLite's busy timeout expires. Defer the
    // scan until the crawl reaches a terminal state; normal result pages remain
    // available and continue refreshing from committed checkpoints meanwhile.
    if (crawlActive) {
      setReconciliation(null);
      return;
    }
    let active = true;
    void bridge.reconciliation({
      connection_id: connectionId,
      cursor: null,
      limit: 1,
      search: '',
      column_filters: {},
      direction: direction || null,
      query_type: queryType,
      date_from: dateFrom,
      date_to: dateTo,
    }).then(result => {
      if (active) setReconciliation(result.reconciliation ?? null);
    }).catch(() => {
      if (active) setReconciliation(null);
    });
    return () => { active = false; };
  }, [connectionId, crawlActive, dateFrom, dateTo, direction, queryType]);

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

  function toggleExportScope(scope: ResultExportScope) {
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

  function setColumnSort(column: string, sortDirection: 'asc' | 'desc') {
    setFiltersByMode(current => ({
      ...current,
      [mode]: { ...current[mode], sort: { column, direction: sortDirection } },
    }));
  }

  const currentResultQuery = useCallback((scope: ResultMode = mode): ResultQuery => ({
    connection_id: connectionId,
    cursor: null,
    limit: PAGE_SIZE,
    search: filtersByMode[scope].search.trim(),
    column_filters: filtersByMode[scope].column_filters,
    sort: filtersByMode[scope].sort,
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
    if (mode === 'reconciliation') return;
    setExclusion(current => exclusionFromSelection(current, selection, mode, currentResultQuery()));
    setSelection(emptyInvoiceSelection());
    setConfirmExclusion(false);
    setFeedback('Đã loại các hóa đơn đã chọn khỏi tổng và file Excel trong phiên Kết quả này.');
  }

  async function exportResults() {
    const requestedScopes: ResultExportScope[] = mode === 'reconciliation'
      ? ['reconciliation'] : exportScopes;
    if (!connectionId || requestedScopes.length === 0) return;
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
      const availability = await Promise.all(requestedScopes.map(async (scope) => {
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
      scopes: requestedScopes,
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
        result_scopes: requestedScopes,
        date_from: dateFrom,
        date_to: dateTo,
        direction: direction || null,
        query_type: queryType,
        search: exportSearch,
        result_filters: Object.fromEntries(
          requestedScopes.map(scope => [scope, filtersByMode[scope]])
        ),
        exclusion: mode === 'reconciliation' ? { keys: [], rules: [] } : exclusion,
      }]);
      if (summary.failures.length) {
        const error = summary.failures[0].error;
        diagnosticLog('results_export_failed', { connection_id: connectionId, scopes: requestedScopes, code: (error as { code?: string })?.code }, 'error');
        setFeedback(resultExportErrorMessage(error, dateFrom, dateTo, requestedScopes, exportSearch));
        return;
      }
      diagnosticLog('results_export_completed', { connection_id: connectionId, scopes: requestedScopes, file_count: summary.count });
      setFeedback(`Đã tạo ${summary.count} file Excel trong thư mục lưu trữ.`);
      setExportOpen(false);
    } catch (error) {
      diagnosticLog('results_export_failed', { connection_id: connectionId, scopes: requestedScopes, code: (error as { code?: string })?.code }, 'error');
      setFeedback(resultExportErrorMessage(error, dateFrom, dateTo, requestedScopes, exportSearch));
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
  const selectable = mode !== 'reconciliation';
  const gridTemplateColumns = useMemo(
    () => [...(selectable ? ['42px'] : []), ...columns.map((column) => columnWidth(column, columnLabels[column]))].join(' '),
    [columnLabels, columns, selectable],
  );
  const resultExportWorking = resultExports.active && resultExports.owner === 'results';
  const selectedInvoiceCount = selectionCount(selection, aggregate?.invoice_count ?? 0);
  const visibleSelectableKeys = [...new Set(items.filter(item => !item.excluded).map(item => item.invoice_key))];
  const headerChecked = selection.allMatching || (
    visibleSelectableKeys.length > 0 && visibleSelectableKeys.every(key => invoiceSelected(selection, key))
  );
  const headerIndeterminate = selectedInvoiceCount > 0 && !headerChecked;

  return <section className="results-page results-page--figma" aria-label="Kết quả hóa đơn">
    <button className="results-back" type="button" onClick={onBack}><img src={backIcon} alt="" /> Quay lại Quản lý HĐĐT</button>
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
          disabled={resultExports.active || (mode === 'reconciliation' && (!reconciliation?.coverage_ranges.length || !reconciliation.issue_count))}
          title={resultExports.active && resultExports.owner === 'bulk' ? 'Đang tải kết quả tất cả ở màn Hóa đơn.' : undefined}
          onClick={() => mode === 'reconciliation' ? void exportResults() : setExportOpen((value) => !value)}
        >
          <svg className="results-export-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m0 0 4-4m-4 4-4-4M5 16v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" /></svg>
          <span>{resultExportWorking
            ? `Đang tạo Excel… ${Math.round(resultExports.percent)}%`
            : mode === 'reconciliation' ? 'Tải xuống kết quả chênh lệch' : 'Tải xuống kết quả'}</span>
        </button>
        {exportOpen && mode !== 'reconciliation' ? <div className="results-export-popover" role="dialog" aria-label="Chọn nội dung tải xuống">
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
      <button className="results-reconciliation-tab" role="tab" aria-selected={mode === 'reconciliation'} data-active={mode === 'reconciliation'} onClick={() => setMode('reconciliation')}>
        Đối chiếu Tổng quan &amp; Chi tiết
        {reconciliation?.issue_count ? <span className="results-reconciliation-indicator" title="Phát hiện chênh lệch dữ liệu Tổng quan và Chi tiết" aria-label="Phát hiện chênh lệch dữ liệu Tổng quan và Chi tiết" /> : null}
      </button>
    </div>

    <div className="results-filters results-filters--figma">
      {selectable ? <div className="results-exclusion-control">
        <button className="stop-button results-exclude-button" type="button" disabled={selectedInvoiceCount === 0} onClick={() => setConfirmExclusion(true)}>
          Loại khỏi tải xuống ({selectedInvoiceCount})
        </button>
        {confirmExclusion ? <div className="results-exclude-confirm" role="dialog" aria-label="Xác nhận loại hóa đơn">
          <strong>Loại {selectedInvoiceCount} hóa đơn khỏi file tải xuống?</strong>
          <span>Dữ liệu nguồn không bị xóa hoặc thay đổi.</span>
          <div><button type="button" onClick={() => setConfirmExclusion(false)}>Hủy</button><button type="button" onClick={confirmSelectedExclusion}>Xác nhận</button></div>
        </div> : null}
      </div> : null}
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

    {mode === 'reconciliation' && reconciliation ? <div className="results-reconciliation-summary" aria-label="Tổng hợp đối chiếu">
      {reconciliation.uncovered_ranges?.length ? <div>
        <p><span>Dữ liệu hiện có:</span></p>
        <small>Tổng quan: {formatVietnameseNumber(reconciliation.selected_overview_invoice_count ?? reconciliation.overview_invoice_count)} hóa đơn · Chi tiết: {formatVietnameseNumber(reconciliation.selected_detail_invoice_count ?? reconciliation.detail_invoice_count)} hóa đơn</small>
        <small>Chênh lệch dữ liệu hiện có: {(reconciliation.selected_difference ?? 0) > 0 ? '+' : ''}{formatVietnameseNumber(reconciliation.selected_difference ?? 0)} hóa đơn (chưa dùng để kết luận thiếu ngoài phạm vi 2/2)</small>
      </div> : null}
      <div>
        <p><span>{reconciliation.uncovered_ranges?.length ? 'Kết quả trong phạm vi đã đối chiếu:' : 'Tổng số lượng hóa đơn:'}</span> <strong data-warning={reconciliation.difference !== 0}>{reconciliation.difference === 0 ? 'Không chênh lệch số lượng' : `Chênh lệch ${reconciliation.difference > 0 ? '+' : ''}${formatVietnameseNumber(reconciliation.difference)} hóa đơn`}</strong></p>
        <small>(Tổng quan: {formatVietnameseNumber(reconciliation.overview_invoice_count)}; Chi tiết: {formatVietnameseNumber(reconciliation.detail_invoice_count)})</small>
      </div>
      <p><span>Thiếu Chi tiết:</span> <strong data-warning={reconciliation.missing_detail_count > 0}>{formatVietnameseNumber(reconciliation.missing_detail_count)} hóa đơn</strong></p>
      <p><span>Thiếu Tổng quan:</span> <strong data-warning={reconciliation.missing_overview_count > 0}>{formatVietnameseNumber(reconciliation.missing_overview_count)} hóa đơn</strong></p>
      <p><span>Hóa đơn lệch tiền:</span> <strong data-warning={reconciliation.money_mismatch_count > 0}>{formatVietnameseNumber(reconciliation.money_mismatch_count)} hóa đơn</strong></p>
      {reconciliation.coverage_ranges.length ? <footer>Phạm vi đủ điều kiện đối chiếu: {reconciliation.coverage_ranges.map(range => `${formatDisplayDate(range.date_from)} - ${formatDisplayDate(range.date_to)}`).join('; ')}</footer> : null}
      {reconciliation.uncovered_ranges?.length ? <footer data-warning="true">Chưa đủ dữ liệu để đối chiếu: {reconciliation.uncovered_ranges.map(range => `${formatDisplayDate(range.date_from)} - ${formatDisplayDate(range.date_to)}`).join('; ')}</footer> : null}
    </div> : null}

    {feedback ? <div className="results-feedback" role="status">{feedback}</div> : null}
    {state === 'error' ? <div className="results-state" role="alert">Không thể tải kết quả.<button onClick={() => void loadPage(pageNumber)}>Thử lại</button></div> : null}
    {state === 'loading' && items.length === 0 && columns.length === 0 ? <div className="results-state" role="status">Đang tải...</div> : null}
    {state === 'ready' && items.length === 0 && columns.length === 0 ? <div className="results-state results-empty">{mode === 'reconciliation' && !reconciliation?.coverage_ranges.length ? 'Chưa đủ dữ liệu Tổng quan và Chi tiết để đối chiếu trong khoảng thời gian này.' : mode === 'reconciliation' && reconciliation?.issue_count === 0 ? 'Không phát hiện chênh lệch giữa Tổng quan và Chi tiết.' : crawlActive ? 'Chưa có hóa đơn đã ghi vào DB trong lựa chọn này. Tiến trình đồng bộ vẫn đang chạy.' : !crawlItem ? 'Chưa có dữ liệu hóa đơn.' : 'Không có hóa đơn trong khoảng thời gian đã chọn.'}</div> : null}
    {columns.length ? <div className="results-table results-table--figma results-table--excel-schema" tabIndex={0} aria-label="Bảng dữ liệu theo mẫu Excel nguồn">
      <div className="results-row results-row--header" style={{ gridTemplateColumns }}>
        {selectable ? <span className="results-checkbox-cell"><input
          type="checkbox"
          aria-label="Chọn tất cả hóa đơn phù hợp bộ lọc trên mọi trang"
          aria-checked={headerIndeterminate ? 'mixed' : headerChecked}
          ref={node => { if (node) node.indeterminate = headerIndeterminate; }}
          checked={headerChecked}
          onChange={() => setSelection(headerChecked ? emptyInvoiceSelection() : { allMatching: true, selected: new Set(), deselected: new Set() })}
        /></span> : null}
        {columns.map((column) => {
          const label = columnLabels[column] || column;
          return <span className="results-header-slot" key={column}>
            <div className="result-header-cell">
              <span className="result-header-title" title={label}>{label}</span>
              <ColumnFilterPopover
                column={column}
                label={label}
                active={columnFilters[column]}
                sort={filtersByMode[mode].sort}
                loadValues={() => loadFacet(column)}
                onApply={rule => setColumnFilter(column, rule)}
                onClear={() => setColumnFilter(column)}
                onSort={sortDirection => setColumnSort(column, sortDirection)}
              />
            </div>
          </span>;
        })}
      </div>
      {items.length === 0 ? <div className="results-table-empty" role="status">
        {state === 'loading'
          ? 'Đang tải...'
          : mode === 'reconciliation' && !reconciliation?.coverage_ranges.length
            ? 'Chưa đủ dữ liệu Tổng quan và Chi tiết để đối chiếu trong khoảng thời gian này.'
          : mode === 'reconciliation' && reconciliation?.issue_count === 0
            ? 'Không phát hiện chênh lệch giữa Tổng quan và Chi tiết.'
          : hasActiveFilter
            ? 'Không có dữ liệu phù hợp với bộ lọc hiện tại.'
            : crawlActive
              ? 'Chưa có hóa đơn đã ghi vào DB trong lựa chọn này. Tiến trình đồng bộ vẫn đang chạy.'
              : 'Không có hóa đơn trong khoảng thời gian đã chọn.'}
      </div> : null}
      {items.map((item, rowIndex) => <div className="results-row" style={{ gridTemplateColumns }} key={resultKey(item)}>
        {selectable ? <span className="results-checkbox-cell"><input type="checkbox" aria-label={`Chọn hóa đơn ${item.invoice_key}`} disabled={item.excluded} checked={item.excluded || invoiceSelected(selection, item.invoice_key)} onChange={() => setSelection(current => toggleInvoice(current, item.invoice_key))} /></span> : null}
        {columns.map((column) => {
          const rawValue = column === 'stt' && (item.fields[column] === null || item.fields[column] === undefined)
            ? (pageNumber - 1) * PAGE_SIZE + rowIndex + 1
            : item.fields[column];
          const display = formatResultCell(column, rawValue, columnTypes[column]);
          if (column === 'url' && typeof rawValue === 'string' && safeExternalHttpUrl(rawValue)) {
            return <span key={column} title={rawValue}>
              <button
                className="results-external-link"
                type="button"
                title={rawValue}
                onClick={() => void window.miaRuntime?.external.open(rawValue)}
              >{display}</button>
            </span>;
          }
          if (column === 'reconciliation_status') {
            return <span key={column}><b className={`results-reconciliation-status ${reconciliationStatusClass(display)}`}>{display}</b></span>;
          }
          if (column === 'mismatch_fields') {
            return <span className="results-reconciliation-mismatch-fields" key={column} title={display}>{display}</span>;
          }
          return <span className={getDifferenceClass(column, rawValue)} key={column} title={display}>{display}</span>;
        })}
      </div>)}
      {aggregate && items.length > 0 ? <div className="results-row results-row--total" style={{ gridTemplateColumns }}>
        {selectable ? <span className="results-checkbox-cell" /> : null}
        {columns.map(column => {
          const rawValue = column === 'stt'
            ? 'Tổng'
            : column === 'khmshdon'
              ? `${formatVietnameseNumber(aggregate.invoice_count)} HĐ`
              : Object.prototype.hasOwnProperty.call(aggregate.totals, column) ? aggregate.totals[column] : '';
          const display = rawValue === '' ? '' : formatResultCell(column, rawValue, columnTypes[column]);
          return <span className={mode === 'reconciliation' ? getDifferenceClass(column, rawValue, true) : undefined} key={column} title={display}>{display}</span>;
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

function safeExternalHttpUrl(value: string) {
  if (value.length < 1 || value.length > 2048) return false;
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.toLowerCase();
    return ['http:', 'https:'].includes(parsed.protocol)
      && !parsed.username
      && !parsed.password
      && Boolean(host)
      && !['localhost', '127.0.0.1', '::1'].includes(host);
  } catch {
    return false;
  }
}

function reconciliationStatusClass(value: string) {
  if (value === 'Thiếu chi tiết') return 'is-missing-detail';
  if (value === 'Thiếu tổng quan') return 'is-missing-overview';
  return 'is-money-mismatch';
}

function formatDisplayDate(value: string) {
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}/${month}/${year}` : value;
}

function columnWidth(column: string, label = '') {
  if (column === 'stt') return '66px';
  if (column === 'reconciliation_status') return '170px';
  if (column === 'mismatch_fields') return '220px';
  if (column.startsWith('overview_') || column.startsWith('detail_') || column.startsWith('difference_')) return '185px';
  if (['khmshdon', 'khhdon', 'shdon', 'dvtte', 'tgia', 'tthai'].includes(column)) return '150px';
  if (['tdlap', 'ntao', 'nky'].includes(column)) return '170px';
  if (column.includes('mst') || column === 'nmcmnd' || column === 'mhdon') return '180px';
  if (['nbten', 'nmten', 'ten', 'nbdchi', 'nmdchi', 'url'].includes(column)) return '260px';
  if (['tgtcthue', 'tgtthue', 'ttcktmai', 'tgtphi', 'tgtttbso', 'dgia', 'thtien', 'tthue'].includes(column)) return '180px';
  return `${Math.max(150, Math.min(260, label.length * 8 + 36))}px`;
}
