import { expect, test } from '@playwright/test';

test('sidebar, support link and account popup use the shared shell', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'shell-account', username: '0101234567', company_name: 'CÔNG TY TNHH MIA TEST', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const copied: string[] = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (value: string) => { copied.push(value); } } });
    Object.defineProperty(window, '__shellTest', { configurable: true, value: { external: [] as string[], copied } });
    Object.defineProperty(window, 'miaRuntime', { configurable: true, value: {
      license: {
        initialize: async () => ({ state: 'active', active: true, valid: true, expired: false, reason: 'ok' }),
        details: async () => ({ state: 'active', active: true, phone: '098****321', phone_status: 'verified', expires_at: null, device_bound: true, canonical_key: 'KEYV2-****F82B', reason: 'ok', mode: null }),
        revealKey: async () => 'KEYV2-20cd0a15bc1ab172b385707877c0f82b-0987654321',
      },
      offlineAuth: {
        status: async () => ({ state: 'unlocked', configured: true, unlocked: true, retry_after_seconds: 0 }),
        change: async () => ({ state: 'unlocked', configured: true, unlocked: true, retry_after_seconds: 0 }),
      },
      accountConnections: { list: async () => [account] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      external: { open: async (url: string) => { (window as unknown as { __shellTest: { external: string[] } }).__shellTest.external.push(url); return true; } },
    } });
  });
  await page.goto('/');

  const brandName = page.locator('.brand-row strong');
  await expect(brandName).toHaveText('MIA TOOL 2026');
  await expect(brandName).toHaveCSS('white-space', 'nowrap');
  await expect(brandName).toHaveCSS('font-size', '18px');
  await expect(brandName).toHaveCSS('font-weight', '800');
  await expect(brandName).toHaveCSS('color', 'rgb(15, 122, 67)');
  expect((await brandName.boundingBox())?.height).toBeLessThanOrEqual(22);

  const menu = page.locator('.app-navigation .nav-button');
  await expect(menu).toHaveCount(8);
  await expect(menu).toHaveText(['Quản lý HĐĐT', 'XML/HTML/PDF', 'Xuất tờ khai thuế GTGT', 'Tra cứu PDF gốc', 'Tra cứu MVT', 'Lịch sử tải xuống', 'Cài đặt hệ thống', 'Hướng dẫn sử dụng']);
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
  expect(await page.evaluate(() => (window as unknown as { __shellTest: { external: string[] } }).__shellTest.external)).toEqual(['https://zalo.me/1239687147063946847']);

  await page.getByRole('button', { name: 'Thông tin tài khoản' }).click();
  const popup = page.getByRole('dialog', { name: 'Thông tin tài khoản' });
  await expect(popup).toBeVisible();
  await expect(popup).not.toContainText('CÔNG TY TNHH MIA TEST');
  await expect(popup).not.toContainText('MST:');
  await expect(popup).not.toContainText('SĐT:');
  await expect(popup).not.toContainText('Đăng xuất');
  await expect(popup).toContainText('MIA TOOL 2026 4.2.2');
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
  await page.getByRole('button', { name: 'Tra cứu PDF gốc', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Tra cứu PDF gốc' })).toBeVisible();
  await expect(page.getByText('Chức năng đang cập nhật')).toBeVisible();
  await page.getByRole('button', { name: 'Cài đặt hệ thống', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Mật khẩu đăng nhập' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Đổi mật khẩu' })).toBeVisible();
  await expect(page.locator('.utility-page--settings')).toHaveCSS('overflow-y', 'auto');
  await expect(page.getByLabel('Số lần thử lại')).toHaveCSS('height', '42px');
  await page.screenshot({ path: 'test-results/settings/system-settings.png', fullPage: false });
  await page.getByRole('button', { name: 'Đổi mật khẩu' }).click();
  await page.locator('.utility-page--settings').hover();
  await page.mouse.wheel(0, 700);
  await expect.poll(() => page.locator('.utility-page--settings').evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  const guide = page.getByRole('button', { name: 'Hướng dẫn sử dụng', exact: true });
  await expect(guide.locator('svg')).toHaveCount(1);
  await guide.click();
  await expect(page.getByRole('heading', { name: 'Hướng dẫn sử dụng', exact: true })).toBeVisible();
  await expect(page.getByTitle('Hướng dẫn sử dụng MIA TOOL 2026')).toBeVisible();
  const openGuideVideo = page.getByRole('button', { name: 'Mở video trên YouTube' });
  await expect(openGuideVideo).toBeVisible();
  await openGuideVideo.click();
  expect(await page.evaluate(() => (window as unknown as { __shellTest: { external: string[] } }).__shellTest.external))
    .toContain('https://www.youtube.com/watch?v=17FEQpNv4Tw');
});
