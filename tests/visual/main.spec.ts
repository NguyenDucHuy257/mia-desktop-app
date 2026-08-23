import { expect, test } from '@playwright/test';

test('main invoice screen follows the 1500x1024 Figma reference', async ({ page }) => {
  await page.goto('/?figma=1');
  await page.evaluate(() => document.fonts.ready);
  const screenshot = await page.screenshot({ animations: 'disabled' });
  await expect(screenshot).toMatchSnapshot('figma-main-1500x1024.png', {
    maxDiffPixelRatio: Number(process.env.MIA_VISUAL_MAX_DIFF_RATIO ?? 0.03),
    threshold: 0.25,
  });
});

test('single account form follows Figma frame 1:368', async ({ page }) => {
  await page.goto('/?figma=1');
  await page.evaluate(() => document.fonts.ready);
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  const screenshot = await page.screenshot({ animations: 'disabled' });
  await expect(screenshot).toMatchSnapshot('figma-add-account-single-1500x1024.png', {
    maxDiffPixelRatio: Number(process.env.MIA_ACCOUNT_VISUAL_MAX_DIFF_RATIO ?? 0.01),
    threshold: 0.25,
  });
});

test('bulk account form follows Figma frame 60:1182', async ({ page }) => {
  await page.goto('/?figma=1');
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
  await expect(page.getByRole('alertdialog', { name: 'Thông báo thành công' })).toContainText('Đã thêm tài khoản thành công.');
  await expect(page.locator('.notice-icon[data-kind="success"]')).toHaveText('✓');
  await page.getByRole('button', { name: 'Đóng' }).click();
  await expect(page.getByLabel('Mật khẩu')).toHaveValue('');

  const beforeTabs = await page.getByRole('tab').evaluateAll((tabs) => tabs.map((tab) => {
    const box = tab.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width, height: box.height };
  }));
  await page.getByRole('tab', { name: 'Thêm hàng loạt' }).click();
  const afterTabs = await page.getByRole('tab').evaluateAll((tabs) => tabs.map((tab) => {
    const box = tab.getBoundingClientRect();
    return { x: box.x, y: box.y, width: box.width, height: box.height };
  }));
  expect(afterTabs).toEqual(beforeTabs);
  await page.getByLabel('Nhập danh sách tài khoản (MST|PASSWORD)').fill(
    '0309876543|password-one\ninvalid-line',
  );
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await expect(page.getByText('Dòng 2: Thiếu dấu phân cách |.')).toBeVisible();
});

test('local account list starts empty, persists in the gateway and supports deletion', async ({ page }) => {
  await page.goto('/?demo=1');
  await expect(page.getByText('Hiển thị 0–0 trên tổng 0 tài khoản')).toBeVisible();
  await expect(page.getByText('Tên công ty')).toBeVisible();
  await expect(page.getByText('Kỳ tải')).toHaveCount(0);
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu').fill('not-stored-in-renderer');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await expect(page.getByText('Hiển thị 1–1 trên tổng 1 tài khoản')).toBeVisible();
  await expect(page.getByTitle('—')).toBeVisible();
  await expect(page.getByText('Chưa kiểm tra đăng nhập')).toBeVisible();
  const accountSelection = page.getByRole('button', { name: 'Chọn 0101234567' }).locator('.selection-box');
  await expect(accountSelection).toHaveAttribute('data-checked', 'true');
  await page.getByRole('button', { name: 'Chọn 0101234567' }).click();
  await expect(accountSelection).toHaveAttribute('data-checked', 'false');
  await page.getByRole('button', { name: 'Chọn 0101234567' }).click();
  await expect(accountSelection).toHaveAttribute('data-checked', 'true');
  await page.getByRole('button', { name: 'Xóa 0101234567' }).click();
  await expect(page.getByText('Hiển thị 0–0 trên tổng 0 tài khoản')).toBeVisible();
});

