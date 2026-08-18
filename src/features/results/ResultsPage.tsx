import { useCallback, useEffect, useRef, useState } from 'react';
import type { DetailResult, LocalResultPage, OverviewResult } from '../../lib/runtime-bridge';

type ResultItem = OverviewResult | DetailResult;

export function ResultsPage({ connectionId, onBack }: { connectionId: string; onBack(): void }) {
  const [mode, setMode] = useState<'overview' | 'details'>('overview');
  const [direction, setDirection] = useState<'purchase' | 'sold' | ''>('');
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [items, setItems] = useState<ResultItem[]>([]);
  const [page, setPage] = useState<LocalResultPage<ResultItem>['pagination'] | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const generation = useRef(0);
  const consumedCursors = useRef(new Set<string>());

  const load = useCallback(async (cursor: string | null = null, append = false) => {
    const bridge = window.miaRuntime?.results;
    if (!bridge) { setState('error'); return; }
    if (append && cursor && consumedCursors.current.has(cursor)) { setPage((current) => current ? { ...current, has_more: false, next_cursor: null } : current); return; }
    if (!append) { generation.current += 1; consumedCursors.current.clear(); }
    const token = generation.current;
    if (cursor) consumedCursors.current.add(cursor);
    setState('loading');
    try {
      const query = { connection_id: connectionId, cursor, limit: 50, search: debouncedSearch, direction: direction || null };
      const result = await bridge[mode](query) as LocalResultPage<ResultItem>;
      if (token !== generation.current) return;
      setItems((current) => append ? [...current, ...result.items.filter((item) => !current.some((old) => resultKey(old) === resultKey(item)))] : result.items);
      setPage(result.pagination);
      setState('ready');
    } catch { if (token === generation.current) setState('error'); }
  }, [connectionId, debouncedSearch, direction, mode]);

  useEffect(() => { const timer = setTimeout(() => setDebouncedSearch(search), 250); return () => clearTimeout(timer); }, [search]);
  useEffect(() => { void load(); }, [load]);

  return <section className="results-page" aria-label="Kết quả hóa đơn">
    <header className="results-header"><button type="button" onClick={onBack}>← Quay lại</button><h1>Kết quả hóa đơn</h1></header>
    <div className="results-tabs"><button data-active={mode === 'overview'} onClick={() => setMode('overview')}>Tổng quan</button><button data-active={mode === 'details'} onClick={() => setMode('details')}>Chi tiết</button></div>
    <div className="results-filters"><input aria-label="Tìm kiếm kết quả" placeholder="Tìm kiếm hóa đơn..." value={search} onChange={(event) => setSearch(event.target.value)} />{search ? <button type="button" aria-label="Xóa tìm kiếm" onClick={() => setSearch('')}>×</button> : null}<select aria-label="Lọc mua bán" value={direction} onChange={(event) => setDirection(event.target.value as typeof direction)}><option value="">Mua vào và bán ra</option><option value="purchase">Mua vào</option><option value="sold">Bán ra</option></select></div>
    {state === 'error' ? <div className="results-state" role="alert">Không thể tải kết quả.<button onClick={() => void load()}>Thử lại</button></div> : null}
    {state === 'loading' && items.length === 0 ? <div className="results-state" role="status">Đang tải...</div> : null}
    {state === 'ready' && items.length === 0 ? <div className="results-state">Chưa có dữ liệu.</div> : null}
    {items.length ? <div className="results-table"><div className="results-row results-row--header"><span>Loại</span><span>Mã hóa đơn</span><span>Nội dung</span></div>{items.map((item) => <div className="results-row" key={resultKey(item)}><span>{item.direction === 'purchase' ? 'Mua vào' : 'Bán ra'}</span><strong>{item.business_key}</strong><span>{JSON.stringify(item.payload)}</span></div>)}</div> : null}
    {page?.has_more ? <button className="results-more" disabled={state === 'loading'} onClick={() => void load(page.next_cursor, true)}>Tải thêm</button> : null}
  </section>;
}

function resultKey(item: ResultItem) { return 'detail_id' in item ? `d-${item.detail_id}` : `o-${item.overview_id}`; }
