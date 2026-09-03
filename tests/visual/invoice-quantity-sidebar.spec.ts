import { expect, test } from './licensed-test';

test('invoice quantities use two real columns and sidebar active state fills its row', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'quantity-demo', username: '0101234567', company_name: 'Công ty Số lượng', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
    } });
  });
  await page.goto('/');

  const header = page.locator('.invoice-count-header');
  await expect(header).toBeVisible();
  await expect(header.locator('b')).toHaveCount(2);
  await expect(header.locator('b').nth(0)).toHaveText('Tổng quan');
  await expect(header.locator('b').nth(1)).toHaveText('Chi tiết');
  await expect(header).toHaveCSS('grid-column-start', 'span 2');

  const firstRow = page.locator('.invoice-page .table-row').first();
  await expect(firstRow).toBeVisible();
  await expect(firstRow.locator('.invoice-count-value')).toHaveCount(2);
  expect(await firstRow.locator('.invoice-count-value').allTextContents()).toEqual(['0', '0']);
  await expect(firstRow.locator('.invoice-count-value').nth(0)).toHaveCSS('white-space', 'nowrap');
  await expect(firstRow.locator('.invoice-count-value').nth(1)).toHaveCSS('white-space', 'nowrap');

  const active = page.locator('.nav-button[data-active="true"]');
  const sidebar = page.locator('.sidebar');
  const navigation = page.locator('.app-navigation');
  await expect(active).toHaveCSS('border-radius', '6px');
  await expect(active).toHaveCSS('border-top-width', '0px');
  const [activeBox, sidebarBox, navigationBox] = await Promise.all([active.boundingBox(), sidebar.boundingBox(), navigation.boundingBox()]);
  expect((activeBox?.x ?? 0) - (sidebarBox?.x ?? 0)).toBe(12);
  expect(Math.abs((activeBox?.width ?? 0) - (navigationBox?.width ?? 0))).toBeLessThanOrEqual(1);

  await page.screenshot({ path: 'test-results/invoice-quantity-sidebar.png', fullPage: true });
});