test('invoice accounts paginate by twenty after filtering and preserve selection', async ({ page }) => {
  await page.addInitScript(() => {
    const accounts = Array.from({ length: 21 }, (_, index) => ({
      connection_id: `conn_${index + 1}`,
      username: String(1000000000 + index),
      company_name: `Công ty ${String(index + 1).padStart(2, '0')}`,
      status: 'connected', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false,
    }));
    Object.defineProperty(window, 'miaRuntime', { value: { accountConnections: {
      list: async () => [...accounts],
      create: async () => accounts[0],
      get: async (id: string) => accounts.find((account) => account.connection_id === id),
      reconnect: async () => accounts[0],
      revoke: async (id: string) => { const index = accounts.findIndex((account) => account.connection_id === id); if (index >= 0) accounts.splice(index, 1); },
    } } });
  });
  await page.goto('/');
  await expect(page.locator('.table-row')).toHaveCount(20);
  await expect(page.getByText('Hiển thị 1–20 trên tổng 21 tài khoản')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Chọn 1000000000' }).locator('.selection-box')).toHaveAttribute('data-checked', 'true');

  await page.getByRole('button', { name: 'Trang sau' }).click();
  await expect(page.locator('.table-row')).toHaveCount(1);
  await expect(page.getByText('Hiển thị 21–21 trên tổng 21 tài khoản')).toBeVisible();
  await page.getByRole('button', { name: 'Trang trước' }).click();
  await expect(page.getByRole('button', { name: 'Chọn 1000000000' }).locator('.selection-box')).toHaveAttribute('data-checked', 'true');

  await page.getByLabel('Tìm kiếm tài khoản').fill('Công ty 21');
  await expect(page.locator('.table-row')).toHaveCount(1);
  await expect(page.getByText('Hiển thị 1–1 trên tổng 1 tài khoản')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Trang sau' })).toBeDisabled();
  await page.getByLabel('Tìm kiếm tài khoản').fill('');
  await page.getByRole('button', { name: 'Trang sau' }).click();
  await page.getByRole('button', { name: 'Xóa 1000000020' }).click();
  await expect(page.locator('.table-row')).toHaveCount(20);
  await expect(page.getByText('Hiển thị 1–20 trên tổng 20 tài khoản')).toBeVisible();
  await expect(page.locator('.pagination button[data-active="true"]')).toHaveText('1');
});

test('detail results retain total rows on page two and empty export is stopped before lifecycle', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_results', username: '0100000000', company_name: 'Công ty Kết quả', status: 'connected', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const record = { job_id: 'job_results', connection_id: account.connection_id, intent: {}, idempotency_key: 'desktop-results', created_at: 'now', updated_at: 'now', status: 'running' };
    let exportCalls = 0;
    Object.defineProperty(window, 'resultExportCalls', { get: () => exportCalls });
    const resultPage = (query: { cursor?: string | null; search?: string }) => {
      if (query.search) return { items: [], columns: ['stt', 'ten'], column_labels: { stt: 'STT', ten: 'Tên hàng hóa' }, total_count: 0, pagination: { limit: 50, has_more: false, next_cursor: null } };
      const second = Boolean(query.cursor);
      const count = second ? 23 : 50;
      return {
        items: Array.from({ length: count }, (_, index) => ({ row_id: `${second ? 50 : 0}-${index}`, direction: 'purchase', fields: { stt: (second ? 50 : 0) + index + 1, ten: `Dòng ${(second ? 50 : 0) + index + 1}` } })),
        columns: ['stt', 'ten'], column_labels: { stt: 'STT', ten: 'Tên hàng hóa' }, total_count: 73,
        pagination: { limit: 50, has_more: !second, next_cursor: second ? null : 'detail-page-2' },
      };
    };
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], create: async () => account, get: async () => account, reconnect: async () => account, revoke: async () => undefined },
      jobs: {
        resume: async () => record,
        resumeAll: async () => [record],
        start: async () => ({ record, accepted: {} }),
        status: async () => ({ job_id: record.job_id, status: 'completed', stage: null, overall_percent: 100, current_month: null, updated_at: 'now', error: null }),
        summary: async () => ({ job_id: record.job_id, status: 'completed', warning_count: 0, stages: [], coverage_plan: {}, work: {}, post_processing: {} }),
        cancel: async () => ({}), clear: async () => undefined,
      },
      results: { overview: async (query: { cursor?: string | null; search?: string }) => resultPage(query), details: async (query: { cursor?: string | null; search?: string }) => resultPage(query) },
      artifacts: { export: async () => { exportCalls += 1; return { count: 1, files: ['D:\\MIA\\result.xlsx'] }; } },
    } });
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Xem kết quả' })).toBeVisible();
  await page.getByRole('button', { name: 'Xem kết quả' }).click();
  await page.getByRole('tab', { name: 'Chi tiết' }).click();
  await expect(page.getByText('Tổng 73 hàng · tối đa 50 hàng/trang')).toBeVisible();
  await page.getByRole('button', { name: 'Trang sau' }).click();
  await expect(page.locator('.results-row:not(.results-row--header)')).toHaveCount(23);
  await expect(page.getByText('Tổng 73 hàng · tối đa 50 hàng/trang')).toBeVisible();

  await page.getByLabel('Tìm kiếm kết quả').fill('không-có');
  await expect(page.getByText('Không tồn tại hóa đơn trong thời gian này.')).toBeVisible();
  await page.getByRole('button', { name: 'Tải xuống kết quả' }).click();
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect(page.getByText('Không tồn tại hóa đơn phù hợp với lựa chọn hiện tại.')).toBeVisible();
  expect(await page.evaluate(() => (window as typeof window & { resultExportCalls: number }).resultExportCalls)).toBe(0);
});

