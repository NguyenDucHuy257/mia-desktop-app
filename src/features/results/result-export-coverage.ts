import type { InvoiceDirection } from '../../lib/api/contracts';
import type { VatReturnCoverageAccount } from '../../lib/runtime-bridge';

export type CoverageExportScope = 'overview' | 'details';

function formatDate(value: string) {
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}/${month}/${year}` : value;
}

export function resultExportCoverageWarning(
  coverage: VatReturnCoverageAccount,
  scopes: CoverageExportScope[],
  direction: InvoiceDirection | '',
) {
  const directions: InvoiceDirection[] = direction ? [direction] : ['purchase', 'sold'];
  const lines: string[] = [];
  for (const currentDirection of directions) {
    const state = coverage[currentDirection];
    const directionLabel = currentDirection === 'purchase' ? 'Mua vào' : 'Bán ra';
    for (const scope of scopes) {
      const ranges = scope === 'overview'
        ? state.missing_overview_ranges
        : state.missing_detail_ranges;
      if (!ranges.length) continue;
      const scopeLabel = scope === 'overview' ? 'Tổng quan' : 'Chi tiết';
      lines.push(`${scopeLabel} – ${directionLabel}: ${ranges.map((range) => (
        `${formatDate(range.date_from)} - ${formatDate(range.date_to)}`
      )).join(', ')}`);
    }
  }
  if (!lines.length) return '';
  return `Chưa thể xuất Excel vì dữ liệu chưa đồng bộ đủ:\n${lines.join('\n')}\nVui lòng đồng bộ bổ sung các khoảng trên rồi xuất lại.`;
}
