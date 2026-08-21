import { useCallback, useEffect, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { readLastSyncDateRange } from '../../components/date-input-utils';
import downloadIcon from '../../assets/figma/artifact-download.svg';
import backIcon from '../../assets/figma/back.png';
import type { DetailResult, LocalResultPage, OverviewResult } from '../../lib/runtime-bridge';
import '../../styles/results-enhancements.css';

type ResultMode = 'overview' | 'details';
type ResultItem = OverviewResult | DetailResult;

const DEFAULT_RANGE = { dateFrom: '2023-10-01', dateTo: '2023-10-31' };

export function ResultsPage({ connectionId, onBack }: { connectionId: string; onBack(): void }) {
  const initialRange = useRef(readLastSyncDateRange() ?? DEFAULT_RANGE).current;
  const [mode, setMode] = useState<ResultMode>('overview');
  const [direction, setDirection] = useState<'purchase' | 'sold' | ''>('');
  const [dateFrom, setDateFrom] = useState(initialRange.dateFrom);
  const [dateTo, setDateTo] = useState(initialRange.dateTo);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [items, setItems] = useState<ResultItem[]>([]);
  const [page, setPage] = useState<LocalResultPage<ResultItem>['pagination'] | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [exportOpen, setExportOpen] = useState(false);
  const [exportScopes, setExportScopes] = useState<ResultMode[]>(['overview', 'details']);
  const [exportState, setExportState] = useState<'idle' | 'working'>('idle');
  const [feedback, setFeedback] = useState('');
  const generation = useRef(0);
  const consumedCursors = useRef(new Set<string>());
  const exportRoot = useRef<HTMLDivElement>(null);

  const load = useCallback(async (cursor: string | null = null, append = false) => {
    const bridge = window.miaRuntime?.results;
    if (!bridge || !connectionId) { setState('error'); return; }
    if (append && cursor && consumedCursors.current.has(cursor)) {
      setPage((current) => current ? { ...current, has_more: false, next_cursor: null } : current);
      return;
    }
    if (!append) { generation.current += 1; consumedCursors.current.clear(); }
    const token = generation.current;
    if (cursor) consumedCursors.current.add(cursor);
    setState('loading');
    try {
      const query = {
        connection_id: connectionId,
        cursor,
        limit: 50,
        search: debouncedSearch,
        direction: direction || null,
        date_from: dateFrom,
        date_to: dateTo,
      };
      const result = await bridge[mode](query) as LocalResultPage<ResultItem>;
      if (token !== generation.current) return;
      setItems((current) => append
        ? [...current, ...result.items.filter((item) => !current.some((old) => resultKey(old) === resultKey(item)))]
        : result.items);
      setPage(result.pagination);
      setState('ready');
    } catch {
      if (token === generation.current) setState('error');
    }
  }, [connectionId, dateFrom, dateTo, debouncedSearch, direction, mode]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(timer);
  }, [search]);
  useEffect(() => { void load(); }, [load]);
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
    setFeedback('');
    setExportState('working');
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
        search: debouncedSearch,
      });
      setFeedback(`Đã tải ${result.count} file kết quả.`);
      setExportOpen(false);
    } catch {
      setFeedback('Không thể tải kết quả. Vui lòng thử lại.');
    } finally {
      setExportState('idle');
    }
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
          <button type="button" disabled={exportState === 'working' || exportScopes.length === 0} onClick={() => void exportResults()}>{exportState === 'working' ? 'Đang tải...' : 'Chọn thư mục và tải'}</button>
        </div> : null}
      </div>
    </header>

    <div className="results-tabs results-tabs--figma" role="tablist" aria-label="Loại kết quả">
      <button role="tab" aria-selected={mode === 'overview'} data-active={mode === 'overview'} onClick={() => setMode('overview')}>Tổng quan</button>
      <button role="tab" aria-selected={mode === 'details'} data-active={mode === 'details'} onClick={() => setMode('details')}>Chi tiết</button>
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
    </div>

    {feedback ? <div className="results-feedback" role="status">{feedback}</div> : null}
    {state === 'error' ? <div className="results-state" role="alert">Không thể tải kết quả.<button onClick={() => void load()}>Thử lại</button></div> : null}
    {state === 'loading' && items.length === 0 ? <div className="results-state" role="status">Đang tải...</div> : null}
    {state === 'ready' && items.length === 0 ? <div className="results-state results-empty">Không tồn tại hóa đơn trong thời gian này.</div> : null}
    {items.length ? <div className="results-table results-table--figma">
      <div className="results-row results-row--header"><span>Loại</span><span>Mã hóa đơn</span><span>Nội dung</span></div>
      {items.map((item) => <div className="results-row" key={resultKey(item)}>
        <span>{item.direction === 'purchase' ? 'Mua vào' : 'Bán ra'}</span>
        <strong>{item.business_key || '—'}</strong>
        <span title={JSON.stringify(item.payload)}>{resultSummary(item)}</span>
      </div>)}
    </div> : null}
    {page?.has_more ? <button className="results-more" disabled={state === 'loading'} onClick={() => void load(page.next_cursor, true)}>Tải thêm</button> : null}
  </section>;
}

function resultKey(item: ResultItem) {
  return 'detail_id' in item ? `d-${item.detail_id}-${item.line_key}` : `o-${item.overview_id}`;
}

function resultSummary(item: ResultItem) {
  const payload = item.payload;
  const values = [payload.nbmten, payload.nmmten, payload.tgia, payload.tgtcthue, payload.tgtttbso]
    .filter((value) => value !== null && value !== undefined && String(value).trim());
  return values.length ? values.map(String).join(' · ') : JSON.stringify(payload);
}
