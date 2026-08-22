export type ResultTableMode = 'overview' | 'details';

const NUMBER_FIELDS = new Set([
  'tgtcthue', 'tgtthue', 'ttcktmai', 'tgtcktmai', 'tgtphi', 'tgtttbso',
  'tgia', 'dgia', 'stckhau', 'tsuat', 'thtien', 'tthue', 'sluong',
]);

const TOTAL_FIELDS = new Set([
  'tgtcthue', 'tgtthue', 'ttcktmai', 'tgtcktmai', 'tgtphi', 'tgtttbso',
  'tgia', 'dgia', 'stckhau', 'tsuat', 'thtien', 'tthue',
]);

const HIDDEN_FIELDS = new Set([
  'id', 'direction', 'company_tax_code', 'created_at', 'updated_at',
  'detail_path', 'raw_detail_path', 'normalized_ready', 'error_message',
]);

const LABELS: Record<string, string> = {
  khmshdon: 'Ký hiệu mẫu số HĐ',
  khhdon: 'Ký hiệu HĐ',
  shdon: 'Số hóa đơn',
  nlap: 'Ngày lập',
  nlap_date: 'Ngày lập',
  ntao: 'Ngày tạo',
  nky: 'Ngày ký',
  mhdon: 'Mã hóa đơn',
  dvtte: 'Đơn vị tiền tệ',
  tgia: 'Tỷ giá',
  nbten: 'Tên người bán',
  nbmst: 'MST người bán',
  nbdchi: 'Địa chỉ người bán',
  nmten: 'Tên người mua',
  nmmst: 'MST người mua',
  nmdchi: 'Địa chỉ người mua',
  m_VT: 'Mã vật tư',
  ten: 'Tên hàng hóa, dịch vụ',
  dvtinh: 'Đơn vị tính',
  sluong: 'Số lượng',
  dgia: 'Đơn giá',
  stckhau: 'Chiết khấu',
  tsuat: 'Thuế suất',
  thtien: 'Thành tiền chưa thuế',
  tthue: 'Tiền thuế',
  tgtcthue: 'Tổng tiền chưa thuế',
  tgtthue: 'Tổng tiền thuế',
  tgtcktmai: 'Tổng tiền chiết khấu thương mại',
  tgtphi: 'Tổng tiền phí',
  tgtttbso: 'Tổng tiền thanh toán',
  tthai: 'Trạng thái hóa đơn',
  ttxly: 'Trạng thái xử lý',
  url: 'URL tra cứu',
  mk: 'Mã tra cứu',
  ghichu: 'Ghi chú',
  thtttoan: 'Hình thức thanh toán',
  tchat: 'Tính chất',
  dgiai: 'Diễn giải',
  slo: 'Số lô',
  hdung: 'Hạn dùng',
  invoice_category: 'Loại hóa đơn',
  query_type: 'Nguồn dữ liệu',
};

const OVERVIEW_ORDER = [
  'nlap', 'nlap_date', 'khmshdon', 'khhdon', 'shdon', 'dvtte', 'tgia',
  'nbten', 'nbmst', 'nbdchi', 'nmten', 'nmmst', 'nmdchi',
  'tgtcthue', 'tgtthue', 'ttcktmai', 'tgtcktmai', 'tgtphi', 'tgtttbso',
  'tthai', 'ttxly', 'invoice_category', 'query_type',
];

const DETAIL_ORDER = [
  'khmshdon', 'khhdon', 'shdon', 'ntao', 'nky', 'mhdon', 'dvtte', 'tgia',
  'nbten', 'nbmst', 'nbdchi', 'nmten', 'nmmst', 'nmdchi', 'm_VT', 'ten',
  'dvtinh', 'sluong', 'dgia', 'stckhau', 'tsuat', 'thtien', 'tthue',
  'ttcktmai', 'tgtphi', 'tgtttbso', 'tthai', 'ttxly', 'url', 'mk',
  'ghichu', 'thtttoan', 'tchat', 'dgiai', 'slo', 'hdung',
];

const numberFormatter = new Intl.NumberFormat('vi-VN', {
  maximumFractionDigits: 20,
  useGrouping: true,
});

export function columnLabel(key: string, mode: ResultTableMode) {
  if (key === 'ttcktmai') {
    return mode === 'details' ? 'Tổng tiền CKTM' : 'Tổng tiền chiết khấu thương mại';
  }
  return LABELS[key] ?? key;
}

export function isNumericResultColumn(key: string) {
  return NUMBER_FIELDS.has(key.toLowerCase());
}

export function isTotalResultColumn(key: string) {
  return TOTAL_FIELDS.has(key.toLowerCase());
}

export function orderResultColumns(columns: string[], mode: ResultTableMode) {
  const unique = [...new Set(columns)].filter((key) => !HIDDEN_FIELDS.has(key.toLowerCase()));
  const order = mode === 'details' ? DETAIL_ORDER : OVERVIEW_ORDER;
  const rank = new Map(order.map((key, index) => [key, index]));
  return unique.sort((left, right) => {
    const leftRank = rank.get(left) ?? Number.MAX_SAFE_INTEGER;
    const rightRank = rank.get(right) ?? Number.MAX_SAFE_INTEGER;
    return leftRank === rightRank ? left.localeCompare(right, 'vi') : leftRank - rightRank;
  });
}

export function parseResultNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  let text = value.trim().replace(/\s+/g, '').replace(/%$/, '');
  if (!text) return null;
  if (/^-?\d+(?:\.\d+)?$/.test(text)) {
    const direct = Number(text);
    return Number.isFinite(direct) ? direct : null;
  }
  if (text.includes(',') && text.includes('.')) {
    text = text.lastIndexOf(',') > text.lastIndexOf('.')
      ? text.replaceAll('.', '').replace(',', '.')
      : text.replaceAll(',', '');
  } else if (text.includes(',')) {
    text = text.replace(',', '.');
  }
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

export function formatResultValue(key: string, value: unknown) {
  if (value === null || value === undefined || value === '') return '—';
  if (isNumericResultColumn(key)) {
    const number = parseResultNumber(value);
    if (number !== null) {
      const suffix = typeof value === 'string' && value.trim().endsWith('%') ? '%' : '';
      return `${numberFormatter.format(number)}${suffix}`;
    }
  }
  if (typeof value === 'object') {
    try { return JSON.stringify(value); } catch { return String(value); }
  }
  return String(value);
}

export function formatTotalValue(key: string, value: number | undefined) {
  return value === undefined ? '' : numberFormatter.format(value);
}
