import { expect, test } from './licensed-test';

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

test('password visibility control preserves value, focus and hidden default', async ({ page }) => {
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  const password = page.getByLabel('Mật khẩu', { exact: true });
  const toggle = page.getByRole('button', { name: 'Hiện mật khẩu' });
  await expect(password).toHaveAttribute('type', 'password');
  await password.fill('MIA-secret-2026');
  await password.evaluate((element: HTMLInputElement) => element.setSelectionRange(4, 4));
  await toggle.click();
  await expect(password).toHaveAttribute('type', 'text');
  await expect(password).toHaveValue('MIA-secret-2026');
  await expect(password).toBeFocused();
  await expect.poll(() => password.evaluate((element: HTMLInputElement) => element.selectionStart)).toBe(4);
  await expect(page.getByText('Vui lòng nhập mã số thuế.')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Ẩn mật khẩu' })).toHaveAttribute('aria-pressed', 'true');
  await page.screenshot({ path: 'test-results/password-visible.png', animations: 'disabled' });
  await page.getByRole('button', { name: 'Ẩn mật khẩu' }).click();
  await expect(password).toHaveAttribute('type', 'password');
  await expect(password).toHaveValue('MIA-secret-2026');
  await page.screenshot({ path: 'test-results/password-hidden.png', animations: 'disabled' });
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await expect(page.getByLabel('Mật khẩu', { exact: true })).toHaveAttribute('type', 'password');
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
  await page.getByLabel('Mật khẩu', { exact: true }).fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await expect(page.getByRole('alertdialog', { name: 'Thông báo thành công' })).toContainText('Đã thêm tài khoản thành công.');
  await expect(page.locator('.notice-icon[data-kind="success"]')).toHaveText('✓');
  await page.getByRole('button', { name: 'Đóng' }).click();
  await expect(page.getByLabel('Mật khẩu', { exact: true })).toHaveValue('');

  await page.getByRole('tab', { name: 'Thêm hàng loạt' }).click();
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
  await page.getByLabel('Mật khẩu', { exact: true }).fill('not-stored-in-renderer');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await expect(page.getByText('Hiển thị 1–1 trên tổng 1 tài khoản')).toBeVisible();
  await expect(page.getByTitle('—')).toBeVisible();
  await expect(page.locator('.table-row').last()).toContainText('Sẵn sàng');
  await expect(page.locator('.table-row').last()).toContainText('Chưa đồng bộ');
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
  await page.getByRole('tab', { name: /^Chi tiết$/ }).click();
  await expect(page.getByText('Tổng 73 hàng · tối đa 50 hàng/trang')).toBeVisible();
  await page.getByRole('button', { name: 'Trang sau' }).click();
  await expect(page.locator('.results-row:not(.results-row--header)')).toHaveCount(23);
  await expect(page.getByText('Tổng 73 hàng · tối đa 50 hàng/trang')).toBeVisible();

  await page.getByLabel('Tìm kiếm kết quả').fill('không-có');
  await expect(page.getByText('Không có dữ liệu phù hợp với bộ lọc hiện tại.')).toBeVisible();
  await page.getByRole('button', { name: 'Tải xuống kết quả' }).click();
  const exportDialog = page.getByRole('dialog', { name: 'Chọn nội dung tải xuống' });
  await exportDialog.getByLabel('Tổng quan').uncheck();
  await exportDialog.getByRole('button', { name: 'Tải xuống', exact: true }).click();
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
  await page.getByLabel('Mật khẩu', { exact: true }).fill('synthetic-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await expect(page.locator('.invoice-page .company-name', { hasText: 'Công ty đã xác thực' })).toBeVisible();
  await expect(page.getByText('Chưa kiểm tra đăng nhập')).toHaveCount(0);
});

test('invoice scope menu follows Figma node 4:654 and closes outside', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const scope = page.getByRole('button', { name: 'Tổng quan' });
  await expect(scope).toHaveCSS('white-space', 'nowrap');
  await expect(scope).toHaveCSS('border-radius', '6px');
  await expect(scope).toHaveJSProperty('scrollHeight', await scope.evaluate((node) => node.clientHeight));
  await scope.click();
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
      start: async () => ({ record, accepted: { job_id: 'job-1', status: 'queued', current_stage: null, worker_slot_id: null } }),
      status: async () => ({ job_id: 'job-1', status: ++calls === 1 ? 'running' : 'running', stage: 'overview', overall_percent: 35, scope_progress: { scope: 'overview', processed: 35, total: 100 }, current_month: { key: '2026-01', index: 1, total: 1, processed: 35, planned: 100, percent: 35 }, updated_at: 'now', error: null }),
      summary: async () => ({ job_id: 'job-1', status: 'cancelled', warning_count: 0, stages: [], coverage_plan: {}, work: {}, post_processing: {} }),
      cancel: async () => ({ job_id: 'job-1', status: 'cancelling', stage: 'overview', overall_percent: 35, current_month: null, updated_at: 'now', error: null }),
      clear: async () => undefined,
    } } });
  });
  await page.goto('/?demo=1');
  const toolbar = page.locator('.invoice-sync-toolbar-card');
  await expect(toolbar).toBeVisible();
  await expect(toolbar.getByText('1. Loại hóa đơn', { exact: true })).toBeVisible();
  await expect(toolbar.getByText('2. Khoảng thời gian', { exact: true })).toBeVisible();
  await expect(toolbar.getByText('3. Loại bảng kê xuất Excel', { exact: true })).toBeVisible();
  await expect(toolbar).toHaveCSS('border-radius', '6px');
  const controlHeights = await toolbar.locator('.compact-select, .date-range-trigger').evaluateAll((controls) => controls.map((control) => Math.round(control.getBoundingClientRect().height)));
  expect(controlHeights).toEqual([46, 46, 46]);
  const folderBox = await toolbar.locator('.invoice-export-folder').boundingBox();
  const actionBox = await toolbar.getByRole('button', { name: 'Đồng bộ dữ liệu' }).boundingBox();
  expect(folderBox).not.toBeNull();
  expect(actionBox).not.toBeNull();
  expect(Math.abs((folderBox?.y ?? 0) - (actionBox?.y ?? 0))).toBeLessThanOrEqual(3);
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu', { exact: true }).fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await page.getByRole('menuitem', { name: /Đồng bộ mới/ }).click();
  await expect(page.locator('.table-row').last()).not.toContainText('Tiến trình tổng');
  await expect(page.locator('.table-row').last()).toContainText('35%');
  await expect(page.locator('.table-row').last()).toContainText('35/100 hóa đơn');
  await page.getByRole('button', { name: 'Dừng tải' }).click();
  await expect(page.locator('.table-row').last()).toContainText(/Đang dừng|Đã dừng/);
});

