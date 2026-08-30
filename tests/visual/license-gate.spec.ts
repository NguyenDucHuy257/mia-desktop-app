import { expect, test } from '@playwright/test';

async function installLicenseBridge(page: import('@playwright/test').Page, initialState: Record<string, unknown>) {
  await page.addInitScript((state) => {
    let current = state;
    Object.defineProperty(window, 'miaRuntime', {
      configurable: true,
      value: {
        license: {
          status: async () => current,
          initialize: async () => current,
          submitPhone: async () => {
            current = { state: 'activation_required', active: false, reason: 'key_not_activated', activation_key: 'KEYV2-STABLE-TEST-KEY-0981234567' };
            return current;
          },
          retry: async () => current,
          details: async () => ({ state: current.state, active: current.active }),
          updatePhone: async () => current,
        },
      },
    });
  }, initialState);
}

test('shows the MIA legacy migration screen without asking for phone', async ({ page }) => {
  await installLicenseBridge(page, { state: 'migrating', active: false });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Đang nâng cấp bản quyền' })).toBeVisible();
  await expect(page.getByText('Bạn không cần nhập lại key.')).toBeVisible();
  await expect(page.getByLabel('Số điện thoại')).toHaveCount(0);
});

test('opens Phone Form only for no-match and keeps the activation action in the same gate', async ({ page }) => {
  await installLicenseBridge(page, { state: 'phone_required', active: false });
  await page.goto('/');
  const phone = page.getByLabel('Số điện thoại');
  await expect(phone).toBeVisible();
  await phone.fill('0981234567');
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(page.getByRole('heading', { name: 'Thiết bị chưa được kích hoạt' })).toBeVisible();
  await expect(page.getByText('KEYV2-STABLE-TEST-KEY-0981234567')).toBeVisible();
});

test('asks a detected legacy customer for phone without asking for a new key', async ({ page }) => {
  await installLicenseBridge(page, { state: 'legacy_phone_required', active: false });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Bổ sung số điện thoại' })).toBeVisible();
  await expect(page.getByText('Bạn không cần cấp lại key.')).toBeVisible();
  await expect(page.getByLabel('Số điện thoại')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Thiết bị chưa được kích hoạt' })).toHaveCount(0);
});

test('rejects dummy phone before calling activation', async ({ page }) => {
  await installLicenseBridge(page, { state: 'phone_required', active: false });
  await page.goto('/');
  await page.getByLabel('Số điện thoại').fill('0000000000');
  await page.getByRole('button', { name: 'Tiếp tục' }).click();
  await expect(page.getByText(/Vui lòng nhập số điện thoại hợp lệ/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Thiết bị chưa được kích hoạt' })).toHaveCount(0);
});
