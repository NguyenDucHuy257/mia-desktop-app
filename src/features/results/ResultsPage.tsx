import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import downloadIcon from '../../assets/figma/artifact-download.svg';
import backIcon from '../../assets/figma/back.png';
import { diagnosticLog } from '../../lib/diagnostic-logger';
import type { DetailResult, LocalResultPage, OverviewResult, ResultMeta } from '../../lib/runtime-bridge';
import {
  columnLabel,
  formatResultValue,
  formatTotalValue,
  isNumericResultColumn,
  isTotalResultColumn,
  orderResultColumns,
} from './result-table-utils';
import '../../styles/results-enhancements.css';

type ResultMode = 'overview' | 'details';
type ResultItem = OverviewResult | DetailResult;

const DEFAULT_RANGE = { dateFrom: '2023-10-01', dateTo: '2023-10-31' };
const PAGE_SIZE = 50;

export function ResultsPage({ connectionId, initialDateFrom, initialDateTo, onBack }: {
  connectionId: string;
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
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({});
  const [activeFilter, setActiveFilter] = useState<string | null>(null);
  const [filterDraft, setFilterDraft] = useState('');
  const [excludedBusinessKeys, setExcludedBusinessKeys] = useState<string[]>([]);
  const [selectedBusinessKeys, setSelectedBusinessKeys] = useState<string[]>([]);
  const [items, setItems] = useState<ResultItem[]>([]);
  const [page, setPage] = useState<LocalResultPage<ResultItem>['pagination'] | null>(null);
  const [meta, setMeta] = useState<ResultMeta | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [exportOpen, setExportOpen] = useState(false);
  const [exportScopes, setExportScopes] = useState<ResultMode[]>(['overview', 'details']);
  const [exportState, setExportState] = useState<'idle' | 'working'>('idle');
  const [feedback, setFeedback] = useState('');
  const generation = useRef(0);
  const exportRoot = useRef<HTMLDivElement>(null);

  const filterSignature = JSON.stringify(columnFilters);
  const exclusionSignature = JSON.stringify(excludedBusinessKeys);

  const load = useCallback(async (cursor: string | null = null, includeMeta = cursor === null) => {
    const bridge = window.miaRuntime?.results;
    if (!bridge || !connectionId) { setState('error'); return; }
    const token = ++generation.current;
    setState('loading');
    const query = {
      connection_id: connectionId,
      cursor,
      limit: PAGE_SIZE,
      search: debouncedSearch,
      direction: direction || null,
      date_from: dateFrom,
      date_to: dateTo,
      column_filters: columnFilters,
      exclude_business_keys: excludedBusinessKeys,
      include_meta: includeMeta,
    };
    const started = performance.now();
    diagnosticLog('results_request', {
      connection_id: connectionId,
      mode,
      date_from: dateFrom,
      date_to: dateTo,
      direction: direction || null,
      page: cursor ? pageIndex + 1 : 1,
      has_search: Boolean(debouncedSearch),
      column_filter_count: Object.keys(columnFilters).length,
      excluded_invoice_count: excludedBusinessKeys.length,
    });
    try {
      const result = await bridge[mode](query) as LocalResultPage<ResultItem>;
      if (token !== generation.current) return;
      setItems(result.items);
      setPage(result.pagination);
      if (result.meta) setMeta(result.meta);
      setState('ready');
      diagnosticLog('results_response', {
        connection_id: connectionId,
        mode,
        row_count: result.items.length,
        total_rows: result.meta?.total_rows,
        has_more: result.pagination.has_more,
        duration_ms: Math.round(performance.now() - started),
      });
    } catch (error) {
      diagnosticLog('results_failed', {
        connection_id: connectionId,
        mode,
        date_from: dateFrom,
        date_to: dateTo,
        code: (error as { code?: string })?.code,
        duration_ms: Math.round(performance.now() - started),
      }, 'error');
      if (token === generation.current) setState('error');
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionId, dateFrom, dateTo, debouncedSearch, direction, mode, filterSignature, exclusionSignature]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    setCursorHistory([null]);
    setPageIndex(0);
    setSelectedBusinessKeys([]);
    setMeta(null);
    void load(null, true);
  }, [load]);

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (event.target instanceof Element) {
        if (exportOpen && !exportRoot.current?.contains(event.target)) setExportOpen(false);
        if (activeFilter && !event.target.closest('.results-column-filter-wrap')) setActiveFilter(null);
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setExportOpen(false);
        setActiveFilter(null);
      }
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', escape);
    };
  }, [activeFilter, exportOpen]);

  const payloadColumns = useMemo(() => {
    const discovered = meta?.columns ?? [...new Set(items.flatMap((item) => Object.keys(item.payload ?? {})))];
    return orderResultColumns(discovered, mode);
  }, [items, meta?.columns, mode]);

  const visibleBusinessKeys = useMemo(
    () => [...new Set(items.map((item) => item.business_key).filter(Boolean))],
    [items],
  );
  const selectedSet = useMemo(() => new Set(selectedBusinessKeys), [selectedBusinessKeys]);
  const allVisibleSelected = visibleBusinessKeys.length > 0 && visibleBusinessKeys.every((key) => selectedSet.has(key));
  const someVisibleSelected = visibleBusinessKeys.some((key) => selectedSet.has(key));
  const totalPages = Math.max(1, Math.ceil((meta?.total_rows ?? items.length) / PAGE_SIZE));

  function toggleExportScope(scope: ResultMode) {
    setExportScopes((current) => current.includes(scope)
      ? current.filter((item) => item !== scope)
      : [...current, scope]);
  }

  function openColumnFilter(key: string) {
    setFilterDraft(columnFilters[key] ?? '');
    setActiveFilter((current) => current === key ? null : key);
  }

  function applyColumnFilter(key: string) {
    const value = filterDraft.trim();
    setColumnFilters((current) => {
      const next = { ...current };
      if (value) next[key] = value;
      else delete next[key];
      return next;
    });
    setActiveFilter(null);
  }

  function clearColumnFilter(key: string) {
    setFilterDraft('');
    setColumnFilters((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    setActiveFilter(null);
  }

  function toggleBusinessKey(key: string) {
    setSelectedBusinessKeys((current) => current.includes(key)
      ? current.filter((item) => item !== key)
      : [...current, key]);
  }

  function toggleVisibleSelection() {
    setSelectedBusinessKeys((current) => {
      const currentSet = new Set(current);
      if (visibleBusinessKeys.every((key) => currentSet.has(key))) {
        visibleBusinessKeys.forEach((key) => currentSet.delete(key));
      } else {
        visibleBusinessKeys.forEach((key) => currentSet.add(key));
      }
      return [...currentSet];
    });
  }

  function excludeSelectedInvoices() {
    if (selectedBusinessKeys.length === 0) return;
    const count = selectedBusinessKeys.length;
    setExcludedBusinessKeys((current) => [...new Set([...current, ...selectedBusinessKeys])]);
    setSelectedBusinessKeys([]);
    setFeedback(`Đã loại ${count} hóa đơn khỏi danh sách tải xuống. Dữ liệu trong cơ sở dữ liệu không bị xóa.`);
  }

  function restoreExcludedInvoices() {
    setExcludedBusinessKeys([]);
    setSelectedBusinessKeys([]);
    setFeedback('Đã khôi phục toàn bộ hóa đơn vào danh sách tải xuống.');
  }

  async function exportResults() {
    const artifacts = window.miaRuntime?.artifacts;
    if (!artifacts || !connectionId || exportScopes.length === 0) return;
    setFeedback('');
    setExportState('working');
    diagnosticLog('results_export_requested', {
      connection_id: connectionId,
      scopes: exportScopes,
      date_from: dateFrom,
      date_to: dateTo,
      direction: direction || null,
      column_filter_count: Object.keys(columnFilters).length,
      excluded_invoice_count: excludedBusinessKeys.length,
    });
    try {
      const destination = await artifacts.selectDirectory();
      if (!destination) return;
      const result = await artifacts.export({
        destination,
        connection_ids: [connectionId],
        kinds: ['excel'],
        result_scopes: exportScopes,
        date_from: dateFrom,
        date_to: dateTo,
        direction: direction || null,
        search: search.trim(),
        column_filters: columnFilters,
        exclude_business_keys: excludedBusinessKeys,
        filter_scope: mode,
      });
      diagnosticLog('results_export_completed', { connection_id: connectionId, scopes: exportScopes, file_count: result.count });
      setFeedback(`Đã tải ${result.count} file kết quả. ${excludedBusinessKeys.length ? `${excludedBusinessKeys.length} hóa đơn đã loại không được ghi vào file.` : ''}`.trim());
      setExportOpen(false);
    } catch (error) {
      diagnosticLog('results_export_failed', { connection_id: connectionId, scopes: exportScopes, code: (error as { code?: string })?.code }, 'error');
      setFeedback('Không thể tải kết quả. Vui lòng thử lại.');
    } finally {
      setExportState('idle');
    }
  }

  function goNext() {
    const nextCursor = page?.next_cursor;
    if (!page?.has_more || !nextCursor) return;
    const nextIndex = pageIndex + 1;
    setCursorHistory((current) => [...current.slice(0, nextIndex), nextCursor]);
    setPageIndex(nextIndex);
    setSelectedBusinessKeys([]);
    void load(nextCursor, false);
  }

  function goPrevious() {
    if (pageIndex <= 0) return;
    const previousIndex = pageIndex - 1;
    const previousCursor = cursorHistory[previousIndex] ?? null;
    setPageIndex(previousIndex);
    setSelectedBusinessKeys([]);
    void load(previousCursor, previousCursor === null);
  }

  function headerCell(key: string, label: string) {
    const active = Boolean(columnFilters[key]);
    return <div className="results-column-filter-wrap">
      <span>{label}</span>
      <button
        className="results-column-filter-button"
        data-active={active}
        type="button"
        aria-label={`Lọc cột ${label}`}
        aria-expanded={activeFilter === key}
        onClick={() => openColumnFilter(key)}
      >
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3h12l-4.5 5v4.2l-3 1.5V8L2 3Z" /></svg>
      </button>
      {activeFilter === key ? <div className="results-column-filter-popover" role="dialog" aria-label={`Bộ lọc ${label}`}>
        <strong>Lọc {label}</strong>
        <input
          autoFocus
          value={filterDraft}
          placeholder="Tìm trong cột..."
          onChange={(event) => setFilterDraft(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') applyColumnFilter(key); }}
        />
        <div>
          <button type="button" onClick={() => clearColumnFilter(key)}>Xóa lọc</button>
          <button type="button" data-primary onClick={() => applyColumnFilter(key)}>Áp dụng</button>
        </div>
      </div> : null}
    </div>;
  }

  return <section className="results-page results-page--figma" aria-label="Kết quả hóa đơn">
    <button className="results-back" type="button" onClick={onBack}><img src={backIcon} alt="" /> Quay lại Quản lý HDDT</button>
    <header className="results-header results-header--figma">
      <div>
        <h1>Kết quả hóa đơn</h1>
        <p>Dữ liệu đã đồng bộ được đọc trực tiếp từ bộ lưu trữ cục bộ.</p>
      </div>
      <div className="results-export" ref={exportRoot}>
        <button className="results-export-trigger" type="button" aria-expanded={exportOpen} onClick={() => setExportOpen((value) => !value)}><img src={downloadIcon} alt="" /> Tải xuống kết quả</button>
        {exportOpen ? <div className="results-export-popover" role="dialog" aria-label="Chọn nội dung tải xuống">
          <strong>Nội dung file Excel</strong>
          <label><input type="checkbox" checked={exportScopes.includes('overview')} onChange={() => toggleExportScope('overview')} /> Tổng quan</label>
          <label><input type="checkbox" checked={exportScopes.includes('details')} onChange={() => toggleExportScope('details')} /> Chi tiết</label>
          <p>Bộ lọc cột hiện tại áp dụng cho tab đang xem; các hóa đơn đã loại sẽ không xuất ở cả hai sheet.</p>
          <button type="button" disabled={exportState === 'working' || exportScopes.length === 0} onClick={() => void exportResults()}>{exportState === 'working' ? 'Đang tải...' : 'Chọn thư mục và tải'}</button>
        </div> : null}
      </div>
    </header>

    <div className="results-tabs results-tabs--figma" role="tablist" aria-label="Loại kết quả">
      <button role="tab" aria-selected={mode === 'overview'} data-active={mode === 'overview'} onClick={() => { setMode('overview'); setColumnFilters({}); }}>Tổng quan</button>
      <button role="tab" aria-selected={mode === 'details'} data-active={mode === 'details'} onClick={() => { setMode('details'); setColumnFilters({}); }}>Chi tiết</button>
    </div>

    <div className="results-filters results-filters--figma">
      <DateRangePicker dateFrom={dateFrom} dateTo={dateTo} fromLabel="Từ ngày xem" toLabel="Đến ngày xem" onChange={(from, to) => { setDateFrom(from); setDateTo(to); }} />
      <select aria-label="Lọc mua bán" value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}>
        <option value="">Mua vào và bán ra</option>
        <option value="purchase">Mua vào</option>
        <option value="sold">Bán ra</option>
      </select>
      <div className="results-search-wrap">
        <input aria-label="Tìm kiếm kết quả" placeholder="Lọc theo số HĐ, tên KH..." value={search} onChange={(event) => setSearch(event.target.value)} />
        {search ? <button type="button" aria-label="Xóa tìm kiếm" onClick={() => setSearch('')}>×</button> : null}
      </div>
      <button className="results-delete-selected" type="button" disabled={selectedBusinessKeys.length === 0} onClick={excludeSelectedInvoices}>Xóa khỏi tải xuống ({selectedBusinessKeys.length})</button>
      {excludedBusinessKeys.length > 0 ? <button className="results-restore-excluded" type="button" onClick={restoreExcludedInvoices}>Khôi phục {excludedBusinessKeys.length} HĐ</button> : null}
    </div>

    <div className="results-meta-line">
      <span>{meta ? `${meta.total_rows.toLocaleString('vi-VN')} dòng · ${meta.total_invoices.toLocaleString('vi-VN')} hóa đơn` : 'Đang tính tổng dữ liệu...'}</span>
      {Object.keys(columnFilters).length > 0 ? <span>{Object.keys(columnFilters).length} cột đang lọc</span> : null}
      {excludedBusinessKeys.length > 0 ? <span>{excludedBusinessKeys.length} hóa đơn đã loại khỏi tải xuống</span> : null}
    </div>

    {feedback ? <div className="results-feedback" role="status">{feedback}</div> : null}
    {state === 'error' ? <div className="results-state" role="alert">Không thể tải kết quả.<button onClick={() => void load(cursorHistory[pageIndex] ?? null, pageIndex === 0)}>Thử lại</button></div> : null}
    {state === 'loading' && items.length === 0 ? <div className="results-state" role="status">Đang tải...</div> : null}
    {state === 'ready' && items.length === 0 ? <div className="results-state results-empty">Không tồn tại hóa đơn phù hợp bộ lọc trong thời gian này.</div> : null}

    {items.length ? <div className="results-table-shell">
      <table className="results-data-table">
        <thead>
          <tr>
            <th className="results-select-column">
              <label className="results-checkbox" title="Chọn toàn bộ hóa đơn đang hiển thị">
                <input type="checkbox" checked={allVisibleSelected} ref={(node) => { if (node) node.indeterminate = someVisibleSelected && !allVisibleSelected; }} onChange={toggleVisibleSelection} />
                <span />
              </label>
            </th>
            <th className="results-index-column">STT</th>
            <th>{headerCell('__direction', 'Loại')}</th>
            <th>{headerCell('__business_key', 'Mã hóa đơn')}</th>
            {mode === 'details' ? <th>{headerCell('__line_key', 'Dòng')}</th> : null}
            {payloadColumns.map((key) => <th key={key} className={isNumericResultColumn(key) ? 'is-number' : undefined}>{headerCell(key, columnLabel(key, mode))}</th>)}
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => <tr key={resultKey(item)}>
            <td className="results-select-column">
              <label className="results-checkbox" title="Chọn hóa đơn để loại khỏi file tải xuống">
                <input type="checkbox" checked={selectedSet.has(item.business_key)} onChange={() => toggleBusinessKey(item.business_key)} />
                <span />
              </label>
            </td>
            <td className="results-index-column">{pageIndex * PAGE_SIZE + index + 1}</td>
            <td>{item.direction === 'purchase' ? 'Mua vào' : 'Bán ra'}</td>
            <td className="results-business-key">{item.business_key || '—'}</td>
            {mode === 'details' ? <td>{'line_key' in item ? item.line_key : '—'}</td> : null}
            {payloadColumns.map((key) => <td key={key} className={isNumericResultColumn(key) ? 'is-number' : undefined} title={String(item.payload?.[key] ?? '')}>{formatResultValue(key, item.payload?.[key])}</td>)}
          </tr>)}
        </tbody>
        <tfoot>
          <tr>
            <td />
            <td className="results-total-label">Tổng</td>
            <td />
            <td className="results-total-invoices">{meta ? `${meta.total_invoices.toLocaleString('vi-VN')} HĐ` : ''}</td>
            {mode === 'details' ? <td /> : null}
            {payloadColumns.map((key) => <td key={key} className={isTotalResultColumn(key) ? 'is-number results-total-value' : undefined}>{isTotalResultColumn(key) ? formatTotalValue(key, meta?.totals[key]) : ''}</td>)}
          </tr>
        </tfoot>
      </table>
    </div> : null}

    {items.length ? <footer className="results-pagination">
      <span>Trang {pageIndex + 1}/{totalPages}</span>
      <div>
        <button type="button" disabled={pageIndex === 0 || state === 'loading'} onClick={goPrevious}>‹ Trang trước</button>
        <button type="button" disabled={!page?.has_more || state === 'loading'} onClick={goNext}>Trang sau ›</button>
      </div>
    </footer> : null}
  </section>;
}

function resultKey(item: ResultItem) {
  return 'detail_id' in item ? `d-${item.detail_id}-${item.line_key}` : `o-${item.overview_id}`;
}