test('verified runtime account immediately shows portal company information', async ({ page }) => {
  await page.addInitScript(() => {
    const accounts: Array<Record<string, unknown>> = [];
    Object.defineProperty(window, 'miaRuntime', { value: { accountConnections: {
      list: async () => accounts,
      create: async ({ username }: { username: string }) => {
        const account = { connection_id: 'verified-1', username, company_name: 'Công ty đã xác thực', status: 'connected', token_generation: 0, created_at: 'now', updated_at: 'now', reused: false };
        accounts.push(account);
        return account;
      },
      get: async () => accounts[0], reconnect: async () => accounts[0], revoke: async () => undefined,
    } } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0100000000');
  await page.getByLabel('Mật khẩu').fill('synthetic-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await expect(page.getByText('Công ty đã xác thực')).toBeVisible();
  await expect(page.getByText('Chưa kiểm tra đăng nhập')).toHaveCount(0);
});

test('invoice scope menu follows Figma node 4:654 and closes outside', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  await page.getByRole('button', { name: 'Chi tiết' }).click();
  await expect(page.locator('[data-node-id="4:654"]')).toHaveScreenshot('figma-declaration-type-4-654.png', {
    maxDiffPixelRatio: 0.12, threshold: 0.25,
  });
  await page.locator('.invoice-content').click({ position: { x: 800, y: 300 } });
  await expect(page.locator('[data-node-id="4:654"]')).toHaveCount(0);
});

test('creates, polls and cancels a job through the IPC allowlist', async ({ page }) => {
  await page.addInitScript(() => {
    let calls = 0;
    const record = { job_id: 'job-1', connection_id: 'conn_demo', intent: {}, idempotency_key: 'desktop-fixed', created_at: 'now', updated_at: 'now' };
    Object.defineProperty(window, 'miaRuntime', { value: { jobs: {
      resume: async () => null,
      syncStates: async (connectionIds: string[], direction: string) => connectionIds.map((connection_id) => direction === 'purchase' ? ({
        connection_id, direction, status: 'completed', current_month: null,
        sync_from: '2025-01-01', sync_until: '2025-08-31', invoice_count: 1320,
        baseline_invoice_count: null, added_invoice_count: null, last_job_id: 'old-purchase', sync_mode: 'new',
      }) : ({
        connection_id, direction, status: 'not_synced', current_month: null,
        sync_from: null, sync_until: null, invoice_count: 0,
        baseline_invoice_count: null, added_invoice_count: null, last_job_id: null, sync_mode: null,
      })),
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
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await expect(page.getByText('1.320 hóa đơn')).toBeVisible();
  await expect(page.getByText('Đã đồng bộ', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await page.getByRole('menuitem', { name: /Đồng bộ mới/ }).click();
  await expect(page.locator('.progress-cell')).toContainText('35%');
  await expect(page.locator('.progress-cell')).toContainText('Đang chuẩn bị dữ liệu tổng quan');
  await page.getByRole('button', { name: 'Dừng tải' }).click();
  await expect(page.getByRole('button', { name: 'Đồng bộ dữ liệu' })).toContainText('Đang dừng');
});

test('keeps exactly one purchase/sold direction and sends the selected sync mode', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'miaRuntime', { value: { jobs: {
      resume: async () => null,
      syncStates: async (connectionIds: string[], direction: string) => connectionIds.map((connection_id) => direction === 'purchase' ? ({
        connection_id, direction, status: 'completed', current_month: null,
        sync_from: '2025-01-01', sync_until: '2025-08-31', invoice_count: 1320,
        baseline_invoice_count: null, added_invoice_count: null, last_job_id: 'old-purchase', sync_mode: 'new',
      }) : ({
        connection_id, direction, status: 'not_synced', current_month: null,
        sync_from: null, sync_until: null, invoice_count: 0,
        baseline_invoice_count: null, added_invoice_count: null, last_job_id: null, sync_mode: null,
      })),
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
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await expect(page.getByText('1.320 hóa đơn')).toBeVisible();
  await expect(page.getByText('Đã đồng bộ', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Mua vào' }).click();
  await expect(page.getByLabel('Loại giao dịch').getByText('Mua vào')).toBeVisible();
  await expect(page.getByLabel('Loại giao dịch').getByText('Bán ra')).toBeVisible();
  await page.getByLabel('Loại giao dịch').getByText('Mua vào', { exact: true }).click();
  await expect(page.getByLabel('Loại giao dịch').getByLabel('Mua vào')).toBeChecked();
  await expect(page.getByLabel('Loại giao dịch').getByLabel('Bán ra')).not.toBeChecked();
  await page.getByLabel('Loại giao dịch').getByText('Bán ra', { exact: true }).click();
  await expect(page.getByLabel('Loại giao dịch').getByLabel('Mua vào')).not.toBeChecked();
  await expect(page.getByLabel('Loại giao dịch').getByLabel('Bán ra')).toBeChecked();
  await expect(page.getByText('0 hóa đơn')).toBeVisible();
  await expect(page.locator('.sync-state-cell').getByText('Chưa đồng bộ', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Bán ra' }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await expect(page.getByRole('menu', { name: 'Chọn cách đồng bộ' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu', { name: 'Chọn cách đồng bộ' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Chi tiết' }).click();
  await expect(page.locator('[data-node-id="4:654"] .option-box[data-checked="true"]')).toHaveCount(2);
  await page.locator('[data-node-id="4:654"]').getByText('Tổng quan', { exact: true }).click();
  await page.locator('[data-node-id="4:654"]').getByText('Chi tiết', { exact: true }).click();
  await expect(page.locator('[data-node-id="4:654"]').getByLabel('Tổng quan')).not.toBeChecked();
  await expect(page.locator('[data-node-id="4:654"]').getByLabel('Chi tiết')).not.toBeChecked();
  await page.getByRole('button', { name: 'Chi tiết' }).click();
  await page.getByRole('button', { name: /KHOẢNG THỜI GIAN/ }).click();
  await page.getByLabel('Từ ngày đồng bộ nhập tay').fill('01/01/2026');
  await page.getByLabel('Đến ngày đồng bộ nhập tay').fill('31/01/2026');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await page.getByRole('menuitem', { name: /Đồng bộ mới/ }).click();
  await expect(page.getByRole('alertdialog', { name: 'Thông báo' })).toContainText('Vui lòng chọn ít nhất');
  await expect(page.locator('.notice-icon[data-kind="notice"]')).toHaveText('!');
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: 'Chi tiết' }).click();
  await page.locator('[data-node-id="4:654"]').getByText('Chi tiết', { exact: true }).click();
  await page.getByRole('button', { name: 'Chi tiết' }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await page.getByRole('menuitem', { name: /Đồng bộ bổ sung/ }).click();
  const captured = await page.evaluate(() => (window as typeof window & { capturedIntent?: { directions?: string[]; query_types?: string[]; scopes?: string[]; data_types?: string[]; sync_mode?: string } }).capturedIntent);
  expect(captured).toMatchObject({ date_from: '2026-01-01', date_to: '2026-01-31', directions: ['sold'], query_types: ['query', 'sco-query'], scopes: ['detail'], data_types: ['invoice'], sync_mode: 'supplement' });
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

test('shows sanitized job start errors in the account row', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'miaRuntime', { value: { jobs: {
      resume: async () => null,
      start: async () => { throw new Error('sanitized failure'); },
      status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined,
    } } });
  });
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu').fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await page.getByRole('menuitem', { name: /Đồng bộ mới/ }).click();
  await expect(page.locator('.failure-message')).toContainText('Không thể tạo tác vụ đồng bộ');
  await expect(page.locator('.failure-message')).not.toContainText('sanitized failure');
});

test('opens local overview/detail results and paginates by cursor', async ({ page }) => {
  await page.addInitScript(() => {
    const pageFor = (kind: string, cursor?: string | null) => ({
      items: [{ overview_id: cursor ? 2 : 1, detail_id: cursor ? 2 : 1, direction: 'purchase', business_key: `${kind}-${cursor ?? 'first'}`, line_key: 'line-1', payload: { company: 'Demo' } }],
      pagination: { limit: 50, has_more: !cursor, next_cursor: cursor ? null : 'djE6MQ' },
    });
    Object.defineProperty(window, 'miaRuntime', { value: { results: {
      overview: async (query: { cursor?: string | null }) => pageFor('overview', query.cursor),
      details: async (query: { cursor?: string | null }) => pageFor('detail', query.cursor),
    } } });
  });
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu').fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('button', { name: 'Mở tác vụ 0101234567' }).click();
  await page.getByRole('menuitem', { name: 'Xem kết quả' }).click();
  await expect(page.getByText('overview-first')).toBeVisible();
  await page.getByRole('button', { name: 'Tải thêm' }).click();
  await expect(page.getByText('overview-djE6MQ')).toBeVisible();
  await page.getByRole('button', { name: 'Chi tiết' }).click();
  await expect(page.getByText('detail-first')).toBeVisible();
});

test('row action menu exports one or all selected accounts and closes with Escape', async ({ page }) => {
  await page.addInitScript(() => {
    const exports: unknown[] = [];
    Object.defineProperty(window, 'artifactExports', { value: exports });
    Object.defineProperty(window, 'miaRuntime', { value: { artifacts: {
      selectDirectory: async () => 'D:\\MIA',
      export: async (request: unknown) => { exports.push(request); return { count: 1, files: ['D:\\MIA\\demo.xlsx'] }; },
    } } });
  });
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu').fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  const trigger = page.getByRole('button', { name: 'Mở tác vụ 0101234567' });
  await trigger.click();
  await expect(page.getByRole('menu')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu')).toHaveCount(0);
  await trigger.click();
  await page.getByRole('menuitem', { name: 'Tải Excel tài khoản này' }).click();
  await expect(page.getByRole('alertdialog')).toContainText('Đã xuất 1 file Excel');
  const calls = await page.evaluate(() => (window as typeof window & { artifactExports: Array<{ connection_ids: string[]; kinds: string[] }> }).artifactExports);
  expect(calls[0]?.connection_ids).toHaveLength(1);
  expect(calls[0]).toMatchObject({ kinds: ['excel'] });
});

test('XML and HTML tabs provide Figma-aligned filtering and download interactions', async ({ page }) => {
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'XML' }).click();
  await expect(page.locator('[data-node-id="1:654"]')).toBeVisible();
  await expect(page.getByRole('table', { name: 'Danh sách XML' }).getByRole('row')).toHaveCount(5);
  await page.getByRole('button', { name: 'Mua vào' }).click();
  await expect(page.getByRole('table', { name: 'Danh sách XML' }).getByRole('row')).toHaveCount(3);
  await page.getByRole('button', { name: 'Tải XML hàng loạt' }).click();
  await expect(page.getByRole('alertdialog', { name: 'Thông báo' })).toContainText('2 file XML');
  await page.getByRole('button', { name: 'Đóng' }).click();

  await page.getByRole('button', { name: 'HTML', exact: true }).click();
  await expect(page.locator('[data-node-id="104:22"]')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tải HTML hàng loạt' })).toBeVisible();
});

test('production artifact tabs render runtime files instead of demo rows', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_local', username: '0100000000', company_name: 'Công ty Runtime', status: 'connected', token_generation: 0, created_at: 'now', updated_at: 'now', reused: false };
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], get: async () => account, create: async () => account, reconnect: async () => account, revoke: async () => undefined },
      artifacts: {
        list: async (request: { cursor?: string | null; search?: string }) => {
          const filename = request.search ? `tim-${request.search}.xml` : request.cursor ? 'hoa-don-trang-2.xml' : 'hoa-don-runtime.xml';
          return { items: [{ artifact_id: `job:${filename}`, connection_id: 'conn_local', job_id: 'job_1', filename, kind: 'xml', direction: 'purchase', size: 128, updated_at: Date.now() * 1_000_000 }], pagination: { limit: 50, has_more: !request.cursor && !request.search, next_cursor: !request.cursor && !request.search ? 'cursor-2' : null } };
        },
        export: async () => ({ count: 1, files: ['D:\\MIA\\hoa-don-runtime.xml'] }), selectDirectory: async () => 'D:\\MIA', openDirectory: async () => true,
      },
    } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'XML' }).click();
  await expect(page.getByRole('table', { name: 'Danh sách XML' })).toContainText('hoa-don-runtime.xml');
  await expect(page.getByRole('table', { name: 'Danh sách XML' })).not.toContainText('HD-2023-001');
  await expect(page.getByLabel('Chọn công ty')).toContainText('Công ty Runtime');
  await page.getByRole('button', { name: 'Tải trang sau' }).click();
  await expect(page.getByRole('table', { name: 'Danh sách XML' })).toContainText('hoa-don-trang-2.xml');
  await page.getByLabel('Tìm kiếm XML').fill('ABC-123');
  await expect(page.getByRole('table', { name: 'Danh sách XML' })).toContainText('tim-ABC-123.xml');
});

test('date, company, search, status and pagination controls update the UI', async ({ page }) => {
  await page.goto('/?figma=1');
  await page.getByRole('button', { name: /KHOẢNG THỜI GIAN/ }).click();
  const calendarOffsets = await page.locator('.date-range-calendar-control').evaluateAll((controls) => controls.map((control) => {
    const icon = control.querySelector('img');
    if (!icon) return Number.POSITIVE_INFINITY;
    const controlBox = control.getBoundingClientRect();
    const iconBox = icon.getBoundingClientRect();
    return Math.max(
      Math.abs((controlBox.left + controlBox.width / 2) - (iconBox.left + iconBox.width / 2)),
      Math.abs((controlBox.top + controlBox.height / 2) - (iconBox.top + iconBox.height / 2)),
    );
  }));
  expect(calendarOffsets).toHaveLength(2);
  expect(Math.max(...calendarOffsets)).toBeLessThanOrEqual(1);
  await page.getByLabel('Từ ngày đồng bộ nhập tay').fill('01/09/2023');
  await page.getByLabel('Đến ngày đồng bộ nhập tay').fill('30/09/2023');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await page.getByLabel('Tìm kiếm tài khoản').fill('0101234567');
  await expect(page.locator('.table-row')).toHaveCount(1);
  await page.getByLabel('Lọc trạng thái').selectOption('failed');
  await expect(page.locator('.table-row')).toHaveCount(0);
  await page.getByLabel('Lọc trạng thái').selectOption('completed');
  await expect(page.locator('.table-row')).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Trang sau' })).toBeDisabled();
  await expect(page.locator('.pagination button[data-active="true"]')).toHaveText('1');
  await page.getByRole('button', { name: 'Cài đặt tài khoản' }).click();
  await expect(page.getByRole('heading', { name: 'Cài đặt' })).toBeVisible();

  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'XML' }).click();
  await page.getByRole('button', { name: /KHOẢNG THỜI GIAN/ }).click();
  await page.getByLabel('Từ ngày nhập tay').fill('01/09/2023');
  await page.getByLabel('Đến ngày nhập tay').fill('30/09/2023');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await page.getByLabel('Chọn công ty').selectOption('company-b');
  await expect(page.getByLabel('Chọn công ty')).toHaveValue('company-b');
  await page.getByRole('button', { name: 'Trang sau' }).click();
  await expect(page.locator('.artifact-pager button[data-active="true"]').first()).toHaveText('2');
});

test('settings persist scheduler limits and logs are filtered after main-process redaction', async ({ page }) => {
  await page.addInitScript(() => {
    let preferences = { concurrency: 2, retries: 5 };
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [] },
      preferences: { get: async () => preferences, set: async (value: { concurrency: number; retries: number }) => (preferences = value) },
      logs: { list: async () => ['2026 INFO storage_initialized', '2026 WARN retry_scheduled'] },
    } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Cài đặt', exact: true }).click();
  await page.getByLabel('Giới hạn tài khoản chạy đồng thời').selectOption('4');
  await page.getByLabel('Số lần thử lại').fill('1');
  await page.getByRole('button', { name: 'Lưu cài đặt' }).click();
  await expect(page.getByRole('alertdialog')).toContainText('Job mới sẽ áp dụng');
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: 'Nhật ký' }).click();
  await expect(page.locator('.utility-log-list')).toContainText('storage_initialized');
  await page.getByLabel('Tìm kiếm Nhật ký').fill('retry');
  await expect(page.locator('.utility-log-list li')).toHaveCount(1);
  await expect(page.locator('.utility-log-list')).toContainText('retry_scheduled');
});

test('bulk Excel progress stays determinate inside the toolbar button', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_local', username: '0100000000', company_name: 'Công ty Runtime', status: 'ready', token_generation: 0, created_at: 'now', updated_at: 'now', reused: false };
    let progressListener: ((value: Record<string, unknown>) => void) | undefined;
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], get: async () => account, create: async () => account, reconnect: async () => account, revoke: async () => undefined },
      jobs: { resume: async () => null, resumeAll: async () => [], latestAll: async () => [], start: async () => ({}), status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        selectDirectory: async () => 'C:\\MIA',
        onExportProgress: (listener: (value: Record<string, unknown>) => void) => { progressListener = listener; return () => { progressListener = undefined; }; },
        export: async () => {
          await new Promise((resolve) => setTimeout(resolve, 50));
          progressListener?.({ status: 'running', scope: 'details', phase: 'write_rows', processed: 43, total: 100, percent: 43 });
          await new Promise((resolve) => setTimeout(resolve, 500));
          progressListener?.({ status: 'completed', scope: 'details', phase: 'completed', processed: 100, total: 100, percent: 100 });
          return { count: 1, files: ['C:\\MIA\\result.xlsx'] };
        },
      },
    } });
  });
  await page.goto('/');
  const exportButton = page.getByRole('button', { name: 'Tải kết quả tất cả' });
  await expect(exportButton).toBeEnabled();
  await exportButton.click();
  const progressButton = page.locator('.invoice-export-all-button');
  await expect(progressButton).toContainText('1/1');
  await expect(progressButton).toContainText('43%');
  await expect(progressButton).toHaveAttribute('aria-valuenow', '43');
  await expect(page.locator('.invoice-export-all-wrap .result-export-progress')).toHaveCount(0);
  await expect(page.locator('.stop-button .stop-button-icon')).toHaveCount(1);
});

