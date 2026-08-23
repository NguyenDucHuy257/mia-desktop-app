import { useEffect, useMemo, useRef, useState } from 'react';
import type { ColumnFilterRule, ResultFacetResponse } from '../../lib/runtime-bridge';
import { formatResultCell } from './result-presentation';

export function ColumnFilterPopover({ column, label, active, loadValues, onApply, onClear }: {
  column: string;
  label: string;
  active?: ColumnFilterRule;
  loadValues(): Promise<ResultFacetResponse>;
  onApply(rule: ColumnFilterRule): void;
  onClear(): void;
}) {
  const [open, setOpen] = useState(false);
  const [facet, setFacet] = useState<ResultFacetResponse | null>(null);
  const [search, setSearch] = useState('');
  const [operator, setOperator] = useState<ColumnFilterRule['operator']>();
  const [operand, setOperand] = useState('');
  const [operandTo, setOperandTo] = useState('');
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void loadValues().then((value) => { if (!cancelled) setFacet(value); });
    const close = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    return () => { cancelled = true; document.removeEventListener('pointerdown', close); };
  }, [loadValues, open]);

  useEffect(() => {
    if (!open) return;
    setSelected(active?.values ? new Set(active.values.map(valueKey)) : null);
    setSearch(active?.search ?? '');
    setOperator(active?.operator);
    setOperand(String(active?.value ?? ''));
    setOperandTo(String(active?.value_to ?? ''));
  }, [active, open]);

  const visible = useMemo(() => (facet?.values ?? []).filter((value) => (
    String(value ?? '').toLocaleLowerCase('vi').includes(search.toLocaleLowerCase('vi'))
  )), [facet, search]);
  const allSelected = selected === null || visible.every((value) => selected.has(valueKey(value)));

  return <div className="column-filter" ref={root}>
    <button type="button" className="column-filter-trigger" data-active={Boolean(active)} aria-label={`Lọc cột ${label}`} onClick={() => setOpen(value => !value)}>▾</button>
    {open ? <div className="column-filter-menu" role="dialog" aria-label={`Bộ lọc ${label}`}>
      <strong>{label}</strong>
      <input aria-label={`Tìm trong cột ${label}`} placeholder="Tìm kiếm..." value={search} onChange={event => setSearch(event.target.value)} />
      {facet && facet.column_type !== 'text' ? <div className="column-filter-condition">
        <select aria-label={`Điều kiện số ${label}`} value={operator ?? ''} onChange={event => setOperator(event.target.value as ColumnFilterRule['operator'] || undefined)}>
          <option value="">Không có điều kiện</option>
          <option value="number_equals">Bằng</option><option value="gt">Lớn hơn</option><option value="gte">Lớn hơn hoặc bằng</option>
          <option value="lt">Nhỏ hơn</option><option value="lte">Nhỏ hơn hoặc bằng</option><option value="between">Trong khoảng</option>
        </select>
        {operator ? <input aria-label={`Giá trị lọc ${label}`} inputMode="decimal" value={operand} onChange={event => setOperand(event.target.value)} /> : null}
        {operator === 'between' ? <input aria-label={`Giá trị lọc đến ${label}`} inputMode="decimal" value={operandTo} onChange={event => setOperandTo(event.target.value)} /> : null}
      </div> : null}
      <label className="column-filter-select-all"><input type="checkbox" checked={allSelected} onChange={() => {
        if (allSelected) setSelected(new Set());
        else setSelected(new Set((facet?.values ?? []).map(valueKey)));
      }} /> Chọn tất cả</label>
      <div className="column-filter-values">
        {!facet ? <span>Đang tải…</span> : visible.map(value => {
          const key = valueKey(value);
          return <label key={key}><input type="checkbox" checked={selected === null || selected.has(key)} onChange={() => {
            const next = new Set(selected ?? (facet?.values ?? []).map(valueKey));
            next.has(key) ? next.delete(key) : next.add(key);
            setSelected(next);
          }} /> <span title={String(value ?? '')}>{formatResultCell(column, value, facet.column_type)}</span></label>;
        })}
      </div>
      {facet?.truncated ? <small>Hiển thị 250 giá trị đầu; nội dung tìm kiếm vẫn áp dụng trên toàn bộ dữ liệu.</small> : null}
      <div className="column-filter-actions">
        <button type="button" onClick={() => { onClear(); setOpen(false); }}>Xóa lọc</button>
        <button type="button" onClick={() => setOpen(false)}>Hủy</button>
        <button type="button" onClick={() => {
          const all = facet?.values ?? [];
          const values = selected === null || selected.size === all.length
            ? undefined
            : all.filter(value => selected.has(valueKey(value))) as Array<string | number | boolean | null>;
          onApply({
            ...(values ? { values } : {}), ...(search ? { search } : {}),
            ...(operator && operand ? { operator, value: operand } : {}),
            ...(operator === 'between' && operandTo ? { value_to: operandTo } : {}),
          });
          setOpen(false);
        }}>Áp dụng</button>
      </div>
    </div> : null}
  </div>;
}

function valueKey(value: unknown) {
  return `${typeof value}:${JSON.stringify(value)}`;
}
