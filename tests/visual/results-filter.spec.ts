import { expect, test } from '@playwright/test';

test('column filters are nested, interactive, portalled and use the Excel-like workflow', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_filter', username: '0100000000', company_name: 'Công ty Filter', status: 'connected', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const record = { job_id: 'job_filter', connection_id: account.connection_id, intent: {}, idempotency_key: 'filter', created_at: 'now', updated_at: 'now', status: 'running' };
    const calls: Array<Record<string, unknown>> = [];
    Object.defineProperty(window, 'resultFilterCalls', { value: calls });
    const columns = ['stt', 'khmshdon', 'nbten', 'tgtttbso', 'tsuat'];
    const labels = { stt: 'STT', khmshdon: 'Mã HĐ', nbten: 'Tên người bán', tgtttbso: 'Tổng tiền thanh toán', tsuat: 'Thuế suất' };
    const rows = [
      { row_id: 1, direction: 'purchase', invoice_key: 'purchase|query|0101|AA|1|1', fields: { stt: 1, khmshdon: '1', nbten: 'Alpha', tgtttbso: 1924545.7000000002, tsuat: 10 } },
      { row_id: 2, direction: 'purchase', invoice_key: 'purchase|query|0102|AA|2|1', fields: { stt: 2, khmshdon: '2', nbten: 'Beta', tgtttbso: 1870182.2999999998, tsuat: '10%' } },
    ];
    const result = async (query: Record<string, unknown>) => {
      calls.push(structuredClone(query));
      const filters = (query.column_filters ?? {}) as Record<string, unknown>;
      const visibleRows = filters.tgtttbso ? [] : rows;
      return {
        items: visibleRows, columns, column_labels: labels,
        column_types: { stt: 'number', tgtttbso: 'number', tsuat: 'percent' },
        total_count: visibleRows.length,
        aggregate: { matching_row_count: visibleRows.length, row_count: visibleRows.length, invoice_count: visibleRows.length, totals: { tgtttbso: visibleRows.length ? 3794728 : 0 } },
        pagination: { limit: 50, has_more: false, next_cursor: null },
      };
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
      results: {
        overview: result, details: result,
        facets: async ({ column }: { column: string }) => ({
          values: column === 'nbten' ? ['Alpha', 'Beta'] : column === 'tgtttbso' ? [1924545.7000000002, 1870182.2999999998] : ['1', '2'],
          truncated: false,
          column_type: column === 'tgtttbso' ? 'number' : 'text',
        }),
      },
      artifacts: {
        export: async () => ({ count: 1, files: ['C:\\MIA\\result.xlsx'] }), selectDirectory: async () => 'C:\\MIA',
        list: async () => ({ items: [], pagination: { limit: 200, has_more: false, next_cursor: null } }),
        targets: async () => ({ keys: [], total: 0 }), cancel: async () => ({ cancelled: true }), openDirectory: async () => true,
        onInvoiceProgress: () => () => undefined, onExportProgress: () => () => undefined,
      },
    } });
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Xem kết quả' }).click();
  await expect(page.locator('.results-header-slot')).toHaveCount(5);
  await expect(page.locator('.results-header-slot > .result-header-cell > .result-column-filter > .result-column-filter-button')).toHaveCount(5);
  await expect(page.locator('.results-table')).toContainText('1.924.546');
  await expect(page.locator('.results-row--total')).toContainText('3.794.728');
  await expect(page.locator('.results-table')).not.toContainText('7000000002');

  const moneyButton = page.getByRole('button', { name: 'Lọc cột Tổng tiền thanh toán' });
  await moneyButton.click();
  const moneyMenu = page.locator('.result-column-filter-menu[data-column="tgtttbso"]');
  await expect(moneyMenu).toBeVisible();
  expect(await moneyMenu.evaluate((node) => node.parentElement === document.body)).toBe(true);
  await expect(moneyMenu).toHaveCSS('border-radius', '6px');
  await expect(moneyMenu).toHaveCSS('background-color', 'rgb(255, 255, 255)');
  await expect(moneyMenu).toHaveCSS('border-top-color', 'rgb(208, 213, 221)');
  await expect(moneyMenu).toContainText('Sắp xếp tăng dần');
  await expect(moneyMenu.locator('.result-filter-values')).toContainText('1.924.546');
  await expect(moneyMenu.locator('.result-filter-values')).not.toContainText('7000000002');

  await page.getByRole('button', { name: 'Lọc cột Tên người bán' }).click();
  await expect(page.locator('.result-column-filter-menu')).toHaveCount(1);
  const textMenu = page.locator('.result-column-filter-menu[data-column="nbten"]');
  await expect(textMenu).toBeVisible();
  await textMenu.getByLabel('Tìm trong cột Tên người bán').fill('Alpha');
  await expect(textMenu.locator('.result-filter-values label')).toHaveCount(1);
  await textMenu.getByText('(Chọn tất cả)').click();
  await expect(textMenu.locator('.result-filter-values input')).not.toBeChecked();
  await textMenu.getByRole('button', { name: 'Hủy' }).click();
  await expect(page.getByRole('button', { name: 'Lọc cột Tên người bán' })).toHaveAttribute('data-active', 'false');

  await page.getByRole('button', { name: 'Lọc cột Tên người bán' }).click();
  await page.getByLabel('Tìm trong cột Tên người bán').fill('Alpha');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await expect(page.getByRole('button', { name: 'Lọc cột Tên người bán' })).toHaveAttribute('data-active', 'true');
  await page.getByRole('button', { name: 'Lọc cột Tên người bán' }).click();
  await page.getByRole('button', { name: 'Xóa bộ lọc' }).click();
  await expect(page.getByRole('button', { name: 'Lọc cột Tên người bán' })).toHaveAttribute('data-active', 'false');

  await moneyButton.click();
  await page.keyboard.press('Escape');
  await expect(page.locator('.result-column-filter-menu')).toHaveCount(0);
  await moneyButton.click();
  await page.locator('.results-page').click({ position: { x: 5, y: 5 } });
  await expect(page.locator('.result-column-filter-menu')).toHaveCount(0);

  await page.locator('.results-table').evaluate((node) => { node.scrollLeft = node.scrollWidth; node.dispatchEvent(new Event('scroll')); });
  await moneyButton.click();
  await expect(moneyMenu).toBeVisible();
  const viewport = page.viewportSize()!;
  const box = await moneyMenu.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
  await page.screenshot({ path: 'test-results/results-filter-ui.png', fullPage: true });
  await moneyMenu.getByRole('button', { name: 'Sắp xếp tăng dần' }).click();
  await expect.poll(async () => page.evaluate(() => (window as typeof window & { resultFilterCalls: Array<Record<string, unknown>> }).resultFilterCalls.at(-1))).toMatchObject({ sort: { column: 'tgtttbso', direction: 'asc' } });
  await moneyButton.click();
  await moneyMenu.getByRole('button', { name: 'Sắp xếp giảm dần' }).click();
  await expect.poll(async () => page.evaluate(() => (window as typeof window & { resultFilterCalls: Array<Record<string, unknown>> }).resultFilterCalls.at(-1))).toMatchObject({ sort: { column: 'tgtttbso', direction: 'desc' } });
  await expect(moneyButton).toHaveAttribute('data-active', 'true');

  await moneyButton.click();
  await moneyMenu.getByLabel('Điều kiện Tổng tiền thanh toán').selectOption('gt');
  await moneyMenu.getByLabel('Giá trị lọc Tổng tiền thanh toán').fill('999999999');
  await moneyMenu.getByRole('button', { name: 'Áp dụng' }).click();
  await expect(page.locator('.results-table-empty')).toHaveText('Không có dữ liệu phù hợp với bộ lọc hiện tại.');
  await expect(page.locator('.results-header-slot')).toHaveCount(5);
  await expect(moneyButton).toBeVisible();
  await page.screenshot({ path: 'test-results/results-filter-empty-header.png', fullPage: true });
  await moneyButton.click();
  await expect(moneyMenu).toBeVisible();
  await moneyMenu.getByRole('button', { name: 'Xóa bộ lọc' }).click();
  await expect(page.locator('.results-row:not(.results-row--header):not(.results-row--total)')).toHaveCount(2);

  await page.getByLabel('Tìm kiếm kết quả').fill('Alpha toàn cục');
  await expect.poll(async () => page.evaluate(() => (window as typeof window & { resultFilterCalls: Array<Record<string, unknown>> }).resultFilterCalls.at(-1))).toMatchObject({ search: 'Alpha toàn cục' });

  await page.getByLabel('Tìm kiếm kết quả').fill('');
  await page.locator('.results-row:not(.results-row--header):not(.results-row--total)').first().getByRole('checkbox').check();
  await page.getByRole('button', { name: /Loại khỏi tải xuống/ }).click();
  const confirm = page.locator('.results-exclude-confirm');
  await expect(confirm).toBeVisible();
  await expect(confirm).toHaveCSS('border-radius', '6px');
  await expect(confirm).toContainText('Loại 1 hóa đơn khỏi file tải xuống?');
});