test('artifact default frames use direct Figma exports as visual baselines', async ({ page }) => {
  await page.goto('/?demo=1');
  await page.evaluate(() => document.fonts.ready);
  await page.getByRole('button', { name: 'XML' }).click();
  await expect(await page.screenshot({ animations: 'disabled' })).toMatchSnapshot('figma-xml-1500x1024.png', { maxDiffPixelRatio: 0.01, threshold: 0.25 });
  await page.getByRole('button', { name: 'HTML', exact: true }).click();
  await expect(await page.screenshot({ animations: 'disabled' })).toMatchSnapshot('figma-html-1500x1024.png', { maxDiffPixelRatio: 0.01, threshold: 0.25 });
  await page.getByRole('button', { name: 'PDF', exact: true }).click();
  await expect(await page.screenshot({ animations: 'disabled' })).toMatchSnapshot('figma-pdf-1500x1024.png', { maxDiffPixelRatio: 0.01, threshold: 0.25 });
  await page.getByRole('button', { name: 'Tải HTML hàng loạt' }).click();
  await expect(await page.screenshot({ animations: 'disabled' })).toMatchSnapshot('figma-pdf-progress-1500x1024.png', { maxDiffPixelRatio: 0.01, threshold: 0.25 });
});

test('PDF tab validates folder and exposes converting/cancel states', async ({ page }) => {
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'PDF', exact: true }).click();
  await expect(page.locator('[data-node-id="106:18856"]')).toBeVisible();
  const folder = page.getByLabel('Thư mục lưu trữ');
  await folder.fill('');
  await expect(page.getByRole('button', { name: 'Tải HTML hàng loạt' })).toBeDisabled();
  await folder.fill('D:\\MIA\\PDF');
  await page.getByRole('button', { name: 'Tải HTML hàng loạt' }).click();
  await expect(page.locator('[data-node-id="106:19444"]')).toBeVisible();
  await expect(page.getByRole('status')).toContainText('31%');
  await page.getByRole('button', { name: 'Dừng lại' }).click();
  await expect(page.locator('[data-node-id="106:18856"]')).toBeVisible();
});

