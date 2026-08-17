import { useEffect, useMemo, useState } from 'react';
import type { ResultPage } from '../../lib/api/contracts';
import { collectAllResults, matchesResult } from './result-pagination';

type ResultTab = 'overview' | 'details';

export function ResultsPage({ jobId, onBack }: { jobId: string; onBack(): void }) {
  const [tab, setTab] = useState<ResultTab>('overview');
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [query, setQuery] = useState('');
  const [direction, setDirection] = useState('');
  const [state, setState] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading');
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    const bridge = window.miaRuntime?.jobs;
    let active = true;
    setState('loading');
    setItems([]);
    if (!bridge) { setState('error'); return () => { active = false; }; }
    const read = (cursor?: string): Promise<ResultPage<Record<string, unknown>>> => (
      tab === 'overview' ? bridge.overview(jobId, 200, cursor) : bridge.details(jobId, 200, cursor)
    );
    void collectAllResults(read).then((result) => {
      if (!active) return;
      setItems(result.items);
      setState(result.items.length ? 'ready' : 'empty');
    }).catch(() => { if (active) setState('error'); });
    return () => { active = false; };
  }, [jobId, retryKey, tab]);

  const filtered = useMemo(() => items.filter((item) => matchesResult(item, query, direction)), [direction, items, query]);
  const columns = useMemo(() => {
    const preferred = tab === 'overview'
      ? ['shdon', 'khhdon', 'nbmst', 'nlap', 'direction', 'invoice_category']
      : ['stt', 'ten', 'dvtinh', 'sluong', 'dgia', 'thtien'];
    return preferred.filter((key) => items.some((item) => item[key] !== undefined));
  }, [items, tab]);

  return <section className="results-page" aria-label="Kết quả hóa đơn">
    <button className="results-back" type="button" onClick={onBack}>‹ Quay lại Quản lý HDDT</button>
    <header className="results-header"><div><h1>Kết quả tải hóa đơn</h1><p>{items.length} dòng dữ liệu</p></div><button type="button" className="results-export">⇩ Xuất kết quả tất cả</button></header>
    <nav className="results-tabs" aria-label="Loại kết quả">
      <button type="button" data-active={tab === 'overview'} onClick={() => setTab('overview')}>Tổng quan</button>
      <button type="button" data-active={tab === 'details'} onClick={() => setTab('details')}>Chi tiết</button>
    </nav>
    <div className="results-filters">
      <select aria-label="Lọc hướng hóa đơn" value={direction} onChange={(event) => setDirection(event.target.value)}><option value="">Tất cả hóa đơn</option><option value="purchase">Mua vào</option><option value="sold">Bán ra</option></select>
      <input aria-label="Tìm trong kết quả" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Lọc theo số HĐ, tên KH..." />
    </div>
    {state === 'loading' ? <div className="results-state" role="status">Đang tải kết quả…</div> : null}
    {state === 'empty' ? <div className="results-state">Chưa có dữ liệu phù hợp.</div> : null}
    {state === 'error' ? <div className="results-state" role="alert">Không thể tải kết quả.<button type="button" onClick={() => setRetryKey((value) => value + 1)}>Thử lại</button></div> : null}
    {state === 'ready' ? <div className="results-table-wrap"><table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{filtered.map((item, index) => <tr key={String(item.id ?? `${item.shdon}-${item.stt}-${index}`)}>{columns.map((column) => <td key={column}>{String(item[column] ?? '')}</td>)}</tr>)}</tbody></table>{filtered.length === 0 ? <div className="results-state">Không tìm thấy kết quả.</div> : null}</div> : null}
  </section>;
}
