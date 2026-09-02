import { expect, test } from '@playwright/test';

test('sidebar, support link and account popup use the shared shell', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'shell-account', username: '0101234567', company_name: 'CÔNG TY TNHH MIA TEST', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const copied: string[] = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (value: string) => { copied.push(value); } } });
    Object.defineProperty(window, '__shellTest', { configurable: true, value: { external: [] as string[], copied } });
    Object.defineProperty(window, 'miaRuntime', { configurable: true, value: {
      license: {
        initialize: async () => ({ state: 'active', active: true }),
        details: async () => ({ state: 'active', active: true, phone: '098****321', phone_status: 'verified', expires_at: null, device_bound: true, canonical_key: 'KEYV2-****F82B', reason: null, mode: null }),
        revealKey: async () => 'KEYV2-20cd0a15bc1ab172b385707877c0f82b-0987654321',
      },
      accountConnections: { list: async () => [account] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      external: { open: async (url: string) => { (window as unknown as { __shellTest: { external: string[] } }).__shellTest.external.push(url); return true; } },
    } });
  });
  await page.goto('/');

  const menu = page.locator('.app-navigation .nav-button');
  await expect(menu).toHaveCount(7);
  await expect(menu).toHaveText(['Quản lý HĐĐT', 'XML/HTML/PDF', 'Xuất tờ khai thuế GTGT', 'Lịch sử tải xuống', 'Tra cứu MVT', 'Cài đặt hệ thống', 'Hướng dẫn sử dụng']);
  await expect(page.locator('.sidebar')).not.toContainText('Danh sách MST');
  await expect(page.locator('.brand > span')).toHaveText('Giải pháp tải HDDT hàng loạt');
  await expect(page.locator('.topbar-company h1')).toHaveText('CÔNG TY CỔ PHẦN GIẢI PHÁP VÀ CÔNG NGHỆ SỐ WETECH');
  await expect(page.locator('.topbar-company h1')).toHaveCSS('color', 'rgb(22, 101, 52)');
  await expect(page.locator('.topbar')).toHaveCSS('height', '52px');
  const verifiedIcon = page.locator('.topbar-company-description img');
  await expect(verifiedIcon).toHaveCount(1);
  await expect(verifiedIcon).toHaveAttribute('alt', 'Đã xác minh');
  await expect(verifiedIcon).toHaveCSS('width', '12px');
  await expect(verifiedIcon).toHaveCSS('height', '12px');
  await expect(page.locator('.support-hotline')).toContainText('0383.466.992 - 0865.219.286');

  await page.getByRole('button', { name: 'Liên hệ ngay' }).click();
  expect(await page.evaluate(() => (window as unknown as { __shellTest: { external: string[] } }).__shellTest.external)).toEqual(['https://chat.zalo.me/']);

  await page.getByRole('button', { name: 'Thông tin tài khoản' }).click();
  const popup = page.getByRole('dialog', { name: 'Thông tin tài khoản' });
  await expect(popup).toBeVisible();
  await expect(popup).not.toContainText('CÔNG TY TNHH MIA TEST');
  await expect(popup).not.toContainText('MST:');
  await expect(popup).not.toContainText('SĐT:');
  await expect(popup).not.toContainText('Đăng xuất');
  await expect(popup).toContainText('MIA 4.0.5');
  await expect(popup.locator('code')).not.toContainText('20cd0a15');
  await popup.getByRole('button', { name: 'Hiện' }).click();
  await expect(popup.locator('code')).toHaveText('KEYV2-20cd0a15bc1ab172b385707877c0f82b-0987654321');
  await popup.getByRole('button', { name: 'Sao chép' }).click();
  await expect(page.getByRole('status')).toHaveText('Đã sao chép key xác thực');
  expect(await page.evaluate(() => (window as unknown as { __shellTest: { copied: string[] } }).__shellTest.copied)).toEqual(['KEYV2-20cd0a15bc1ab172b385707877c0f82b-0987654321']);

  await page.keyboard.press('Escape');
  await expect(popup).toBeHidden();

  await page.getByRole('button', { name: 'Tra cứu MVT', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Tra cứu MVT' })).toBeVisible();
  await expect(page.getByText('Chức năng đang cập nhật')).toBeVisible();
  const guide = page.getByRole('button', { name: 'Hướng dẫn sử dụng', exact: true });
  await expect(guide.locator('svg')).toHaveCount(1);
  await guide.click();
  await expect(page.getByRole('heading', { name: 'Hướng dẫn sử dụng' })).toBeVisible();
  await expect(page.getByText('Chức năng đang cập nhật')).toBeVisible();
});
