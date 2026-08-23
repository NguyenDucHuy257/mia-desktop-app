import { expect, test } from '@playwright/test';

const account = {
  connection_id: 'conn_download', username: '0100000000', company_name: 'Công ty Mẫu',
  status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false,
};

test('unified artifact screen uses local coverage and starts one multi-format batch', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    const snapshots: unknown[] = [];
    const starts: unknown[] = [];
    let statusReads = 0;
    Object.defineProperty(window, 'artifactCalls', { value: { snapshots, starts } });
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 7, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        snapshot: async (request: unknown) => { snapshots.push(request); return { ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 12, cached: { xml: 4, html: 3, pdf: 2 } }] }; },
        startBatch: async (request: unknown) => { starts.push(request); return { task_id: 'artifact-1', status: 'running' }; },
        batchStatus: async () => {
          statusReads += 1;
          const completed = statusReads > 1;
          return {
            task_id: 'artifact-1', status: completed ? 'completed' : 'running', current_account_id: accountValue.connection_id,
            accounts: { [accountValue.connection_id]: { connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 12, cached: { xml: completed ? 12 : 6, html: completed ? 12 : 5, pdf: 2 }, status: completed ? 'completed' : 'downloading' } },
            formats: {
              xml: { status: completed ? 'completed' : 'running', processed: completed ? 12 : 6, total: 12, percent: completed ? 100 : 50, current_invoice: 'AA/1', failed: 0 },
              html: { status: completed ? 'completed' : 'running', processed: completed ? 12 : 5, total: 12, percent: completed ? 100 : 42, current_invoice: 'AA/1', failed: 0 },
            }, warning_count: 0,
          };
        },
        cancelBatch: async () => ({ cancelled: true }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });

  await page.goto('/', { waitUntil: 'commit' });
  const nav = page.getByRole('button', { name: 'XML/HTML/PDF', exact: true });
  await nav.click();
  await expect(nav).toHaveAttribute('data-active', 'true');
  await expect(page.getByRole('heading', { name: 'XML/HTML/PDF' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'PDF', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Thêm tài khoản' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Đồng bộ dữ liệu' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Xóa tài khoản/ })).toHaveCount(0);
  await expect(page.getByText('Công ty Mẫu')).toBeVisible();
  await expect(page.locator('body')).not.toContainText(/artifact/i);
  await expect(page.getByRole('button', { name: 'Mở thư mục' })).toHaveCount(0);
  await expect(page.locator('.artifact-quantity')).toContainText('XML 4/12');
  await expect(page.locator('.artifact-quantity')).toContainText('HTML 3/12');
  await expect(page.locator('.artifact-quantity')).toContainText('PDF 2/12');
  await expect(page.getByLabel('Định dạng tải xuống').locator('label').filter({ hasText: 'XML' })).toHaveAttribute('data-active', 'true');
  await expect(page.getByLabel('Định dạng tải xuống').locator('label').filter({ hasText: 'HTML' })).toHaveAttribute('data-active', 'true');
  await expect(page.getByLabel('Định dạng tải xuống').locator('label').filter({ hasText: 'PDF' })).toHaveAttribute('data-active', 'false');
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('unified-artifact-account-1500x1024.png', {
    animations: 'disabled', maxDiffPixelRatio: 0.02, threshold: 0.25,
  });
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect(page.locator('.artifact-progress-card')).toHaveCount(2);
  await expect(page.getByText('Đã hoàn thành tải XML/HTML/PDF.')).toBeVisible({ timeout: 4_000 });

  const calls = await page.evaluate(() => (window as typeof window & { artifactCalls: { snapshots: unknown[]; starts: Array<Record<string, unknown>> } }).artifactCalls);
  expect(calls.snapshots.length).toBeGreaterThanOrEqual(1);
  expect(calls.starts).toHaveLength(1);
  expect(calls.starts[0]).toMatchObject({ connection_ids: ['conn_download'], directions: ['purchase', 'sold'], kinds: ['xml', 'html'], pdf_concurrency: 7 });
});

test('directions allow an empty selection and validate only when downloading', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        snapshot: async (request: unknown) => ({ ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 1, cached: { xml: 1, html: 1, pdf: 0 } }] }),
        startBatch: async () => ({ task_id: 'unused', status: 'running' }), batchStatus: async () => ({}), cancelBatch: async () => ({ cancelled: true }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });
  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  const accountButton = page.locator('.artifact-account-body .selection-button').first();
  if (await accountButton.locator('.selection-box').getAttribute('data-checked') !== 'true') await accountButton.click();
  const directions = page.getByLabel('Loại hóa đơn');
  await directions.getByText('Mua vào').click();
  await directions.getByText('Bán ra').click();
  await expect(directions.locator('input:checked')).toHaveCount(0);
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Tải xuống', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect(page.getByRole('alertdialog')).toContainText('Vui lòng chọn ít nhất Mua vào hoặc Bán ra.');
});

