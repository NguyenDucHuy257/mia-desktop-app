import { describe, expect, it } from 'vitest';
import type { VatReturnCoverageAccount } from '../../src/lib/runtime-bridge';
import { resultExportCoverageWarning } from '../../src/features/results/result-export-coverage';

function coverage(): VatReturnCoverageAccount {
  return {
    connection_id: 'conn_1',
    purchase: {
      direction: 'purchase', overview_ready: true, detail_ready: false, ready: false,
      missing_overview_ranges: [],
      missing_detail_ranges: [{ date_from: '2026-02-01', date_to: '2026-02-28' }],
      missing: [{ scope: 'details', date_from: '2026-02-01', date_to: '2026-02-28' }],
    },
    sold: {
      direction: 'sold', overview_ready: false, detail_ready: true, ready: false,
      missing_overview_ranges: [{ date_from: '2026-01-01', date_to: '2026-01-31' }],
      missing_detail_ranges: [],
      missing: [{ scope: 'overview', date_from: '2026-01-01', date_to: '2026-01-31' }],
    },
  };
}

describe('result export coverage warning', () => {
  it('lists missing scope, direction, and exact range', () => {
    expect(resultExportCoverageWarning(coverage(), ['overview', 'details'], '')).toBe(
      'Chưa thể xuất Excel vì dữ liệu chưa đồng bộ đủ:\n'
      + 'Chi tiết – Mua vào: 01/02/2026 - 28/02/2026\n'
      + 'Tổng quan – Bán ra: 01/01/2026 - 31/01/2026\n'
      + 'Vui lòng đồng bộ bổ sung các khoảng trên rồi xuất lại.',
    );
  });

  it('only checks selected scope and direction', () => {
    expect(resultExportCoverageWarning(coverage(), ['overview'], 'purchase')).toBe('');
    expect(resultExportCoverageWarning(coverage(), ['details'], 'purchase')).toContain('Chi tiết – Mua vào');
  });
});
