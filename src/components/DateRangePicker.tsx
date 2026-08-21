import { useEffect, useRef, useState, type KeyboardEvent, type WheelEvent } from 'react';
import calendarIcon from '../assets/figma/artifact-calendar.svg';
import {
  adjustDateText,
  datePartAtCaret,
  displayDate,
  normalizeDateText,
  parseDateText,
  persistSyncDateRange,
} from './date-input-utils';

interface DateRangePickerProps {
  dateFrom: string;
  dateTo: string;
  onChange(dateFrom: string, dateTo: string): void;
  className?: string;
  fromLabel?: string;
  toLabel?: string;
}

export function DateRangePicker({ dateFrom, dateTo, onChange, className = '', fromLabel = 'Từ ngày', toLabel = 'Đến ngày' }: DateRangePickerProps) {
  const [open, setOpen] = useState(false);
  const [fromText, setFromText] = useState(displayDate(dateFrom));
  const [toText, setToText] = useState(displayDate(dateTo));
  const [error, setError] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const isSyncPicker = fromLabel.toLocaleLowerCase('vi').includes('đồng bộ')
    || toLabel.toLocaleLowerCase('vi').includes('đồng bộ');

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, [open]);

  useEffect(() => { setFromText(displayDate(dateFrom)); }, [dateFrom]);
  useEffect(() => { setToText(displayDate(dateTo)); }, [dateTo]);
  useEffect(() => {
    if (isSyncPicker) persistSyncDateRange(dateFrom, dateTo);
  }, [dateFrom, dateTo, isSyncPicker]);

  function apply() {
    const nextFrom = parseDateText(fromText);
    const nextTo = parseDateText(toText);
    if (!nextFrom || !nextTo) { setError('Nhập ngày theo định dạng d/m/yyyy hoặc dd/mm/yyyy.'); return; }
    if (nextFrom > nextTo) { setError('Ngày bắt đầu không được sau ngày kết thúc.'); return; }
    setError('');
    setFromText(displayDate(nextFrom));
    setToText(displayDate(nextTo));
    if (isSyncPicker) persistSyncDateRange(nextFrom, nextTo);
    onChange(nextFrom, nextTo);
    setOpen(false);
  }

  function adjustInput(event: WheelEvent<HTMLInputElement> | KeyboardEvent<HTMLInputElement>, setter: (value: string) => void, delta: number) {
    const input = event.currentTarget;
    const part = datePartAtCaret(input.value, input.selectionStart ?? input.value.length);
    const next = adjustDateText(input.value, part, delta);
    if (!next) return;
    event.preventDefault();
    setter(next);
    const firstSlash = next.indexOf('/');
    const secondSlash = next.indexOf('/', firstSlash + 1);
    const position = part === 'day' ? 1 : part === 'month' ? firstSlash + 2 : secondSlash + 3;
    requestAnimationFrame(() => input.setSelectionRange(position, position));
  }

  function onWheel(event: WheelEvent<HTMLInputElement>, setter: (value: string) => void) {
    if (event.deltaY === 0) return;
    adjustInput(event, setter, event.deltaY < 0 ? 1 : -1);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>, setter: (value: string) => void) {
    if (event.key === 'ArrowUp') adjustInput(event, setter, 1);
    if (event.key === 'ArrowDown') adjustInput(event, setter, -1);
  }

  function normalize(value: string, setter: (value: string) => void) {
    setter(normalizeDateText(value));
  }

  return <div ref={root} className={`date-range-control ${className}`.trim()}>
    <button className="date-range-trigger" type="button" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen((value) => !value)}>
      <img src={calendarIcon} alt="" /><span><small>KHOẢNG THỜI GIAN</small><strong>{displayDate(dateFrom)} - {displayDate(dateTo)}</strong></span>
    </button>
    {open ? <div className="date-range-popover" role="dialog" aria-label="Chọn khoảng thời gian">
      <label>{fromLabel}<span><input aria-label={`${fromLabel} nhập tay`} inputMode="numeric" placeholder="d/m/yyyy" value={fromText} onChange={(event) => setFromText(event.target.value.replace(/[^0-9/]/g, ''))} onBlur={() => normalize(fromText, setFromText)} onWheel={(event) => onWheel(event, setFromText)} onKeyDown={(event) => onKeyDown(event, setFromText)} /><input aria-label={`${fromLabel} chọn lịch`} type="date" value={parseDateText(fromText) ?? ''} max={parseDateText(toText) ?? undefined} onChange={(event) => setFromText(displayDate(event.target.value))} /></span></label>
      <label>{toLabel}<span><input aria-label={`${toLabel} nhập tay`} inputMode="numeric" placeholder="d/m/yyyy" value={toText} onChange={(event) => setToText(event.target.value.replace(/[^0-9/]/g, ''))} onBlur={() => normalize(toText, setToText)} onWheel={(event) => onWheel(event, setToText)} onKeyDown={(event) => onKeyDown(event, setToText)} /><input aria-label={`${toLabel} chọn lịch`} type="date" value={parseDateText(toText) ?? ''} min={parseDateText(fromText) ?? undefined} onChange={(event) => setToText(displayDate(event.target.value))} /></span></label>
      {error ? <p role="alert">{error}</p> : null}
      <div><button type="button" onClick={() => setOpen(false)}>Hủy</button><button type="button" onClick={apply}>Áp dụng</button></div>
    </div> : null}
  </div>;
}