test('missing local coverage blocks downloads without starting an invoice sync job', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    const jobStarts: unknown[] = [];
    const batchStarts: unknown[] = [];
    Object.defineProperty(window, 'forbiddenStarts', { value: { jobStarts, batchStarts } });
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], start: async (value: unknown) => { jobStarts.push(value); }, status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        snapshot: async (request: unknown) => ({ ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: false, missing_ranges: [{ date_from: '2023-10-12', date_to: '2023-10-20' }], total: 4, cached: { xml: 0, html: 0, pdf: 0 } }] }),
        startBatch: async (value: unknown) => { batchStarts.push(value); return { task_id: 'forbidden', status: 'running' }; },
        batchStatus: async () => ({}), cancelBatch: async () => ({ cancelled: true }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });

  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  await expect(page.locator('.artifact-account-status')).toContainText('Chưa đồng bộ');
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect(page.getByRole('alertdialog')).toContainText('chưa được đồng bộ đầy đủ');
  const starts = await page.evaluate(() => (window as typeof window & { forbiddenStarts: { jobStarts: unknown[]; batchStarts: unknown[] } }).forbiddenStarts);
  expect(starts.jobStarts).toHaveLength(0);
  expect(starts.batchStarts).toHaveLength(0);
});

test('PDF can be selected alone and per-format cancellation stays independent', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    const starts: unknown[] = [];
    const cancellations: unknown[] = [];
    Object.defineProperty(window, 'pdfOnlyCalls', { value: { starts, cancellations } });
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 100, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        snapshot: async (request: unknown) => ({ ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 8, cached: { xml: 8, html: 8, pdf: 0 } }] }),
        startBatch: async (request: unknown) => { starts.push(request); return { task_id: 'pdf-only', status: 'running' }; },
        batchStatus: async () => ({ task_id: 'pdf-only', status: 'running', current_account_id: accountValue.connection_id, accounts: { [accountValue.connection_id]: { connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 8, cached: { xml: 8, html: 8, pdf: 1 }, status: 'downloading' } }, formats: { pdf: { status: 'running', processed: 1, total: 8, percent: 12.5, current_invoice: '1', failed: 0 } }, warning_count: 0 }),
        cancelBatch: async (request: unknown) => { cancellations.push(request); return { cancelled: true }; }, selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });

  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  const formats = page.getByLabel('Định dạng tải xuống');
  await formats.getByText('XML').click();
  await formats.getByText('HTML').click();
  await formats.getByText('PDF').click();
  await expect(page.getByRole('button', { name: 'Tải xuống', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect(page.locator('.artifact-progress-card[data-kind="pdf"]')).toBeVisible();
  await page.locator('.artifact-progress-card[data-kind="pdf"]').getByRole('button', { name: 'Dừng' }).click();
  const calls = await page.evaluate(() => (window as typeof window & { pdfOnlyCalls: { starts: Array<Record<string, unknown>>; cancellations: unknown[] } }).pdfOnlyCalls);
  expect(calls.starts[0]).toMatchObject({ kinds: ['pdf'], pdf_concurrency: 100 });
  expect(calls.cancellations).toEqual([{ kind: 'pdf' }]);
});

test('artifact date range and account selection transfer back to invoice management', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        snapshot: async (request: unknown) => ({ ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 1, cached: { xml: 1, html: 1, pdf: 0 } }] }),
        startBatch: async () => ({ task_id: 'unused', status: 'running' }), batchStatus: async () => ({}), cancelBatch: async () => ({ cancelled: true }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });
  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  await page.getByRole('button', { name: /KHOẢNG THỜI GIAN/ }).click();
  await page.getByLabel('Từ ngày nhập tay').fill('01/08/2026');
  await page.getByLabel('Đến ngày nhập tay').fill('23/08/2026');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await page.getByRole('button', { name: 'Quản lý HDDT', exact: true }).click();
  await page.getByRole('button', { name: /KHOẢNG THỜI GIAN/ }).click();
  await expect(page.getByLabel('Từ ngày đồng bộ nhập tay')).toHaveValue('01/08/2026');
  await expect(page.getByLabel('Đến ngày đồng bộ nhập tay')).toHaveValue('23/08/2026');
  await expect(page.getByRole('button', { name: 'Chọn 0100000000' }).locator('.selection-box')).toHaveAttribute('data-checked', 'true');
});

for (const width of [1024, 1280, 1500, 1600]) {
  test(`unified artifact account layout remains usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/?demo=1', { waitUntil: 'commit' });
    await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
    await expect(page.locator('.artifact-account-page')).toBeVisible();
    const dimensions = await page.locator('.artifact-account-page').evaluate((node) => ({ scrollWidth: node.scrollWidth, clientWidth: node.clientWidth }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  });
}
