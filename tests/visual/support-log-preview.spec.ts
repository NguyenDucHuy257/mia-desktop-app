import { expect, test } from '@playwright/test';

test('preview blocked license form with support log download', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'miaRuntime', { value: {
      license: { initialize: async () => ({ state: 'error', active: false, reason: 'legacy_migration_record_incomplete' }) },
      logs: { exportSupport: async () => ({ saved: true }) },
    } });
  });
  await page.setViewportSize({ width: 1200, height: 850 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Tải log lỗi' })).toBeVisible();
  await page.screenshot({ path: 'artifacts/support-log-license-form.png', fullPage: true });
  await page.getByRole('button', { name: 'Tải log lỗi' }).click();
  await expect(page.getByText('Đã lưu log. Vui lòng gửi file này cho bộ phận hỗ trợ.')).toBeVisible();
});
