const NUMBER_FIELDS = new Set([
  'tgtcthue', 'tgtthue', 'ttcktmai', 'tgtphi', 'tgtttbso',
  'tgia', 'dgia', 'stckhau', 'thtien', 'tthue', 'sluong',
]);
const PERCENT_FIELDS = new Set(['tsuat']);
export const MONETARY_FIELDS = new Set([
  'tgtcthue', 'tgtthue', 'ttcktmai', 'tgtphi', 'tgtttbso',
  'dgia', 'stckhau', 'thtien', 'tthue',
]);

function numericValue(value: unknown) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const text = value.trim().replace(/%$/, '').replace(/\s/g, '');
  if (!text) return null;
  const normalized = text.includes(',')
    ? text.replace(/\./g, '').replace(',', '.')
    : text;
  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

export function formatVietnameseNumber(value: unknown) {
  const number = numericValue(value);
  if (number === null) return value === null || value === undefined || value === '' ? '—' : String(value);
  return new Intl.NumberFormat('vi-VN', {
    useGrouping: true,
    minimumFractionDigits: 0,
    maximumFractionDigits: 20,
  }).format(number);
}

export function formatMoney(value: unknown) {
  const number = numericValue(value);
  if (number === null) return value === null || value === undefined || value === '' ? '—' : String(value);
  return new Intl.NumberFormat('vi-VN', {
    useGrouping: true,
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(number);
}

export function formatTaxRate(value: unknown) {
  if (value === null || value === undefined || value === '') return '—';
  const text = String(value).trim();
  if (text.endsWith('%')) return text;
  return `${formatVietnameseNumber(value)}%`;
}

export function formatResultCell(column: string, value: unknown, declaredType?: string) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Có' : 'Không';
  if (PERCENT_FIELDS.has(column) || declaredType === 'percent') return formatTaxRate(value);
  if (MONETARY_FIELDS.has(column) || /^(overview|detail|difference)_(tgtcthue|tgtthue|thtien|tthue|ttcktmai|tgtphi|tgtttbso)$/.test(column)) return formatMoney(value);
  if (NUMBER_FIELDS.has(column) || declaredType === 'number') return formatVietnameseNumber(value);
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function resultColumnType(column: string): 'text' | 'number' | 'percent' {
  if (PERCENT_FIELDS.has(column)) return 'percent';
  if (NUMBER_FIELDS.has(column)) return 'number';
  return 'text';
}