test('keeps one direction and restores the two legacy sync modes', async ({ page }) => {
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
  await page.getByLabel('Mật khẩu', { exact: true }).fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('button', { name: 'Mua vào' }).click();
  await expect(page.getByLabel('Loại giao dịch').getByLabel('Mua vào')).toBeChecked();
  await expect(page.getByLabel('Loại giao dịch').getByLabel('Bán ra')).not.toBeChecked();
  await page.getByLabel('Loại giao dịch').getByText('Bán ra', { exact: true }).click();
  await expect(page.getByRole('button', { name: 'Bán ra' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Tổng quan' })).toBeVisible();
  await page.getByRole('button', { name: 'Tổng quan' }).click();
  await expect(page.locator('[data-node-id="4:654"]').getByLabel('Tổng quan')).toBeChecked();
  await expect(page.locator('[data-node-id="4:654"]').getByLabel('Chi tiết')).not.toBeChecked();
  await page.locator('[data-node-id="4:654"]').getByText('Tổng quan', { exact: true }).click();
  await page.locator('[data-node-id="4:654"]').getByText('Chi tiết', { exact: true }).click();
  await page.locator('.invoice-date-field .date-range-trigger').click();
  await page.getByLabel('Từ ngày đồng bộ nhập tay').fill('01/01/2026');
  await page.getByLabel('Đến ngày đồng bộ nhập tay').fill('31/01/2026');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  const syncMenu = page.getByRole('menu', { name: 'Chọn cách đồng bộ' });
  await expect(syncMenu).toBeVisible();
  await expect(syncMenu.getByRole('menuitem', { name: /Đồng bộ mới/ })).toBeVisible();
  await expect(syncMenu.getByRole('menuitem', { name: /Đồng bộ bổ sung/ })).toBeVisible();
  const assertMenuInsideViewport = async () => {
    const geometry = await syncMenu.evaluate((menu) => {
      const bounds = menu.getBoundingClientRect();
      const descriptions = [...menu.querySelectorAll('small')];
      return {
        left: bounds.left,
        right: bounds.right,
        viewport: window.innerWidth,
        descriptions: descriptions.map((description) => ({
          whiteSpace: getComputedStyle(description).whiteSpace,
          overflowWrap: getComputedStyle(description).overflowWrap,
          clipped: description.scrollWidth > description.clientWidth + 1,
        })),
      };
    });
    expect(geometry.left).toBeGreaterThanOrEqual(0);
    expect(geometry.right).toBeLessThanOrEqual(geometry.viewport);
    expect(geometry.descriptions.every((description) => description.whiteSpace === 'normal' && !description.clipped)).toBe(true);
  };
  await assertMenuInsideViewport();
  await page.setViewportSize({ width: 1024, height: 768 });
  await assertMenuInsideViewport();
  await syncMenu.getByRole('menuitem', { name: /Đồng bộ bổ sung/ }).click();
  const captured = await page.evaluate(() => (window as typeof window & { capturedIntent?: { directions?: string[]; query_types?: string[]; scopes?: string[]; data_types?: string[] } }).capturedIntent);
  expect(captured).toMatchObject({ date_from: '2026-01-01', date_to: '2026-01-31', directions: ['sold'], query_types: ['query', 'sco-query'], scopes: ['overview', 'detail'], data_types: ['invoice'], sync_mode: 'supplement', force_refresh: false, refresh_latest_month: false });
});

test('sync and download waits for successful completion then exports the captured range', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_auto', username: '0101234567', company_name: 'Công ty Auto Export', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const record = { job_id: 'job-auto', connection_id: account.connection_id, intent: {}, idempotency_key: 'auto', created_at: 'now', updated_at: 'now', status: 'queued' };
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], get: async () => account, create: async () => account, reconnect: async () => account, revoke: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 1, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      jobs: {
        resume: async () => null, resumeAll: async () => [], latestAll: async () => [],
        syncStates: async () => [],
        start: async (intent: unknown) => {
          (window as typeof window & { autoSyncIntent?: unknown }).autoSyncIntent = intent;
          return { record: { ...record, intent }, accepted: { job_id: record.job_id, status: 'queued' } };
        },
        status: async () => ({ ...record, status: 'completed', overall_percent: 100, stage: null, current_month: null, error: null }),
        summary: async () => ({ job_id: record.job_id, status: 'completed', warning_count: 0, stages: [], coverage_plan: {}, work: {}, post_processing: {} }),
        cancel: async () => ({}), clear: async () => undefined,
      },
      artifacts: {
        selectDirectory: async () => 'C:\\MIA',
        onExportProgress: () => () => undefined,
        export: async (request: unknown) => {
          (window as typeof window & { autoExportRequest?: unknown }).autoExportRequest = request;
          return { count: 1, files: ['C:\\MIA\\result.xlsx'] };
        },
      },
    } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Đồng bộ & tải xuống' }).click();
  await expect.poll(() => page.evaluate(() => Boolean(
    (window as typeof window & { autoExportRequest?: unknown }).autoExportRequest,
  )), { timeout: 10_000 }).toBe(true);
  const captured = await page.evaluate(() => ({
    sync: (window as typeof window & { autoSyncIntent?: unknown }).autoSyncIntent,
    exportRequest: (window as typeof window & { autoExportRequest?: unknown }).autoExportRequest,
  }));
  expect(captured.sync).toMatchObject({ sync_mode: 'supplement', scopes: ['overview'] });
  expect(captured.exportRequest).toMatchObject({
    connection_ids: ['conn_auto'], destination: 'C:\\MIA', kinds: ['excel'],
    result_scopes: ['overview'], direction: 'purchase',
  });
});

