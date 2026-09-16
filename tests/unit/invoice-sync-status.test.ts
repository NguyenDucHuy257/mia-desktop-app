import { describe, expect, it } from 'vitest';
import { syncStatusPresentation } from '../../src/features/invoices/InvoiceManagementPage';
import type { InvoiceSyncState } from '../../src/lib/api/contracts';

function state(overrides: Partial<InvoiceSyncState>): InvoiceSyncState {
  return {
    connection_id: 'conn-test', direction: 'purchase', status: 'not_synced',
    current_month: null, sync_from: null, sync_until: null,
    invoice_count: 979, detail_invoice_count: 858,
    baseline_invoice_count: null, added_invoice_count: null, last_job_id: 'job-test',
    sync_mode: 'supplement', ...overrides,
  };
}

describe('invoice synchronization status presentation', () => {
  it('reports an overview failure as overview even when old overview coverage exists', () => {
    const result = syncStatusPresentation(state({
      status: 'failed', requested_scope: 'overview', current_stage: 'overview',
      overview_ready: true, detail_ready: false,
      missing_detail_ranges: [{ date_from: '2023-01-01', date_to: '2023-12-31' }],
    }), false);
    expect(result).toEqual({ label: 'Đồng bộ Tổng quan thất bại', detail: null });
  });

  it('does not report missing details when only overview is selected', () => {
    const result = syncStatusPresentation(state({
      overview_ready: true, detail_ready: false,
      sync_from: '2023-01-01', sync_until: '2023-12-31',
      missing_detail_ranges: [{ date_from: '2023-01-01', date_to: '2023-12-31' }],
    }), false);
    expect(result).toEqual({ label: 'Đã đồng bộ', detail: 'Từ 01/01/2023 đến 31/12/2023' });
  });

  it('reports missing details only when details are selected', () => {
    const result = syncStatusPresentation(state({
      overview_ready: true, detail_ready: false,
      missing_detail_ranges: [{ date_from: '2023-01-01', date_to: '2023-12-31' }],
    }), true);
    expect(result).toEqual({ label: 'Chưa đồng bộ đầy đủ', detail: 'Thiếu Chi tiết: 01/01/2023 - 31/12/2023' });
  });

  it('uses the requested scope when a terminal failure has no current stage', () => {
    expect(syncStatusPresentation(state({ status: 'failed', requested_scope: 'detail', current_stage: null }), true).label)
      .toBe('Đồng bộ Chi tiết thất bại');
  });
});
