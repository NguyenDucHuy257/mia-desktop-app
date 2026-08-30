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
  if (typeof value === 'number') return Number.isFinite(value) ? (value === 0 ? 0 : value) : null;
  if (typeof value !== 'string') return null;
  const text = value.trim().replace(/%$/, '').replace(/\s/g, '');
  if (!text) return null;
  const sign = /^[+-]/.test(text) ? text[0] : '';
  const unsigned = sign ? text.slice(1) : text;
  const dots = unsigned.split('.').length - 1;
  const commas = unsigned.split(',').length - 1;
  let normalized = unsigned;
  if (dots && commas) {
    const decimalSeparator = unsigned.lastIndexOf('.') > unsigned.lastIndexOf(',') ? '.' : ',';
    const groupingSeparator = decimalSeparator === '.' ? ',' : '.';
    normalized = unsigned.split(groupingSeparator).join('').replace(decimalSeparator, '.');
  } else if (dots || commas) {
    const separator = dots ? '.' : ',';
    const parts = unsigned.split(separator);
    const grouping = (parts.length > 2 && parts.slice(1).every(part => part.length === 3))
      || (parts.length === 2 && parts[1].length === 3);
    normalized = grouping ? parts.join('') : `${parts.slice(0, -1).join('')}.${parts[parts.length - 1]}`;
  }
  normalized = sign + normalized;
  const number = Number(normalized);
  return Number.isFinite(number) ? (number === 0 ? 0 : number) : null;
}

export function normalizeMoneyDifference(value: unknown) {
  const number = numericValue(value);
  if (number === null) return null;
  const normalized = Math.round(number);
  return normalized === 0 ? 0 : normalized;
}

export function isNonZeroMoneyDifference(value: unknown) {
  const normalized = normalizeMoneyDifference(value);
  return normalized !== null && normalized !== 0;
}

export function getDifferenceClass(column: string, value: unknown, total = false) {
  if (!column.startsWith('difference_')) return undefined;
  const normalized = normalizeMoneyDifference(value);
  if (normalized === null || normalized === 0) return undefined;
  if (total) return normalized < 0 ? 'results-reconciliation-total-difference-negative' : undefined;
  return 'results-reconciliation-difference';
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
  const number = normalizeMoneyDifference(value);
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
