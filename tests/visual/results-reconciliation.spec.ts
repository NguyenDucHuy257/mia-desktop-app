import { expect, test } from '@playwright/test';

test('reconciliation tab shows full-scope warning data in the compact native result table', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_reconcile', username: '0100000000', company_name: 'Công ty Đối chiếu', status: 'connected', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const record = { job_id: 'job_reconcile', connection_id: account.connection_id, intent: {}, idempotency_key: 'reconcile', created_at: 'now', updated_at: 'now', status: 'completed' };
    const calls: Array<Record<string, unknown>> = [];
    Object.defineProperty(window, 'reconciliationCalls', { value: calls });
    const empty = { items: [], columns: [], column_labels: {}, total_count: 0, pagination: { limit: 50, has_more: false, next_cursor: null } };
    const reconciliation = async (query: Record<string, unknown>) => {
      calls.push(structuredClone(query));
      const payload = {
        items: [{
          row_id: 'issue-1', direction: 'purchase', invoice_key: 'purchase|query|0101|AA/26E|1|1',
          fields: {
            stt: 1, reconciliation_status: 'Chênh lệch tiền', khmshdon: '1', khhdon: 'AA/26E', shdon: '1',
            tdlap: '01/08/2026', nbmst: '0101', nbten: 'Công ty bán có tên rất dài để kiểm tra ellipsis',
            nmmst: '0202', nmten: 'Công ty mua', mismatch_fields: 'Tổng tiền thanh toán',
            overview_tgtthue: 20154017, detail_tthue: 20154017, difference_tgtthue: -0.000000003,
            overview_tgtttbso: 5500000, detail_tgtttbso: 5450000, difference_tgtttbso: 50000,
          },
        }],
        columns: ['stt', 'reconciliation_status', 'khmshdon', 'khhdon', 'shdon', 'tdlap', 'nbmst', 'nbten', 'nmmst', 'nmten', 'mismatch_fields', 'overview_tgtthue', 'detail_tthue', 'difference_tgtthue', 'overview_tgtttbso', 'detail_tgtttbso', 'difference_tgtttbso'],
        column_labels: {
          stt: 'STT', reconciliation_status: 'Trạng thái đối chiếu', khmshdon: 'Ký hiệu mẫu số', khhdon: 'Ký hiệu hóa đơn', shdon: 'Số hóa đơn', tdlap: 'Ngày lập', nbmst: 'MST người bán', nbten: 'Tên người bán', nmmst: 'MST người mua', nmten: 'Tên người mua', mismatch_fields: 'Chỉ tiêu chênh lệch', overview_tgtttbso: 'Tổng tiền Tổng quan', detail_tgtttbso: 'Tổng tiền Chi tiết', difference_tgtttbso: 'Chênh lệch',
        },
        column_types: { overview_tgtthue: 'number', detail_tthue: 'number', difference_tgtthue: 'number', overview_tgtttbso: 'number', detail_tgtttbso: 'number', difference_tgtttbso: 'number' },
        total_count: 1,
        aggregate: { matching_row_count: 1, row_count: 1, invoice_count: 1, totals: { overview_tgtttbso: 5500000, detail_tgtttbso: 12000000, difference_tgtttbso: -6500000 } },
        reconciliation: { selected_overview_invoice_count: 1960, selected_detail_invoice_count: 1767, selected_difference: 193, overview_invoice_count: 1767, detail_invoice_count: 1767, difference: 0, missing_detail_count: 0, missing_overview_count: 0, money_mismatch_count: 1, issue_count: 1, coverage_ranges: [{ date_from: '2026-01-01', date_to: '2026-06-30' }], uncovered_ranges: [{ date_from: '2026-07-01', date_to: '2026-07-31' }] },
        pagination: { limit: 50, has_more: false, next_cursor: null },
      };
      if (query.date_from === '2026-08-02') {
        return {
          ...payload,
          items: [], total_count: 0,
          aggregate: { matching_row_count: 0, row_count: 0, invoice_count: 0, totals: {} },
          reconciliation: { selected_overview_invoice_count: 150, selected_detail_invoice_count: 150, selected_difference: 0, overview_invoice_count: 150, detail_invoice_count: 150, difference: 0, missing_detail_count: 0, missing_overview_count: 0, money_mismatch_count: 0, issue_count: 0, coverage_ranges: [{ date_from: '2026-08-02', date_to: '2026-08-31' }], uncovered_ranges: [] },
        };
      }
      return payload;
    };
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], create: async () => account, get: async () => account, reconnect: async () => account, revoke: async () => undefined },
      jobs: {
        resume: async () => record, resumeAll: async () => [record], latestAll: async () => [record],
        status: async () => ({ job_id: record.job_id, status: 'completed', stage: null, overall_percent: 100, current_month: null, updated_at: 'now', error: null }),
        summary: async () => ({ job_id: record.job_id, status: 'completed', warning_count: 0, stages: [], coverage_plan: {}, work: {}, post_processing: {} }),
        start: async () => ({ record, accepted: {} }), cancel: async () => ({}), clear: async () => undefined,
      },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      results: { overview: async () => empty, details: async () => empty, reconciliation, facets: async () => ({ values: ['Chênh lệch tiền'], truncated: false, column_type: 'text' }) },
      artifacts: { export: async () => ({ count: 0, files: [] }), selectDirectory: async () => 'C:\\MIA', list: async () => ({ items: [], pagination: { limit: 200, has_more: false, next_cursor: null } }), targets: async () => ({ keys: [], total: 0 }), cancel: async () => ({ cancelled: true }), openDirectory: async () => true, onInvoiceProgress: () => () => undefined, onExportProgress: () => () => undefined },
      external: { open: async () => true },
    } });
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Xem kết quả' }).click();
  const tab = page.getByRole('tab', { name: /Đối chiếu Tổng quan & Chi tiết/ });
  await expect(tab).toBeVisible();
  await expect(tab.locator('.results-reconciliation-indicator')).toHaveCount(1);
  await tab.click();
  await expect(page.locator('.results-reconciliation-summary')).toContainText('Dữ liệu hiện có:');
  await expect(page.locator('.results-reconciliation-summary')).toContainText('Tổng quan: 1.960 hóa đơn · Chi tiết: 1.767 hóa đơn');
  await expect(page.locator('.results-reconciliation-summary')).toContainText('Chênh lệch dữ liệu hiện có: +193 hóa đơn');
  await expect(page.locator('.results-reconciliation-summary')).toContainText('Kết quả trong phạm vi đã đối chiếu: Không chênh lệch số lượng');
  await expect(page.locator('.results-reconciliation-summary')).toContainText('(Tổng quan: 1.767; Chi tiết: 1.767)');
  await expect(page.locator('.results-reconciliation-summary')).toContainText('Chưa đủ dữ liệu để đối chiếu: 01/07/2026 - 31/07/2026');
  await expect(page.locator('.results-reconciliation-summary')).toContainText('Hóa đơn lệch tiền: 1 hóa đơn');
  await expect(page.locator('.results-reconciliation-status')).toHaveText('Chênh lệch tiền');
  await expect(page.locator('.results-reconciliation-mismatch-fields')).toHaveText('Tổng tiền thanh toán');
  await expect(page.getByRole('button', { name: 'Tải xuống kết quả chênh lệch' })).toBeVisible();
  await expect(page.locator('.results-reconciliation-difference')).toHaveText('50.000');
  const zeroDifference = page.locator('.results-row:not(.results-row--header):not(.results-row--total) > span').filter({ hasText: /^0$/ });
  await expect(zeroDifference).toHaveCount(1);
  await expect(zeroDifference).not.toHaveClass(/results-reconciliation-difference/);
  await expect(page.locator('.results-reconciliation-total-difference-negative')).toHaveText('-6.500.000');
  await expect(page.locator('.results-checkbox-cell')).toHaveCount(0);

  const headerHeight = await page.locator('.results-row--header').evaluate(node => node.getBoundingClientRect().height);
  const rowHeight = await page.locator('.results-row:not(.results-row--header):not(.results-row--total)').evaluate(node => node.getBoundingClientRect().height);
  const summaryHeight = await page.locator('.results-reconciliation-summary').evaluate(node => node.getBoundingClientRect().height);
  const tableHeight = await page.locator('.results-table').evaluate(node => node.getBoundingClientRect().height);
  expect(headerHeight).toBeLessThanOrEqual(42);
  expect(rowHeight).toBeLessThanOrEqual(38);
  expect(summaryHeight).toBeLessThanOrEqual(64);
  expect(tableHeight).toBeGreaterThan(200);
  await expect(page.locator('.results-table')).toHaveCSS('overflow-x', 'scroll');

  await page.setViewportSize({ width: 1024, height: 768 });
  const compactSummary = await page.locator('.results-reconciliation-summary').evaluate(node => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth,
  }));
  expect(compactSummary.scrollWidth).toBeLessThanOrEqual(compactSummary.clientWidth);
  expect(await page.locator('.results-table').evaluate(node => node.getBoundingClientRect().height)).toBeGreaterThan(100);
  await page.setViewportSize({ width: 1500, height: 1024 });

  await page.locator('.date-range-trigger').click();
  await page.getByLabel('Từ ngày xem nhập tay').fill('02/08/2026');
  await page.getByLabel('Đến ngày xem nhập tay').fill('31/08/2026');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { reconciliationCalls: Array<Record<string, unknown>> }).reconciliationCalls.at(-1)?.date_from)).toBe('2026-08-02');
  await expect(tab.locator('.results-reconciliation-indicator')).toHaveCount(0);
  await expect(page.locator('.results-table-empty')).toHaveText('Không phát hiện chênh lệch giữa Tổng quan và Chi tiết.');
  await page.screenshot({ path: 'test-results/results-reconciliation-compact.png', fullPage: true });
});