for (const width of [1024, 1280, 1366, 1440, 1500, 1600]) {
  test(`artifact layout remains usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1024 });
    await page.goto('/?demo=1');
    await page.getByRole('button', { name: 'XML' }).click();
    await expect(page.locator('.artifact-page')).toBeVisible();
    await expect(page.locator('.artifact-page')).toHaveJSProperty('scrollWidth', width - 200);
  });
}

test('unified XML HTML navigation reads overview rows and paginates by fifty', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_xml_html', username: '0100000000', company_name: 'Công ty XML HTML', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const rows = Array.from({ length: 51 }, (_, index) => ({
      row_id: index + 1, direction: 'purchase', fields: {
        tdlap: '20/08/2026', khmshdon: '1', khhdon: 'AA/26E', shdon: String(index + 1),
        nbmst: `010000${String(index).padStart(4, '0')}`, nbten: index === 0 ? `Đối tác ${'rất dài '.repeat(30)}` : `Đối tác ${index + 1}`,
        tgtttbso: index * 1000, tthai: 'Hóa đơn mới',
      },
    }));
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], get: async () => account, create: async () => account, reconnect: async () => account, revoke: async () => undefined },
      jobs: { resume: async () => null, resumeAll: async () => [], latestAll: async () => [], start: async () => ({}), status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      results: { overview: async ({ cursor }: { cursor?: string | null }) => ({ items: cursor ? rows.slice(50) : rows.slice(0, 50), total_count: 51, pagination: { limit: 50, has_more: !cursor, next_cursor: cursor ? null : 'page-2' } }), details: async () => ({ items: [], total_count: 0, pagination: { limit: 50, has_more: false, next_cursor: null } }) },
      artifacts: { targets: async () => ({ keys: [], total: 0 }), cancel: async () => ({ cancelled: true }), list: async () => ({ items: [], pagination: { limit: 200, has_more: false, next_cursor: null } }), export: async () => ({ count: 0, files: [] }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true, onInvoiceProgress: () => () => undefined, onExportProgress: () => () => undefined },
    } });
  });
  await page.goto('/');
  const xmlHtmlNav = page.getByRole('button', { name: 'XML/HTML', exact: true });
  await xmlHtmlNav.click();
  await expect(xmlHtmlNav).toHaveAttribute('data-active', 'true');
  await expect(page.locator('.nav-button[data-active="true"]')).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'HTML', exact: true })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'XML/HTML' })).toBeVisible();
  await expect(page.getByText('Tra cứu XML/HTML các hóa đơn đã/chưa đồng bộ')).toBeVisible();
  await expect(page.locator('.xml-html-company')).toHaveCSS('font-size', '12px');
  await expect(page.locator('.xml-html-row:not(.xml-html-row--head)')).toHaveCount(50);
  const grids = await page.locator('.xml-html-row').evaluateAll((rows) => rows.slice(0, 3).map((row) => getComputedStyle(row).gridTemplateColumns));
  expect(new Set(grids).size).toBe(1);
  const columnEdges = await page.locator('.xml-html-row').evaluateAll((rows) => rows.slice(0, 3).map((row) => Array.from(row.children).map((cell) => {
    const box = cell.getBoundingClientRect(); return [Math.round(box.x), Math.round(box.width)];
  })));
  expect(columnEdges[1]).toEqual(columnEdges[0]);
  expect(columnEdges[2]).toEqual(columnEdges[0]);
  await expect(page.getByText('Tổng 51 hàng · tối đa 50 hàng/trang')).toBeVisible();
  const download = page.getByRole('button', { name: 'Tải xuống kết quả' });
  await expect(download).toHaveCSS('background-color', 'rgb(37, 99, 184)');
  await expect(download).toHaveCSS('color', 'rgb(255, 255, 255)');
  await download.hover();
  await expect(download).toHaveCSS('color', 'rgb(255, 255, 255)');
  await page.mouse.down();
  await expect(download).toHaveCSS('color', 'rgb(255, 255, 255)');
  await page.mouse.move(0, 0);
  await page.mouse.up();
  await page.getByRole('button', { name: 'Trang sau' }).click();
  await expect(page.locator('.xml-html-row:not(.xml-html-row--head)')).toHaveCount(1);
  await page.getByRole('button', { name: 'PDF', exact: true }).click();
  await expect(page.getByRole('button', { name: 'PDF', exact: true })).toHaveAttribute('data-active', 'true');
  await expect(page.locator('.nav-button[data-active="true"]')).toHaveCount(1);
});

test('XML HTML empty data requires explicit overview synchronization', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_sync', username: '0100000000', company_name: 'CÔNG TY TNHH HỒNG TRÀ NGỌC GIA', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    let synced = false;
    const starts: unknown[] = [];
    Object.defineProperty(window, 'xmlHtmlSyncStarts', { value: starts });
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], get: async () => account, create: async () => account, reconnect: async () => account, revoke: async () => undefined },
      jobs: {
        resume: async () => null, resumeAll: async () => [], latestAll: async () => [],
        start: async (intent: unknown) => { starts.push(intent); synced = true; return { record: { job_id: 'job_sync', connection_id: account.connection_id, intent, idempotency_key: 'sync', created_at: 'now', updated_at: 'now', status: 'queued' }, accepted: { job_id: 'job_sync', status: 'queued', current_stage: null } }; },
        status: async () => ({ job_id: 'job_sync', status: 'completed', stage: null, overall_percent: 100, current_month: null, updated_at: 'now', error: null }),
        summary: async () => ({ job_id: 'job_sync', status: 'completed', warning_count: 0, stages: [], coverage_plan: {}, work: {}, post_processing: {} }), cancel: async () => ({}), clear: async () => undefined,
      },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      results: { overview: async () => ({ items: synced ? [{ row_id: 1, direction: 'purchase', fields: { tdlap: '20/08/2026', khmshdon: '1', khhdon: 'AA/26E', shdon: '1', nbmst: '0101', nbten: 'Đối tác', tgtttbso: 1000, tthai: 'Hóa đơn mới' } }] : [], total_count: synced ? 1 : 0, pagination: { limit: 50, has_more: false, next_cursor: null } }), details: async () => ({ items: [], total_count: 0, pagination: { limit: 50, has_more: false, next_cursor: null } }) },
      artifacts: { targets: async () => ({ keys: [], total: 0 }), cancel: async () => ({ cancelled: true }), list: async () => ({ items: [], pagination: { limit: 200, has_more: false, next_cursor: null } }), export: async () => ({ count: 0, files: [] }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true, onInvoiceProgress: () => () => undefined, onExportProgress: () => () => undefined },
    } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'XML/HTML', exact: true }).click();
  await expect(page.getByText('Không tồn tại hóa đơn trong thời gian này.')).toBeVisible();
  await page.getByRole('button', { name: 'Tải xuống kết quả' }).click();
  await expect(page.getByRole('alertdialog')).toContainText('Chưa có dữ liệu hóa đơn trong khoảng thời gian này. Vui lòng Đồng bộ dữ liệu trước.');
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await expect(page.locator('.xml-html-row:not(.xml-html-row--head)')).toHaveCount(1, { timeout: 10_000 });
  const starts = await page.evaluate(() => (window as typeof window & { xmlHtmlSyncStarts: Array<Record<string, unknown>> }).xmlHtmlSyncStarts);
  expect(starts).toHaveLength(1);
  expect(starts[0]).toMatchObject({ connection_id: 'conn_sync', scopes: ['overview'], data_types: ['invoice'] });
});

test('XML HTML source outcomes update counters and row substates live', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_progress', username: '0100000000', company_name: 'Công ty Progress', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const rows = ['1', '2'].map((number, index) => ({
      row_id: index + 1, direction: 'purchase', fields: {
        tdlap: '20/08/2026', khmshdon: '1', khhdon: 'AA/26E', shdon: number,
        nbmst: '0101', nbten: `Đối tác ${number}`, tgtttbso: 1000, tthai: 'Hóa đơn mới',
      },
    }));
    const keys = rows.map((row) => `purchase|query|${row.fields.nbmst}|${row.fields.khhdon}|${row.fields.shdon}|${row.fields.khmshdon}`);
    let artifactListener: ((value: Record<string, unknown>) => void) | null = null;
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], get: async () => account, create: async () => account, reconnect: async () => account, revoke: async () => undefined },
      jobs: {
        resume: async () => null, resumeAll: async () => [], latestAll: async () => [],
        start: async (intent: unknown) => ({ record: { job_id: 'job_progress', connection_id: account.connection_id, intent, idempotency_key: 'progress', created_at: 'now', updated_at: 'now', status: 'queued' }, accepted: { job_id: 'job_progress', status: 'queued', current_stage: null } }),
        status: async () => ({ status: 'queued' }), summary: async () => ({}), cancel: async () => ({ status: 'cancelled' }), clear: async () => undefined,
      },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      results: { overview: async () => ({ items: rows, total_count: 2, pagination: { limit: 50, has_more: false, next_cursor: null } }), details: async () => ({ items: [], total_count: 0, pagination: { limit: 50, has_more: false, next_cursor: null } }) },
      artifacts: {
        targets: async () => ({ keys, total: keys.length }), export: async () => {
          let processed = 0;
          const emit = (artifactKey: string, kind: 'xml' | 'html', state: 'running' | 'completed') => artifactListener?.({
            status: state, processed, total: 4,
            percent: processed / 4 * 100, artifact_key: artifactKey, kind,
          });
          for (const key of keys) {
            emit(key, 'xml', 'running'); emit(key, 'html', 'running');
            await new Promise((resolve) => setTimeout(resolve, 100));
            processed += 1; emit(key, 'xml', 'completed');
            processed += 1; emit(key, 'html', 'completed');
          }
          return { count: 4, files: ['1.xml', '1.html', '2.xml', '2.html'] };
        }, cancel: async () => ({ cancelled: true }),
        list: async () => ({ items: [], pagination: { limit: 200, has_more: false, next_cursor: null } }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
        onInvoiceProgress: (listener: (value: Record<string, unknown>) => void) => { artifactListener = listener; return () => { artifactListener = null; }; }, onExportProgress: () => () => undefined,
      },
    } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'XML/HTML', exact: true }).click();
  await expect(page.locator('.xml-html-row:not(.xml-html-row--head)')).toHaveCount(2);
  await page.getByRole('button', { name: 'Tải xuống kết quả' }).click();

  await expect(page.locator('.xml-html-progress small')).toHaveText('XML 0/2 · HTML 0/2');
  await expect(page.locator('[data-progress="Đang tải"]')).toHaveCount(1);
  await expect(page.locator('[data-progress="Chưa xử lý"]')).toHaveCount(1);

  await expect(page.locator('.xml-html-progress small')).toHaveText('XML 1/2 · HTML 1/2', { timeout: 5_000 });
  await expect(page.locator('[data-progress="Hoàn tất"]')).toHaveCount(1);
  await expect(page.locator('[data-progress="Đang tải"]')).toHaveCount(1);

  await expect(page.locator('[data-progress="Hoàn tất"]')).toHaveCount(2, { timeout: 5_000 });
});
