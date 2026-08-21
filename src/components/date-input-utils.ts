export const LAST_SYNC_DATE_RANGE_KEY = 'mia:last-sync-date-range';

export interface StoredDateRange {
  dateFrom: string;
  dateTo: string;
}

export function displayDate(value: string) {
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}/${month}/${year}` : '';
}

export function parseDateText(value: string) {
  const match = value.trim().match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (!match) return null;
  const day = Number(match[1]);
  const month = Number(match[2]);
  const year = Number(match[3]);
  if (year < 1900 || year > 9999 || month < 1 || month > 12 || day < 1 || day > daysInMonth(year, month)) return null;
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

export function normalizeDateText(value: string) {
  const parsed = parseDateText(value);
  return parsed ? displayDate(parsed) : value.trim();
}

export type DatePart = 'day' | 'month' | 'year';

export function datePartAtCaret(value: string, caret: number): DatePart {
  const firstSlash = value.indexOf('/');
  const secondSlash = value.indexOf('/', firstSlash + 1);
  if (firstSlash < 0 || caret <= firstSlash) return 'day';
  if (secondSlash < 0 || caret <= secondSlash) return 'month';
  return 'year';
}

export function adjustDateText(value: string, part: DatePart, delta: number) {
  const iso = parseDateText(value);
  if (!iso || !Number.isInteger(delta) || delta === 0) return null;
  const [yearText, monthText, dayText] = iso.split('-');
  let year = Number(yearText);
  let month = Number(monthText);
  let day = Number(dayText);

  if (part === 'day') {
    const date = new Date(Date.UTC(year, month - 1, day));
    date.setUTCDate(date.getUTCDate() + delta);
    year = date.getUTCFullYear();
    month = date.getUTCMonth() + 1;
    day = date.getUTCDate();
  } else if (part === 'month') {
    const index = year * 12 + (month - 1) + delta;
    year = Math.floor(index / 12);
    month = ((index % 12) + 12) % 12 + 1;
    day = Math.min(day, daysInMonth(year, month));
  } else {
    year += delta;
    if (year < 1900 || year > 9999) return null;
    day = Math.min(day, daysInMonth(year, month));
  }

  if (year < 1900 || year > 9999) return null;
  return `${String(day).padStart(2, '0')}/${String(month).padStart(2, '0')}/${String(year).padStart(4, '0')}`;
}

export function persistSyncDateRange(dateFrom: string, dateTo: string) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(LAST_SYNC_DATE_RANGE_KEY, JSON.stringify({ dateFrom, dateTo }));
  } catch {
    // Storage is an optional convenience; the picker still works without it.
  }
}

export function readLastSyncDateRange(): StoredDateRange | null {
  if (typeof window === 'undefined') return null;
  try {
    const value = JSON.parse(window.localStorage.getItem(LAST_SYNC_DATE_RANGE_KEY) ?? 'null') as Partial<StoredDateRange> | null;
    if (!value || typeof value.dateFrom !== 'string' || typeof value.dateTo !== 'string') return null;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value.dateFrom) || !/^\d{4}-\d{2}-\d{2}$/.test(value.dateTo) || value.dateFrom > value.dateTo) return null;
    return { dateFrom: value.dateFrom, dateTo: value.dateTo };
  } catch {
    return null;
  }
}

function daysInMonth(year: number, month: number) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}
