import { expect, test } from './licensed-test';

test('column filters are nested, interactive, portalled and use the Excel-like workflow', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_filter', username: '0100000000', company_name: 'Công ty Filter', status: 'connected', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const record = { job_id: 'job_filter', connection_id: account.connection_id, intent: {}, idempotency_key: 'filter', created_at: 'now', updated_at: 'now', status: 'running' };
    const calls: Array<Record<string, unknown>> = [];
    const externalCalls: string[] = [];
    Object.defineProperty(window, 'resultFilterCalls', { value: calls });
    Object.defineProperty(window, 'externalLookupCalls', { value: externalCalls });
    const columns = ['stt', 'khmshdon', 'nbten', 'tgtttbso', 'tsuat', 'url', 'mk'];
    const labels = { stt: 'STT', khmshdon: 'Mã HĐ', nbten: 'Tên người bán', tgtttbso: 'Tổng tiền thanh toán', tsuat: 'Thuế suất', url: 'URL tra cứu hóa đơn', mk: 'Mã tra cứu' };
    const rows = [
      { row_id: 1, direction: 'purchase', invoice_key: 'purchase|query|0101|AA|1|1', fields: { stt: 1, khmshdon: '1', nbten: 'Alpha', tgtttbso: 1924545.7000000002, tsuat: 10, url: 'https://invoice.example/lookup?code=A1', mk: 'A1' } },
      { row_id: 2, direction: 'purchase', invoice_key: 'purchase|query|0102|AA|2|1', fields: { stt: 2, khmshdon: '2', nbten: 'Beta', tgtttbso: 1870182.2999999998, tsuat: '10%', url: 'javascript:alert(1)', mk: '' } },
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
      external: { open: async (url: string) => { externalCalls.push(url); return true; } },
    } });
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Xem kết quả' }).click();
  await expect(page.getByLabel('Lọc mua bán').locator('option')).toHaveText(['Mua vào', 'Bán ra']);
  const invoiceType = page.getByLabel('Loại hóa đơn');
  await expect(invoiceType.locator('option')).toHaveText([
    'Hóa đơn điện tử', 'Máy tính tiền', 'HĐĐT & Máy tính tiền',
  ]);
  await invoiceType.selectOption('combined');
  await expect.poll(async () => page.evaluate(() => (window as typeof window & { resultFilterCalls: Array<Record<string, unknown>> }).resultFilterCalls.at(-1))).toMatchObject({
    query_type: null, query_types: ['query', 'sco-query'], cursor: null,
  });
  await expect(page.locator('.results-pager button[data-active="true"]')).toHaveText('1');
  await page.screenshot({ path: 'test-results/results-combined-dropdown.png', animations: 'disabled' });
  await invoiceType.selectOption('sco-query');
  await expect.poll(async () => page.evaluate(() => (window as typeof window & { resultFilterCalls: Array<Record<string, unknown>> }).resultFilterCalls.at(-1))).toMatchObject({ query_type: 'sco-query' });
  await invoiceType.selectOption('query');
  await expect.poll(async () => page.evaluate(() => (window as typeof window & { resultFilterCalls: Array<Record<string, unknown>> }).resultFilterCalls.at(-1))).toMatchObject({ query_type: 'query' });
  await expect(page.locator('.results-header-slot')).toHaveCount(7);
  await expect(page.locator('.results-header-slot > .result-header-cell > .result-column-filter > .result-column-filter-button')).toHaveCount(7);
  await expect(page.locator('.results-external-link')).toHaveCount(1);
  await page.locator('.results-external-link').click();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { externalLookupCalls: string[] }).externalLookupCalls)).toEqual(['https://invoice.example/lookup?code=A1']);
  await expect(page.locator('.results-table')).toContainText('1.924.546');
  await expect(page.locator('.results-row--total')).toContainText('3.794.728');
  await expect(page.locator('.results-table')).not.toContainText('7000000002');

  const resultTabs = page.locator('.results-tabs--figma [role="tab"]');
  const compactLayout = await page.evaluate(() => {
    const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
    const back = box('.results-back');
    const title = box('.results-header--figma h1');
    const description = box('.results-header--figma p');
    const tabs = box('.results-tabs--figma');
    const filters = box('.results-filters--figma');
    const table = box('.results-table');
    return {
      backTitleGap: title.top - back.bottom,
      descriptionTabsGap: tabs.top - description.bottom,
      tabsFilterGap: filters.top - tabs.bottom,
      tabsHeight: tabs.height,
      filtersHeight: filters.height,
      filterTableGap: table.top - filters.bottom,
      tableTop: table.top,
    };
  });
  expect(compactLayout.backTitleGap).toBeGreaterThanOrEqual(8);
  expect(compactLayout.backTitleGap).toBeLessThanOrEqual(10);
  expect(compactLayout.descriptionTabsGap).toBeGreaterThanOrEqual(12);
  expect(compactLayout.descriptionTabsGap).toBeLessThanOrEqual(16);
  expect(compactLayout.tabsFilterGap).toBeLessThanOrEqual(4);
  expect(compactLayout.tabsHeight).toBeLessThanOrEqual(40);
  expect(compactLayout.filtersHeight).toBeLessThanOrEqual(48);
  expect(compactLayout.filterTableGap).toBeLessThanOrEqual(5);
  expect(compactLayout.tableTop).toBeLessThanOrEqual(230);
  const overviewTableBox = await page.locator('.results-table').boundingBox();
  expect(overviewTableBox).not.toBeNull();
  await resultTabs.nth(1).click();
  await expect(page.locator('.results-row:not(.results-row--header):not(.results-row--total)')).toHaveCount(2);
  const detailTableBox = await page.locator('.results-table').boundingBox();
  expect(detailTableBox).not.toBeNull();
  expect(Math.abs(detailTableBox!.y - overviewTableBox!.y)).toBeLessThanOrEqual(1);
  expect(Math.abs(detailTableBox!.height - overviewTableBox!.height)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: 'test-results/results-details-compact.png', fullPage: true });
  await resultTabs.nth(0).click();
  await expect(page.locator('.results-row:not(.results-row--header):not(.results-row--total)')).toHaveCount(2);

  await page.setViewportSize({ width: 1200, height: 760 });
  const resizedTableBox = await page.locator('.results-table').boundingBox();
  expect(resizedTableBox).not.toBeNull();
  expect(resizedTableBox!.height).toBeGreaterThan(100);
  await expect(page.locator('.results-pager')).toBeVisible();
  await page.screenshot({ path: 'test-results/results-overview-compact-resized.png', fullPage: true });

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
  await expect(page.locator('.results-header-slot')).toHaveCount(7);
  await expect(moneyButton).toBeVisible();
  await page.screenshot({ path: 'test-results/results-filter-empty-header.png', fullPage: true });
  await moneyButton.click();
  await expect(moneyMenu).toBeVisible();
  await moneyMenu.getByRole('button', { name: 'Xóa bộ lọc' }).click();
  await expect(page.locator('.results-row:not(.results-row--header):not(.results-row--total)')).toHaveCount(2);

  await page.getByLabel('Tìm kiếm kết quả').fill('Alpha toàn cục');
  await expect.poll(async () => page.evaluate(() => (window as typeof window & { resultFilterCalls: Array<Record<string, unknown>> }).resultFilterCalls.at(-1))).toMatchObject({ search: 'Alpha toàn cục' });

  await page.getByLabel('Tìm kiếm kết quả').fill('');
  // The unfiltered first page is already cached, so clearing search may restore
  // it without another runtime call. Verify the visible result instead.
  await expect(page.getByLabel('Tìm kiếm kết quả')).toHaveValue('');
  await expect(page.locator('.results-row:not(.results-row--header):not(.results-row--total)')).toHaveCount(2);
  await page.locator('.results-row:not(.results-row--header):not(.results-row--total)').first().getByRole('checkbox').check();
  const excludeButton = page.getByRole('button', { name: /Loại khỏi tải xuống/ });
  await expect(excludeButton).toBeEnabled();
  await excludeButton.click();
  const confirm = page.locator('.results-exclude-confirm');
  await expect(confirm).toBeVisible();
  await expect(confirm).toHaveCSS('border-radius', '6px');
  await expect(confirm).toContainText('Loại 1 hóa đơn khỏi file tải xuống?');
});
