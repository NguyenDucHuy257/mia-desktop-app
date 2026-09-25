import { expect, test } from '@playwright/test';

async function installLicenseBridge(page: import('@playwright/test').Page, initialState: Record<string, unknown>, offlineState?: Record<string, unknown>) {
  await page.addInitScript(({ state, localState }) => {
    let current = state;
    let currentLocal = localState;
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
        offlineAuth: {
          status: async () => currentLocal,
          create: async () => currentLocal,
          unlock: async () => currentLocal,
          change: async () => currentLocal,
        },
      },
    });
  }, { state: initialState, localState: offlineState ?? { state: 'locked', configured: true, unlocked: false, retry_after_seconds: 0 } });
}

test('shows the MIA legacy migration screen without asking for phone', async ({ page }) => {
  await installLicenseBridge(page, { state: 'migrating', active: false });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Đang nâng cấp bản quyền' })).toBeVisible();
  await expect(page.getByText('Bạn không cần nhập lại key.')).toBeVisible();
  await expect(page.getByLabel('Số điện thoại đăng ký')).toHaveCount(0);
});

test('opens Phone Form only for no-match and keeps the activation action in the same gate', async ({ page }) => {
  await installLicenseBridge(page, { state: 'phone_required', active: false });
  await page.goto('/');
  const phone = page.getByLabel('Số điện thoại đăng ký');
  await expect(phone).toBeVisible();
  await page.screenshot({ path: 'test-results/license/new-device-phone.png', fullPage: true });
  await phone.fill('0981234567');
  await page.getByLabel('Email khôi phục').fill('ketoan@example.com');
  await page.getByRole('button', { name: 'Cập nhật' }).click();
  await expect(page.getByRole('heading', { name: 'Mã kích hoạt chưa được cấp quyền' })).toBeVisible();
  await expect(page.getByText('KEYV2-STABLE-TEST-KEY-0981234567')).toBeVisible();
  await page.screenshot({ path: 'test-results/license/activation-required.png', fullPage: true });
});

test('asks a detected legacy customer for phone without asking for a new key', async ({ page }) => {
  await installLicenseBridge(page, { state: 'legacy_phone_required', active: false });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Bổ sung số điện thoại' })).toBeVisible();
  await expect(page.getByText('Bạn không cần cấp lại key.')).toBeVisible();
  await expect(page.getByLabel('Số điện thoại đăng ký')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Mã kích hoạt chưa được cấp quyền' })).toHaveCount(0);
  await page.screenshot({ path: 'test-results/license/legacy-phone-required.png', fullPage: true });
});

test('rejects dummy phone before calling activation', async ({ page }) => {
  await installLicenseBridge(page, { state: 'phone_required', active: false });
  await page.goto('/');
  await page.getByLabel('Số điện thoại đăng ký').fill('0000000000');
  await page.getByLabel('Email khôi phục').fill('ketoan@example.com');
  await page.getByRole('button', { name: 'Cập nhật' }).click();
  await expect(page.getByText(/Vui lòng nhập số điện thoại hợp lệ/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Mã kích hoạt chưa được cấp quyền' })).toHaveCount(0);
});

for (const scenario of [
  { state: 'checking', heading: 'Đang kiểm tra bản quyền' },
  { state: 'expired', heading: 'Bản quyền đã hết hạn' },
  { state: 'verification_required', heading: 'Cần xác minh thêm' },
  { state: 'error', reason: 'license_network_error', heading: 'Không thể kết nối máy chủ bản quyền' },
]) {
  test(`renders the ${scenario.state} license state`, async ({ page }) => {
    await installLicenseBridge(page, { state: scenario.state, active: false, reason: scenario.reason });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: scenario.heading })).toBeVisible();
  });
}

test('does not render the workspace for a legacy-shaped active response missing strict KEYV2 fields', async ({ page }) => {
  await installLicenseBridge(page, { state: 'active', active: true });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Không thể kiểm tra bản quyền' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Quản lý HĐĐT' })).toHaveCount(0);
});

test('does not accept a support hotline as the registration phone', async ({ page }) => {
  await installLicenseBridge(page, { state: 'phone_required', active: false });
  await page.goto('/');
  await page.getByLabel('Số điện thoại đăng ký').fill('0865219286');
  await page.getByLabel('Email khôi phục').fill('ketoan@example.com');
  await page.getByRole('button', { name: 'Cập nhật' }).click();
  await expect(page.getByText(/Vui lòng nhập số điện thoại hợp lệ/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Mã kích hoạt chưa được cấp quyền' })).toHaveCount(0);
});

test('asks the user to create a local password after a valid key is confirmed', async ({ page }) => {
  await installLicenseBridge(page,
    { state: 'active', active: true, valid: true, expired: false, reason: 'ok' },
    { state: 'setup_required', configured: false, unlocked: false, retry_after_seconds: 0 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Tạo mật khẩu đăng nhập' })).toBeVisible();
  await expect(page.getByLabel('Mật khẩu mới', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Nhập lại mật khẩu', { exact: true })).toBeVisible();
  await expect(page.getByText('không được gửi lên server')).toBeVisible();
  await page.screenshot({ path: 'test-results/license/offline-password-setup.png', fullPage: true });
});

test('asks for the local password on later application launches', async ({ page }) => {
  await installLicenseBridge(page,
    { state: 'active', active: true, valid: true, expired: false, reason: 'ok' },
    { state: 'locked', configured: true, unlocked: false, retry_after_seconds: 0 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Đăng nhập trên máy này' })).toBeVisible();
  await expect(page.getByLabel('Mật khẩu', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Đăng nhập' })).toBeVisible();
  await page.screenshot({ path: 'test-results/license/offline-password-login.png', fullPage: true });
});
