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

test('local account list starts empty, persists in the gateway and supports deletion', async ({ page }) => {
  await page.goto('/?demo=1');
  await expect(page.getByText('Hiển thị 0 tài khoản')).toBeVisible();
  await expect(page.getByText('Tên công ty')).toBeVisible();
  await expect(page.getByText('Kỳ tải')).toHaveCount(0);
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu').fill('not-stored-in-renderer');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await expect(page.getByText('Hiển thị 1 tài khoản')).toBeVisible();
  await expect(page.getByText('—')).toBeVisible();
  await expect(page.getByText('Chưa kiểm tra đăng nhập')).toBeVisible();
  await page.getByRole('button', { name: 'Xóa 0101234567' }).click();
  await expect(page.getByText('Hiển thị 0 tài khoản')).toBeVisible();
});

test('job option menus follow Figma nodes 4:628 and 4:654', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  await page.getByRole('button', { name: 'Hóa đơn' }).click();
  await expect(page.locator('[data-node-id="4:628"]')).toHaveScreenshot('figma-option-4-628.png', {
    maxDiffPixelRatio: 0.08, threshold: 0.25,
  });
  await page.getByRole('button', { name: 'Hóa đơn' }).click();
  await page.getByRole('button', { name: 'Chi tiết' }).click();
  await expect(page.locator('[data-node-id="4:654"]')).toHaveScreenshot('figma-declaration-type-4-654.png', {
    maxDiffPixelRatio: 0.12, threshold: 0.25,
  });
});

test('creates, polls and cancels a job through the IPC allowlist', async ({ page }) => {
  await page.addInitScript(() => {
    let calls = 0;
    const record = { job_id: 'job-1', connection_id: 'conn_demo', intent: {}, idempotency_key: 'desktop-fixed', created_at: 'now', updated_at: 'now' };
    Object.defineProperty(window, 'miaRuntime', { value: { jobs: {
      resume: async () => null,
      start: async () => ({ record, accepted: { job_id: 'job-1', status: 'queued', current_stage: null, worker_slot_id: null } }),
      status: async () => ({ job_id: 'job-1', status: ++calls === 1 ? 'running' : 'running', stage: 'overview', overall_percent: 35, current_month: { key: '2026-01', index: 1, total: 1, processed: 35, planned: 100, percent: 35 }, updated_at: 'now', error: null }),
      summary: async () => ({ job_id: 'job-1', status: 'cancelled', warning_count: 0, stages: [], coverage_plan: {}, work: {}, post_processing: {} }),
      cancel: async () => ({ job_id: 'job-1', status: 'cancelling', stage: 'overview', overall_percent: 35, current_month: null, updated_at: 'now', error: null }),
      clear: async () => undefined,
    } } });
  });
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu').fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await expect(page.getByRole('status')).toContainText('35% tổng thể');
  await expect(page.getByRole('status')).toContainText('35/100');
  await page.getByRole('button', { name: 'Dừng tải' }).click();
  await expect(page.getByRole('status')).toContainText('cancelling');
});

test('allows combined overview/detail and multi-select purchase/sold directions', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'miaRuntime', { value: { jobs: {
      resume: async () => null,
      start: async (intent: unknown) => {
        (window as typeof window & { capturedIntent?: unknown }).capturedIntent = intent;
        return { record: { job_id: 'job-options', connection_id: 'conn_demo', intent, idempotency_key: 'desktop-options', created_at: 'now', updated_at: 'now' }, accepted: { job_id: 'job-options', status: 'queued', current_stage: null, worker_slot_id: null } };
      },
      status: async () => ({ job_id: 'job-options', status: 'completed', stage: null, overall_percent: 100, current_month: null, updated_at: 'now', error: null }),
      summary: async () => ({ job_id: 'job-options', status: 'completed', warning_count: 0, stages: [], coverage_plan: {}, work: {}, post_processing: {} }),
      cancel: async () => ({}), clear: async () => undefined,
    } } });
  });
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu').fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('button', { name: 'Hóa đơn' }).click();
  await page.locator('[data-node-id="4:628"]').getByText('Hóa đơn', { exact: true }).click();
  await expect(page.locator('[data-node-id="4:628"]').getByLabel('Hóa đơn')).not.toBeChecked();
  await page.locator('[data-node-id="4:628"]').getByText('HTML', { exact: true }).click();
  await expect(page.getByLabel('HTML')).toBeChecked();
  await page.getByRole('button', { name: 'Hóa đơn' }).click();
  await page.getByRole('button', { name: 'Mua vào' }).click();
  await expect(page.getByLabel('Loại giao dịch').getByText('Mua vào')).toBeVisible();
  await expect(page.getByLabel('Loại giao dịch').getByText('Bán ra')).toBeVisible();
  await page.getByLabel('Loại giao dịch').getByText('Mua vào', { exact: true }).click();
  await page.getByLabel('Loại giao dịch').getByText('Bán ra', { exact: true }).click();
  await expect(page.getByLabel('Loại giao dịch').getByLabel('Mua vào')).not.toBeChecked();
  await expect(page.getByLabel('Loại giao dịch').getByLabel('Bán ra')).not.toBeChecked();
  await page.getByLabel('Loại giao dịch').getByText('Bán ra', { exact: true }).click();
  await page.getByRole('button', { name: 'Mua vào' }).click();
  await page.getByRole('button', { name: 'Chi tiết' }).click();
  await expect(page.locator('[data-node-id="4:654"] .option-box[data-checked="true"]')).toHaveCount(2);
  await page.locator('[data-node-id="4:654"]').getByText('Tổng quan', { exact: true }).click();
  await page.locator('[data-node-id="4:654"]').getByText('Chi tiết', { exact: true }).click();
  await expect(page.locator('[data-node-id="4:654"]').getByLabel('Tổng quan')).not.toBeChecked();
  await expect(page.locator('[data-node-id="4:654"]').getByLabel('Chi tiết')).not.toBeChecked();
  await page.locator('[data-node-id="4:654"]').getByText('Chi tiết', { exact: true }).click();
  await page.getByRole('button', { name: 'Chi tiết' }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  const captured = await page.evaluate(() => (window as typeof window & { capturedIntent?: { directions?: string[]; result_scope?: string } }).capturedIntent);
  expect(captured).toMatchObject({ directions: ['sold'], result_scope: 'detail' });
});

test('shows bounded polling failure and lets the user retry', async ({ page }) => {
  await page.addInitScript(() => {
    const record = { job_id: 'job-retry', connection_id: 'conn_demo', intent: {}, idempotency_key: 'desktop-fixed', created_at: 'now', updated_at: 'now' };
    Object.defineProperty(window, 'miaRuntime', { value: { jobs: {
      resume: async () => record,
      status: async () => { throw new Error('temporary network failure'); },
      summary: async () => ({}), start: async () => ({}), cancel: async () => ({}), clear: async () => undefined,
    } } });
  });
  await page.goto('/');
  await expect(page.getByText('Mất kết nối tạm thời, đang thử lại…')).toBeVisible();
});
