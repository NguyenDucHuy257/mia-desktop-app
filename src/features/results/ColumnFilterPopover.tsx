import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ColumnFilterRule, ResultFacetResponse, ResultSort } from '../../lib/runtime-bridge';
import { formatResultCell } from './result-presentation';

const MENU_WIDTH = 310;

export function ColumnFilterPopover({ column, label, active, sort, loadValues, onApply, onClear, onSort }: {
  column: string;
  label: string;
  active?: ColumnFilterRule;
  sort?: ResultSort;
  loadValues(): Promise<ResultFacetResponse>;
  onApply(rule: ColumnFilterRule): void;
  onClear(): void;
  onSort(direction: 'asc' | 'desc'): void;
}) {
  const [open, setOpen] = useState(false);
  const [facet, setFacet] = useState<ResultFacetResponse | null>(null);
  const [search, setSearch] = useState('');
  const [operator, setOperator] = useState<ColumnFilterRule['operator']>();
  const [operand, setOperand] = useState('');
  const [operandTo, setOperandTo] = useState('');
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void loadValues().then(value => { if (!cancelled) setFacet(value); });
    const reposition = () => {
      const rect = button.current?.getBoundingClientRect();
      if (!rect) return;
      const left = Math.max(8, Math.min(window.innerWidth - MENU_WIDTH - 8, rect.right - MENU_WIDTH));
      const below = rect.bottom + 6;
      const top = below + 500 <= window.innerHeight ? below : Math.max(8, rect.top - 506);
      setPosition({ top, left });
    };
    const closeOutside = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && !button.current?.contains(target) && !menu.current?.contains(target)) setOpen(false);
    };
    const closeEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    reposition();
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeEscape);
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    return () => {
      cancelled = true;
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeEscape);
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
    };
  }, [loadValues, open]);

  useEffect(() => {
    if (!open) return;
    setSelected(active?.values ? new Set(active.values.map(valueKey)) : null);
    setSearch(active?.search ?? '');
    setOperator(active?.operator);
    setOperand(String(active?.value ?? ''));
    setOperandTo(String(active?.value_to ?? ''));
  }, [active, open]);

  const visible = useMemo(() => (facet?.values ?? []).filter(value => (
    String(value ?? '').toLocaleLowerCase('vi').includes(search.toLocaleLowerCase('vi'))
  )), [facet, search]);
  const allSelected = selected === null || visible.every(value => selected.has(valueKey(value)));
  const activeState = Boolean(active) || sort?.column === column;

  const popover = open ? <div className="result-column-filter-menu" role="dialog" aria-label={`Bộ lọc ${label}`} data-column={column} ref={menu} style={{ top: position.top, left: position.left }}>
    <div className="result-filter-sort-actions">
      <button type="button" data-active={sort?.column === column && sort.direction === 'asc'} onClick={() => { onSort('asc'); setOpen(false); }}><SortAscendingIcon /> Sắp xếp tăng dần</button>
      <button type="button" data-active={sort?.column === column && sort.direction === 'desc'} onClick={() => { onSort('desc'); setOpen(false); }}><SortDescendingIcon /> Sắp xếp giảm dần</button>
    </div>
    <div className="result-filter-divider" />
    <strong>Bộ lọc — {label}</strong>
    <input aria-label={`Tìm trong cột ${label}`} placeholder="Tìm kiếm..." value={search} onChange={event => setSearch(event.target.value)} />
    <div className="result-filter-condition">
      <select aria-label={`Điều kiện ${label}`} value={operator ?? ''} onChange={event => setOperator(event.target.value as ColumnFilterRule['operator'] || undefined)}>
        <option value="">Không có điều kiện</option>
        {facet?.column_type === 'text' ? <>
          <option value="contains">Chứa</option><option value="not_contains">Không chứa</option><option value="starts_with">Bắt đầu bằng</option>
          <option value="ends_with">Kết thúc bằng</option><option value="equals">Bằng chính xác</option><option value="not_equals">Khác</option>
        </> : <>
          <option value="number_equals">Bằng</option><option value="not_equals">Khác</option><option value="gt">Lớn hơn</option>
          <option value="gte">Lớn hơn hoặc bằng</option><option value="lt">Nhỏ hơn</option><option value="lte">Nhỏ hơn hoặc bằng</option><option value="between">Trong khoảng</option>
        </>}
      </select>
      {operator ? <input aria-label={`Giá trị lọc ${label}`} inputMode={facet?.column_type === 'text' ? 'text' : 'decimal'} value={operand} onChange={event => setOperand(event.target.value)} /> : null}
      {operator === 'between' ? <input aria-label={`Giá trị lọc đến ${label}`} inputMode="decimal" value={operandTo} onChange={event => setOperandTo(event.target.value)} /> : null}
    </div>
    <label className="result-filter-select-all"><input type="checkbox" checked={allSelected} onChange={() => {
      if (allSelected) setSelected(new Set());
      else setSelected(new Set((facet?.values ?? []).map(valueKey)));
    }} /> (Chọn tất cả)</label>
    <div className="result-filter-values">
      {!facet ? <span>Đang tải…</span> : visible.map(value => {
        const key = valueKey(value);
        return <label key={key}><input type="checkbox" checked={selected === null || selected.has(key)} onChange={() => {
          const next = new Set(selected ?? (facet?.values ?? []).map(valueKey));
          next.has(key) ? next.delete(key) : next.add(key);
          setSelected(next);
        }} /> <span title={String(value ?? '')}>{formatResultCell(column, value, facet.column_type)}</span></label>;
      })}
    </div>
    {facet?.truncated ? <small>Hiển thị 250 giá trị đầu; nội dung tìm kiếm áp dụng trên toàn bộ dữ liệu.</small> : null}
    <div className="result-filter-actions">
      <button type="button" onClick={() => { onClear(); setOpen(false); }}>Xóa bộ lọc</button><span />
      <button type="button" onClick={() => setOpen(false)}>Hủy</button>
      <button type="button" onClick={() => {
        const all = facet?.values ?? [];
        const values = selected === null || selected.size === all.length ? undefined : all.filter(value => selected.has(valueKey(value))) as Array<string | number | boolean | null>;
        onApply({
          ...(values ? { values } : {}), ...(search ? { search } : {}),
          ...(operator && operand ? { operator, value: operand } : {}),
          ...(operator === 'between' && operandTo ? { value_to: operandTo } : {}),
        });
        setOpen(false);
      }}>Áp dụng</button>
    </div>
  </div> : null;

  return <div className="result-column-filter">
    <button type="button" ref={button} className="result-column-filter-button" data-active={activeState} aria-expanded={open} aria-label={`Lọc cột ${label}`} onClick={event => { event.stopPropagation(); setOpen(value => !value); }}><FilterIcon /></button>
    {popover ? createPortal(popover, document.body) : null}
  </div>;
}

function valueKey(value: unknown) { return `${typeof value}:${JSON.stringify(value)}`; }
function FilterIcon() { return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.2 3h11.6L9.4 8.1v3.7l-2.8 1.3v-5z" /></svg>; }
function SortAscendingIcon() { return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 13V3m0 0L2.5 5.5M5 3l2.5 2.5M10 5h4m-4 3h3m-3 3h2" /></svg>; }
function SortDescendingIcon() { return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3v10m0 0l-2.5-2.5M5 13l2.5-2.5M10 5h2m-2 3h3m-3 3h4" /></svg>; }
