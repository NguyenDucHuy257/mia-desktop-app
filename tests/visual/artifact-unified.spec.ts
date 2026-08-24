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
  await expect(page.getByLabel('Tìm kiếm tài khoản tải xuống')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Quản lý HDDT', exact: true }).locator('img')).toHaveCSS('filter', 'grayscale(1) saturate(0) opacity(0.72)');
  await expect(page.locator('.artifact-toolbar-card')).toHaveCSS('border-radius', '6px');
  await expect(page.getByLabel('Tìm kiếm tài khoản tải xuống')).toHaveCSS('border-radius', '6px');
  await expect(page.locator('.artifact-direction-select .compact-select')).toHaveCSS('white-space', 'nowrap');
  await expect(page.locator('.artifact-direction-select .compact-select')).toHaveCSS('border-radius', '6px');
  await expect(page.locator('.artifact-account-table')).toHaveCSS('border-radius', '6px');
  const dateBox = await page.locator('.artifact-toolbar-card .date-range-trigger').boundingBox();
  const folderBox = await page.locator('.artifact-toolbar-card .invoice-export-folder').boundingBox();
  expect(dateBox).not.toBeNull();
  expect(folderBox).not.toBeNull();
  expect(Math.abs((dateBox?.x ?? 0) - (folderBox?.x ?? 0))).toBeLessThanOrEqual(1);
  await expect(page.getByRole('heading', { name: 'XML/HTML/PDF' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'PDF', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Thêm tài khoản' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Đồng bộ dữ liệu' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Xóa tài khoản/ })).toHaveCount(0);
  await expect(page.getByText('Công ty Mẫu')).toBeVisible();
  await expect(page.locator('body')).not.toContainText(/artifact/i);
  await expect(page.getByRole('button', { name: 'Mở thư mục' })).toHaveCount(0);
  const downloadButton = page.getByRole('button', { name: 'Tải xuống', exact: true });
  const stopButton = page.getByRole('button', { name: 'Dừng tải', exact: true });
  await expect(downloadButton).toHaveClass(/sync-button/);
  await expect(stopButton).toHaveClass(/stop-button/);
  await expect(downloadButton.locator('.invoice-action-icon')).toHaveCount(1);
  await expect(stopButton.locator('.stop-button-icon')).toHaveCount(1);
  await expect(downloadButton).toHaveCSS('border-radius', '6px');
  await expect(stopButton).toHaveCSS('border-radius', '6px');
  await expect(page.locator('.artifact-quantity')).toContainText('XML 4/12');
  await expect(page.locator('.artifact-quantity')).toContainText('HTML 3/12');
  await expect(page.locator('.artifact-quantity')).toContainText('PDF 2/12');
  await page.getByRole('button', { name: 'XML + HTML' }).click();
  await expect(page.getByLabel('Định dạng tải xuống').getByLabel('XML')).toBeChecked();
  await expect(page.getByLabel('Định dạng tải xuống').getByLabel('HTML')).toBeChecked();
  await expect(page.getByLabel('Định dạng tải xuống').getByLabel('PDF')).not.toBeChecked();
  await page.keyboard.press('Escape');
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('unified-artifact-account-1500x1024.png', {
    animations: 'disabled', maxDiffPixelRatio: 0.02, threshold: 0.25,
  });
  await downloadButton.click();
  await expect(page.locator('.artifact-progress-card')).toHaveCount(2);
  const progressLayout = await page.locator('.artifact-progress-cards').evaluate((container) => {
    const cards = [...container.querySelectorAll<HTMLElement>('.artifact-progress-card')];
    const bounds = container.getBoundingClientRect();
    return { containerWidth: bounds.width, cardWidths: cards.map((card) => card.getBoundingClientRect().width), cardHeights: cards.map((card) => card.getBoundingClientRect().height) };
  });
  expect(progressLayout.cardWidths).toHaveLength(2);
  expect(Math.abs(progressLayout.cardWidths[0] - progressLayout.cardWidths[1])).toBeLessThanOrEqual(1);
  expect(progressLayout.cardWidths.reduce((sum, width) => sum + width, 0)).toBeGreaterThan(progressLayout.containerWidth - 20);
  expect(Math.max(...progressLayout.cardHeights)).toBeLessThan(90);
  const threeColumnWidths = await page.locator('.artifact-progress-cards').evaluate((container) => {
    const cards = [...container.querySelectorAll<HTMLElement>('.artifact-progress-card')];
    const clone = cards[0].cloneNode(true) as HTMLElement;
    container.append(clone);
    container.setAttribute('data-count', '3');
    const widths = [...container.querySelectorAll<HTMLElement>('.artifact-progress-card')].map((card) => card.getBoundingClientRect().width);
    clone.remove();
    container.setAttribute('data-count', '2');
    return widths;
  });
  expect(threeColumnWidths).toHaveLength(3);
  expect(Math.max(...threeColumnWidths) - Math.min(...threeColumnWidths)).toBeLessThanOrEqual(1);
  await expect(page.locator('.artifact-progress-cards')).not.toContainText('Đang xử lý:');
  await expect(page.locator('.artifact-progress-cards')).not.toContainText('hóa đơn');
  await expect(page.getByText('Đã hoàn thành tải XML/HTML/PDF.')).toBeVisible({ timeout: 4_000 });

  const calls = await page.evaluate(() => (window as typeof window & { artifactCalls: { snapshots: unknown[]; starts: Array<Record<string, unknown>> } }).artifactCalls);
  expect(calls.snapshots.length).toBeGreaterThanOrEqual(1);
  expect(calls.starts).toHaveLength(1);
  expect(calls.starts[0]).toMatchObject({ connection_ids: ['conn_download'], directions: ['purchase'], kinds: ['xml', 'html'], pdf_concurrency: 7 });
});

