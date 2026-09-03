import { expect, test } from './licensed-test';
import { mkdirSync } from 'node:fs';
import { currentYearDateRange, displayDate } from '../../src/components/date-input-utils';

const account = { connection_id: 'conn_vat', username: '0101234567', company_name: 'CÔNG TY VAT', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };

test.beforeEach(async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        vatReturnExport: async (request: unknown) => {
          const browserWindow = window as Window & { __vatExportMode?: string };
          const destination = (request as { destination: string }).destination;
          const filename = 'To_khai_thue_GTGT_0101234567_01-11-2023_30-11-2023.xlsx';
          const finalPath = `${destination}\\Thư mục kết quả VAT có tên rất dài\\${filename}`;
          if (browserWindow.__vatExportMode === 'locked') {
            throw Object.assign(new Error('Không thể ghi đè tờ khai thuế GTGT vì file đang được mở.'), {
              code: `vat_return_destination_file_locked:${JSON.stringify({ filename, path: `${destination}\\${filename}` })}`,
            });
          }
          return { count: 1, files: [finalPath] };
        },
        vatReturnCoverage: async (request: unknown) => {
          const value = request as { date_from: string; date_to: string };
          if (value.date_from === '2023-10-01') await new Promise((resolve) => setTimeout(resolve, 250));
          const newer = value.date_from === '2023-11-01';
          return { ...(request as object), accounts: [{ connection_id: accountValue.connection_id, purchase: { direction: 'purchase', ready: true, missing: [] }, sold: newer ? { direction: 'sold', ready: true, missing: [] } : { direction: 'sold', ready: false, missing: [{ scope: 'details', date_from: value.date_from, date_to: value.date_to }] } }] };
        },
        coverage: async (request: unknown) => ({ ...(request as object), accounts: [] }), snapshot: async (request: unknown) => ({ ...(request as object), accounts: [] }), selectDirectory: async () => 'C:\\MIA', startBatch: async () => ({}), batchStatus: async () => ({}), cancelBatch: async () => ({ cancelled: false }),
      },
    } });
  }, { accountValue: account });
  await page.goto('/');
  await page.getByLabel('Chức năng chính').getByRole('button', { name: 'Xuất tờ khai thuế GTGT', exact: true }).click();
});

test('new date coverage wins when the previous response returns late', async ({ page }) => {
  await page.locator('.date-range-trigger').click();
  await page.getByLabel('Từ ngày nhập tay').fill('01/11/2023');
  await page.getByLabel('Đến ngày nhập tay').fill('30/11/2023');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await expect(page.locator('.vat-direction-coverages .artifact-coverage-badge')).toHaveCount(2);
  await expect(page.locator('.vat-direction-coverages .artifact-coverage-badge').nth(1)).toContainText('Đã đồng bộ');
  await page.waitForTimeout(350);
  await expect(page.locator('.vat-direction-coverages .artifact-coverage-badge').nth(1)).toContainText('Đã đồng bộ');
  await expect(page.locator('.vat-direction-coverages .artifact-coverage-badge').nth(1)).not.toContainText('15/10/2023');
});

