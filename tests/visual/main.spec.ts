import { expect, test } from '@playwright/test';

test('main invoice screen follows the 1500x1024 Figma reference', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const screenshot = await page.screenshot({ animations: 'disabled' });
  await expect(screenshot).toMatchSnapshot('figma-main-1500x1024.png', {
    maxDiffPixelRatio: Number(process.env.MIA_VISUAL_MAX_DIFF_RATIO ?? 0.03),
    threshold: 0.25,
  });
});

test('single account form follows Figma frame 1:368', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  const screenshot = await page.screenshot({ animations: 'disabled' });
  await expect(screenshot).toMatchSnapshot('figma-add-account-single-1500x1024.png', {
    maxDiffPixelRatio: Number(process.env.MIA_ACCOUNT_VISUAL_MAX_DIFF_RATIO ?? 0.01),
    threshold: 0.25,
  });
});

test('bulk account form follows Figma frame 60:1182', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByRole('tab', { name: 'Thêm hàng loạt' }).click();
  const screenshot = await page.screenshot({ animations: 'disabled' });
  await expect(screenshot).toMatchSnapshot('figma-add-account-bulk-1500x1024.png', {
    maxDiffPixelRatio: Number(process.env.MIA_ACCOUNT_VISUAL_MAX_DIFF_RATIO ?? 0.01),
    threshold: 0.25,
  });
});

test('account forms validate input and submit through the browser demo adapter', async ({ page }) => {
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await expect(page.getByText('Vui lòng nhập mã số thuế.')).toBeVisible();
  await expect(page.getByText('Vui lòng nhập mật khẩu.')).toBeVisible();

  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu').fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await expect(page.getByRole('status')).toHaveText('Đã thêm tài khoản thành công.');
  await expect(page.getByLabel('Mật khẩu')).toHaveValue('');

  await page.getByRole('tab', { name: 'Thêm hàng loạt' }).click();
  await page.getByLabel('Nhập danh sách tài khoản (MST|PASSWORD)').fill(
    '0309876543|password-one\ninvalid-line',
  );
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await expect(page.getByText('Dòng 2: Thiếu dấu phân cách |.')).toBeVisible();
});
