import { expect, test } from './licensed-test';

test('failed account downloads diagnostics while healthy account still opens results', async ({ page }) => {
  await page.addInitScript(() => {
    const accounts = ['broken', 'healthy'].map((id) => ({ connection_id: id, username: id === 'broken' ? '0101234567' : '0101234568', company_name: id, status: id === 'broken' ? 'auth_failed' : 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false }));
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => accounts },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      logs: { exportAccount: async (snapshot: unknown) => { (window as unknown as { downloaded: unknown }).downloaded = snapshot; return { saved: true }; } },
    } });
  });
  await page.goto('/');
  const broken = page.locator('.invoice-page .table-row').filter({ hasText: 'broken' });
  await expect(broken.getByRole('button', { name: 'Tải mã lỗi', exact: true })).toBeVisible();
  await expect(broken.getByRole('button', { name: 'Xem kết quả' })).toHaveCount(0);
  await expect(page.locator('.invoice-page .table-row').filter({ hasText: 'healthy' }).getByRole('button', { name: 'Xem kết quả' })).toBeVisible();
  await broken.getByRole('button', { name: 'Tải mã lỗi', exact: true }).click();
  await expect(page.getByText('Đã tải mã lỗi. Vui lòng gửi file JSON vừa lưu cho bộ phận kỹ thuật.')).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { downloaded: { connection_id: string } }).downloaded.connection_id)).toBe('broken');
});