test('VAT return page has reduced controls, dual coverage and blocks incomplete export', async ({ page }) => {
  const defaultRange = currentYearDateRange();
  const header = page.locator('.artifact-account-header');
  await expect(header.getByRole('heading')).toHaveText('Hỗ trợ lập tờ khai thuế GTGT');
  await expect(header.locator('p')).toHaveText('Tổng hợp số liệu hóa đơn và tạo file Excel tham khảo. Vui lòng kiểm tra, đối chiếu trước khi kê khai chính thức.');
  await expect(header).not.toContainText(/workbook|beta|thử nghiệm|chưa hoàn thiện|chưa chính xác/i);
  const toolbar = page.getByLabel('Thiết lập xuất tờ khai thuế GTGT');
  await expect(toolbar).toContainText('Khoảng thời gian');
  await expect(toolbar).not.toContainText('Mua vào');
  await expect(toolbar).not.toContainText('XML');
  const table = page.locator('.vat-return-table');
  await expect(table.locator('.vat-coverage-header')).toContainText('Mua vào');
  await expect(table.locator('.vat-coverage-header')).toContainText('Bán ra');
  await expect(table).not.toContainText('Số lượng hóa đơn');
  await expect(table.locator('.artifact-coverage-badge').nth(0)).toContainText('Đã đồng bộ');
  await expect(table.locator('.artifact-coverage-badge').nth(0).locator('.artifact-coverage-heading i')).toContainText('✓');
  await expect(table.locator('.artifact-coverage-badge').nth(0)).toContainText(`${displayDate(defaultRange.dateFrom)} - ${displayDate(defaultRange.dateTo)}`);
  await expect(table.locator('.artifact-coverage-badge').nth(1).locator('.artifact-coverage-heading i')).toContainText('!');
  await expect(table.locator('.artifact-coverage-badge').nth(1)).toContainText(`Thiếu Chi tiết: ${displayDate(defaultRange.dateFrom)} - ${displayDate(defaultRange.dateTo)}`);
  await page.screenshot({ path: 'test-results/vat-return-page.png', fullPage: true });
  await toolbar.getByRole('button', { name: 'Xuất tờ khai thuế GTGT', exact: true }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText('Bán ra');
  await expect(dialog).toContainText('Quản lý HĐĐT');
  await expect(dialog.locator('.notice-icon')).toHaveAttribute('data-kind', 'warning');
});

test('date field label stays inside the toolbar at desktop and narrow widths', async ({ page }) => {
  for (const width of [1500, 1024, 720]) {
    await page.setViewportSize({ width, height: 900 });
    const toolbar = page.getByLabel('Thiết lập xuất tờ khai thuế GTGT');
    const label = toolbar.getByText('1. Khoảng thời gian', { exact: true });
    const [toolbarBox, labelBox] = await Promise.all([toolbar.boundingBox(), label.boundingBox()]);
    expect(toolbarBox).not.toBeNull();
    expect(labelBox).not.toBeNull();
    expect(labelBox!.y).toBeGreaterThan(toolbarBox!.y);
    expect(labelBox!.x).toBeGreaterThan(toolbarBox!.x);
    expect(labelBox!.y + labelBox!.height).toBeLessThan(toolbarBox!.y + toolbarBox!.height);
  }
});

test('exports exactly one template workbook when both directions are ready', async ({ page }) => {
  await page.locator('.date-range-trigger').click();
  await page.getByLabel('Từ ngày nhập tay').fill('01/11/2023');
  await page.getByLabel('Đến ngày nhập tay').fill('30/11/2023');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await page.getByLabel('Thiết lập xuất tờ khai thuế GTGT').getByRole('button', { name: 'Xuất tờ khai thuế GTGT', exact: true }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Thông báo thành công' });
  await expect(dialog).toContainText('Đã tạo tờ khai thuế GTGT thành công.');
  await expect(dialog).toContainText('To_khai_thue_GTGT_0101234567_01-11-2023_30-11-2023.xlsx');
  await expect(dialog.locator('.notice-icon')).toHaveAttribute('data-kind', 'success');
  await expect(dialog.locator('.notice-icon')).toHaveText('✓');
  await expect(dialog.locator('.notice-icon')).not.toHaveText('!');
  await expect(dialog.locator('.popup-path')).toHaveText('C:\\MIA\\Thư mục kết quả VAT có tên rất dài\\To_khai_thue_GTGT_0101234567_01-11-2023_30-11-2023.xlsx');

  mkdirSync('outputs/vat-return-popup', { recursive: true });
  for (const viewport of [{ width: 1366, height: 768 }, { width: 1024, height: 768 }, { width: 800, height: 600 }]) {
    await page.setViewportSize(viewport);
    const [dialogBox, pathBox, buttonBox] = await Promise.all([
      dialog.boundingBox(), dialog.locator('.popup-path').boundingBox(), dialog.getByRole('button', { name: 'Đóng' }).boundingBox(),
    ]);
    expect(dialogBox).not.toBeNull(); expect(pathBox).not.toBeNull(); expect(buttonBox).not.toBeNull();
    expect(dialogBox!.x).toBeGreaterThanOrEqual(16);
    expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(viewport.width - 16);
    expect(pathBox!.x).toBeGreaterThanOrEqual(dialogBox!.x);
    expect(pathBox!.x + pathBox!.width).toBeLessThanOrEqual(dialogBox!.x + dialogBox!.width);
    expect(buttonBox!.y + buttonBox!.height).toBeLessThanOrEqual(dialogBox!.y + dialogBox!.height);
    await page.screenshot({ path: `outputs/vat-return-popup/success-${viewport.width}x${viewport.height}.png` });
  }
});

test('locked destination uses the red error popup and never invents a suffixed path', async ({ page }) => {
  await page.evaluate(() => { (window as Window & { __vatExportMode?: string }).__vatExportMode = 'locked'; });
  await page.locator('.date-range-trigger').click();
  await page.getByLabel('Từ ngày nhập tay').fill('01/11/2023');
  await page.getByLabel('Đến ngày nhập tay').fill('30/11/2023');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await page.getByLabel('Thiết lập xuất tờ khai thuế GTGT').getByRole('button', { name: 'Xuất tờ khai thuế GTGT', exact: true }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Thông báo lỗi' });
  await expect(dialog.locator('.notice-icon')).toHaveAttribute('data-kind', 'error');
  await expect(dialog.locator('.notice-icon')).toHaveText('×');
  await expect(dialog).toContainText('file đang được mở');
  await expect(dialog).toContainText('Vui lòng đóng file');
  await expect(dialog.locator('.popup-path')).toHaveText('C:\\MIA\\To_khai_thue_GTGT_0101234567_01-11-2023_30-11-2023.xlsx');
  await expect(dialog).not.toContainText('_5.xlsx');
  mkdirSync('outputs/vat-return-popup', { recursive: true });
  await page.setViewportSize({ width: 800, height: 600 });
  await page.screenshot({ path: 'outputs/vat-return-popup/file-locked-800x600.png' });
});