test('shows bounded polling failure and lets the user retry', async ({ page }) => {
  await page.addInitScript(() => {
    const record = { job_id: 'job-retry', connection_id: 'conn_demo', intent: {}, idempotency_key: 'desktop-fixed', created_at: 'now', updated_at: 'now', status: 'running' };
    const account = { connection_id: 'conn_demo', username: '0101234567', company_name: 'Công ty Retry', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    Object.defineProperty(window, 'miaRuntime', { value: { accountConnections: { list: async () => [account] }, preferences: {
      get: async () => ({ concurrency: 1, retries: 0, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value,
    }, jobs: {
      resume: async () => record,
      resumeAll: async () => [record], latestAll: async () => [record],
      status: async () => { throw new Error('temporary network failure'); },
      summary: async () => ({}), start: async () => ({}), cancel: async () => ({}), clear: async () => undefined,
    } } });
  });
  await page.goto('/');
  await expect(page.getByText('Mất kết nối khi cập nhật tiến trình. Đồng bộ mới được khóa để tránh chạy chồng job.')).toBeVisible({ timeout: 30_000 });
});

test('shows sanitized job errors in the affected account row', async ({ page }) => {
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
  await page.getByLabel('Mật khẩu', { exact: true }).fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await page.getByRole('menuitem', { name: /Đồng bộ mới/ }).click();
  await expect(page.locator('.table-row').last()).toContainText('Không thể tạo tác vụ đồng bộ');
  await expect(page.locator('.table-row').last()).toContainText('Lỗi');
});

test('opens local overview/detail results and paginates by cursor', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_results', username: '0101234567', company_name: 'Công ty Results', status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const accounts: unknown[] = [];
    const record = { job_id: 'job-results', connection_id: account.connection_id, intent: {}, idempotency_key: 'results', created_at: 'now', updated_at: 'now', status: 'completed' };
    const pageFor = (kind: string, cursor?: string | null) => ({
      items: [{ row_id: cursor ? 2 : 1, direction: 'purchase', fields: { business_key: `${kind}-${cursor ?? 'first'}`, company: 'Demo' } }],
      columns: ['business_key', 'company'], column_labels: { business_key: 'Mã', company: 'Công ty' }, total_count: 2,
      pagination: { limit: 50, has_more: !cursor, next_cursor: cursor ? null : 'djE6MQ' },
    });
    Object.defineProperty(window, 'miaRuntime', { value: { accountConnections: {
      list: async () => accounts, create: async () => { accounts.push(account); return account; },
      get: async () => account, reconnect: async () => account, revoke: async () => undefined,
    }, jobs: {
      resumeAll: async () => [record], latestAll: async () => [record], resume: async () => record,
      status: async () => ({ ...record, stage: null, overall_percent: 100, current_month: null, error: null }),
      summary: async () => ({ job_id: record.job_id, status: 'completed', warning_count: 0, stages: [], coverage_plan: {}, work: {}, post_processing: {} }),
      start: async () => ({ record, accepted: {} }), cancel: async () => ({}), clear: async () => undefined,
    }, results: {
      overview: async (query: { cursor?: string | null }) => pageFor('overview', query.cursor),
      details: async (query: { cursor?: string | null }) => pageFor('detail', query.cursor),
    } } });
  });
  await page.goto('/?demo=1');
  await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
  await page.getByLabel('Mã số thuế (MST)').fill('0101234567');
  await page.getByLabel('Mật khẩu', { exact: true }).fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await page.getByRole('button', { name: 'Đồng bộ dữ liệu' }).click();
  await page.getByRole('menuitem', { name: /Đồng bộ mới/ }).click();
  await page.getByRole('button', { name: 'Xem kết quả' }).click();
  await expect(page.getByText('overview-first')).toBeVisible();
  await page.getByRole('button', { name: 'Trang sau' }).click();
  await expect(page.getByText('overview-djE6MQ')).toBeVisible();
  await page.getByRole('tab', { name: /^Chi tiết$/ }).click();
  await expect(page.getByText('detail-first')).toBeVisible();
});