test('direction selector always keeps exactly one direction', async ({ page }) => {
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
  await page.getByRole('button', { name: 'Mua vào' }).click();
  const directions = page.getByLabel('Loại hóa đơn');
  await expect(directions.getByLabel('Mua vào')).toBeChecked();
  await expect(directions.getByLabel('Bán ra')).not.toBeChecked();
  await directions.getByText('Bán ra', { exact: true }).click();
  await expect(page.getByRole('button', { name: 'Bán ra' })).toBeVisible();
  await page.getByRole('button', { name: 'Bán ra' }).click();
  await expect(page.getByLabel('Loại hóa đơn').getByLabel('Mua vào')).not.toBeChecked();
  await expect(page.getByLabel('Loại hóa đơn').getByLabel('Bán ra')).toBeChecked();
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
  await page.getByRole('button', { name: 'XML + HTML' }).click();
  const formats = page.getByLabel('Định dạng tải xuống');
  await formats.getByText('XML').click();
  await formats.getByText('HTML').click();
  await formats.getByText('PDF').click();
  await expect(page.getByRole('button', { name: 'Tải xuống', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect(page.locator('.artifact-progress-card[data-kind="pdf"]')).toBeVisible();
  const singleLayout = await page.locator('.artifact-progress-cards').evaluate((container) => ({
    container: container.getBoundingClientRect().width,
    card: container.querySelector<HTMLElement>('.artifact-progress-card')?.getBoundingClientRect().width ?? 0,
  }));
  expect(Math.abs(singleLayout.container - singleLayout.card)).toBeLessThanOrEqual(1);
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

test('latest direction coverage wins and remains deterministic across tab remounts', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    const requests: string[] = [];
    Object.defineProperty(window, 'coverageDirections', { value: requests });
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        coverage: async (request: { directions: string[] }) => {
          const direction = request.directions[0];
          requests.push(direction);
          await new Promise((resolve) => window.setTimeout(resolve, direction === 'purchase' ? 120 : 5));
          return { ...request, accounts: [{
            connection_id: accountValue.connection_id,
            ready: direction === 'purchase',
            missing_ranges: direction === 'purchase' ? [] : [{ date_from: '2026-04-01', date_to: '2026-05-31' }],
          }] };
        },
        snapshot: async (request: { directions: string[] }) => {
          const direction = request.directions[0];
          return { ...request, accounts: [{
            connection_id: accountValue.connection_id,
            ready: direction === 'purchase',
            missing_ranges: direction === 'purchase' ? [] : [{ date_from: '2026-04-01', date_to: '2026-05-31' }],
            total: direction === 'purchase' ? 40 : 20,
            cached: { xml: direction === 'purchase' ? 40 : 10, html: 0, pdf: 0 },
          }] };
        },
        startBatch: async () => ({ task_id: 'unused', status: 'running' }), batchStatus: async () => ({}), cancelBatch: async () => ({ cancelled: true }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });

  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  await page.getByRole('button', { name: 'Mua vào' }).click();
  await page.getByLabel('Loại hóa đơn').getByText('Bán ra', { exact: true }).click();
  await expect(page.locator('.artifact-account-status')).toContainText('01/04/2026 - 31/05/2026');
  await page.waitForTimeout(180);
  await expect(page.locator('.artifact-account-status')).toContainText('01/04/2026 - 31/05/2026');
  await expect(page.locator('.artifact-quantity')).toContainText('XML 10/20');

  for (let index = 0; index < 2; index += 1) {
    await page.getByRole('button', { name: 'Quản lý HDDT', exact: true }).click();
    await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Bán ra' })).toBeVisible();
    await expect(page.locator('.artifact-account-status')).toContainText('01/04/2026 - 31/05/2026');
  }

  await page.getByRole('button', { name: 'Bán ra' }).click();
  await page.getByLabel('Loại hóa đơn').getByText('Mua vào', { exact: true }).click();
  await expect(page.locator('.artifact-account-status')).toHaveText('Sẵn sàng tải');
  await expect(page.locator('.artifact-quantity')).toContainText('XML 40/40');
  const directions = await page.evaluate(() => (window as typeof window & { coverageDirections: string[] }).coverageDirections);
  expect(directions).toContain('purchase');
  expect(directions).toContain('sold');
  expect(directions.every((direction) => direction === 'purchase' || direction === 'sold')).toBe(true);
});

test('active source polling refreshes persisted coverage after every finalized month', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    let finalizedThrough = 1;
    const record = {
      job_id: 'source-monthly', connection_id: accountValue.connection_id,
      intent: {}, idempotency_key: 'monthly', created_at: 'now', updated_at: 'now', status: 'running',
    };
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: {
        resumeAll: async () => [record], latestAll: async () => [record],
        status: async () => {
          if (finalizedThrough === 1) await new Promise((resolve) => window.setTimeout(resolve, 1_500));
          finalizedThrough = Math.min(5, finalizedThrough + 1);
          return { job_id: record.job_id, status: finalizedThrough === 5 ? 'completed' : 'running', stage: 'overview', overall_percent: finalizedThrough * 20, current_month: { key: `2026-${String(finalizedThrough).padStart(2, '0')}`, processed: 1, planned: 1, percent: 100 }, updated_at: 'now', error: null };
        },
        summary: async () => ({}), start: async () => ({}), cancel: async () => ({}), clear: async () => undefined,
      },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        coverage: async (request: unknown) => {
          const nextMonth = finalizedThrough + 1;
          return { ...(request as object), accounts: [{
            connection_id: accountValue.connection_id,
            ready: finalizedThrough >= 5,
            missing_ranges: finalizedThrough >= 5 ? [] : [{ date_from: `2026-${String(nextMonth).padStart(2, '0')}-01`, date_to: '2026-05-31' }],
          }] };
        },
        snapshot: async (request: unknown) => {
          return { ...(request as object), accounts: [{
            connection_id: accountValue.connection_id,
            ready: false, missing_ranges: [],
            total: 10, cached: { xml: 0, html: 0, pdf: 0 },
          }] };
        },
        startBatch: async () => ({ task_id: 'unused', status: 'running' }), batchStatus: async () => ({}), cancelBatch: async () => ({ cancelled: true }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });

  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  const status = page.locator('.artifact-account-status');
  await expect(status).toContainText('01/02/2026 - 31/05/2026');
  await expect(status).toContainText('01/03/2026 - 31/05/2026');
  await expect(status).toContainText('01/04/2026 - 31/05/2026', { timeout: 4_500 });
  await expect(status).toContainText('01/05/2026 - 31/05/2026', { timeout: 4_500 });
  await expect(status).toHaveText('Sẵn sàng tải', { timeout: 4_500 });
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
