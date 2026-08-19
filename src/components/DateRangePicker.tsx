import { useEffect, useRef, useState } from 'react';
import calendarIcon from '../assets/figma/artifact-calendar.svg';

interface DateRangePickerProps {
  dateFrom: string;
  dateTo: string;
  onChange(dateFrom: string, dateTo: string): void;
  className?: string;
  fromLabel?: string;
  toLabel?: string;
}

function displayDate(value: string) {
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}/${month}/${year}` : '';
}

function parseDate(value: string) {
  const match = value.trim().match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (!match) return null;
  const [, day, month, year] = match;
  const iso = `${year}-${month}-${day}`;
  const parsed = new Date(`${iso}T00:00:00`);
  return parsed.getFullYear() === Number(year)
    && parsed.getMonth() + 1 === Number(month)
    && parsed.getDate() === Number(day) ? iso : null;
}

export function DateRangePicker({ dateFrom, dateTo, onChange, className = '', fromLabel = 'Từ ngày', toLabel = 'Đến ngày' }: DateRangePickerProps) {
  const [open, setOpen] = useState(false);
  const [fromText, setFromText] = useState(displayDate(dateFrom));
  const [toText, setToText] = useState(displayDate(dateTo));
  const [error, setError] = useState('');
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, [open]);

  useEffect(() => { setFromText(displayDate(dateFrom)); }, [dateFrom]);
  useEffect(() => { setToText(displayDate(dateTo)); }, [dateTo]);

  function apply() {
    const nextFrom = parseDate(fromText);
    const nextTo = parseDate(toText);
    if (!nextFrom || !nextTo) { setError('Nhập ngày theo định dạng dd/mm/yyyy.'); return; }
    if (nextFrom > nextTo) { setError('Ngày bắt đầu không được sau ngày kết thúc.'); return; }
    setError('');
    onChange(nextFrom, nextTo);
    setOpen(false);
  }

  return <div ref={root} className={`date-range-control ${className}`.trim()}>
    <button className="date-range-trigger" type="button" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen((value) => !value)}>
      <img src={calendarIcon} alt="" /><span><small>KHOẢNG THỜI GIAN</small><strong>{displayDate(dateFrom)} - {displayDate(dateTo)}</strong></span>
    </button>
    {open ? <div className="date-range-popover" role="dialog" aria-label="Chọn khoảng thời gian">
      <label>{fromLabel}<span><input aria-label={`${fromLabel} nhập tay`} inputMode="numeric" placeholder="dd/mm/yyyy" value={fromText} onChange={(event) => setFromText(event.target.value)} /><input aria-label={`${fromLabel} chọn lịch`} type="date" value={parseDate(fromText) ?? ''} max={parseDate(toText) ?? undefined} onChange={(event) => setFromText(displayDate(event.target.value))} /></span></label>
      <label>{toLabel}<span><input aria-label={`${toLabel} nhập tay`} inputMode="numeric" placeholder="dd/mm/yyyy" value={toText} onChange={(event) => setToText(event.target.value)} /><input aria-label={`${toLabel} chọn lịch`} type="date" value={parseDate(toText) ?? ''} min={parseDate(fromText) ?? undefined} onChange={(event) => setToText(displayDate(event.target.value))} /></span></label>
      {error ? <p role="alert">{error}</p> : null}
      <div><button type="button" onClick={() => setOpen(false)}>Hủy</button><button type="button" onClick={apply}>Áp dụng</button></div>
    </div> : null}
  </div>;
}