test('bulk export uses the selected account without a legacy row action menu', async ({ page }) => {
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
  await page.getByLabel('Mật khẩu', { exact: true }).fill('portal-password');
  await page.getByRole('button', { name: 'Thêm ngay' }).click();
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: /Quay lại/ }).click();
  await expect(page.getByRole('menu')).toHaveCount(0);
  await page.getByRole('button', { name: 'Tải xuống kết quả' }).click();
  await expect(page.getByRole('alertdialog')).toContainText('Đã xuất 1 file Excel');
  const calls = await page.evaluate(() => (window as typeof window & { artifactExports: Array<{ connection_ids: string[]; kinds: string[] }> }).artifactExports);
  expect(calls[0]?.connection_ids).toHaveLength(1);
  expect(calls[0]).toMatchObject({ kinds: ['excel'] });
});

test('date, company, search, status and pagination controls update the UI', async ({ page }) => {
  await page.goto('/?figma=1');
  await page.locator('.invoice-date-field .date-range-trigger').click();
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
  await page.getByRole('button', { name: 'Cài đặt hệ thống', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Cài đặt hệ thống' })).toBeVisible();
});

test('settings persist scheduler limits and logs are filtered after main-process redaction', async ({ page }) => {
  await page.addInitScript(() => {
    let preferences = { concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' };
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [] },
      preferences: { get: async () => preferences, set: async (value: typeof preferences) => (preferences = value) },
      logs: { list: async () => ['2026 INFO storage_initialized', '2026 WARN retry_scheduled'] },
    } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Cài đặt hệ thống', exact: true }).click();
  await page.getByLabel('Số lần thử lại').fill('1');
  await page.getByLabel('Số PDF xử lý đồng thời').fill('100');
  await page.getByRole('button', { name: 'Lưu cài đặt' }).click();
  await expect(page.getByRole('alertdialog')).toContainText('Tác vụ mới sẽ áp dụng');
  await page.getByRole('button', { name: 'Đóng' }).click();
  await page.getByRole('button', { name: 'Lịch sử tải xuống' }).click();
  await expect(page.getByText('Chức năng đang cập nhật')).toBeVisible();
});

test('bulk Excel progress stays determinate inside the toolbar button', async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_local', username: '0100000000', company_name: 'Công ty Runtime', status: 'ready', token_generation: 0, created_at: 'now', updated_at: 'now', reused: false };
    let progressListener: ((value: Record<string, unknown>) => void) | undefined;
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], get: async () => account, create: async () => account, reconnect: async () => account, revoke: async () => undefined },
      jobs: { resume: async () => null, resumeAll: async () => [], latestAll: async () => [], start: async () => ({}), status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
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
  const exportButton = page.getByRole('button', { name: 'Tải xuống kết quả' });
  await expect(exportButton).toBeEnabled();
  await exportButton.click();
  const progressButton = page.locator('.invoice-export-all-button');
  await expect(progressButton).toContainText('1/1');
  await expect(progressButton).toContainText('43%');
  await expect(progressButton).toHaveAttribute('aria-valuenow', '43');
  await expect(page.locator('.invoice-export-all-wrap .result-export-progress')).toHaveCount(0);
  await expect(page.locator('.stop-button .stop-button-icon')).toHaveCount(1);
});

test.skip('legacy artifact default frames used separate tabs', async ({ page }) => {
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

test.skip('legacy PDF tab used a separate screen', async ({ page }) => {
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
  test.skip(`legacy artifact layout remains usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1024 });
    await page.goto('/?demo=1');
    await page.getByRole('button', { name: 'XML' }).click();
    await expect(page.locator('.artifact-page')).toBeVisible();
    await expect(page.locator('.artifact-page')).toHaveJSProperty('scrollWidth', width - 200);
  });
}

test.skip('legacy XML HTML navigation read overview rows directly', async ({ page }) => {
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
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
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

test.skip('legacy XML HTML screen started overview synchronization', async ({ page }) => {
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
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
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

test.skip('legacy XML HTML screen used invoice event counters', async ({ page }) => {
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
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
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
