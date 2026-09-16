import { expect, test } from './licensed-test';

const account = {
  connection_id: 'conn_download', username: '0100000000', company_name: 'Công ty Mẫu',
  status: 'ready', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false,
};

test('an active invoice sync blocks artifact download with a popup', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    const starts: unknown[] = [];
    Object.defineProperty(window, 'blockedArtifactStarts', { value: starts });
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: {
        resumeAll: async () => [{ job_id: 'job_active_sync', connection_id: accountValue.connection_id, status: 'running' }],
        latestAll: async () => [],
        status: async () => ({ job_id: 'job_active_sync', status: 'running', overall_percent: 25 }),
        summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined,
      },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        coverage: async (request: unknown) => ({ ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: true, missing_ranges: [] }] }),
        snapshot: async (request: unknown) => ({ ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 1, cached: { xml: 0, html: 0, pdf: 0 } }] }),
        startBatch: async (request: unknown) => { starts.push(request); return { task_id: 'must-not-start', status: 'running' }; },
        batchStatus: async () => ({}), cancelBatch: async () => ({ cancelled: false }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });

  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect(page.getByRole('alertdialog')).toContainText('Đang đồng bộ dữ liệu hóa đơn');
  await expect(page.getByRole('alertdialog')).toContainText('dừng tiến trình hiện tại');
  expect(await page.evaluate(() => (window as typeof window & { blockedArtifactStarts: unknown[] }).blockedArtifactStarts)).toHaveLength(0);
});

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
  await expect(page.locator('.topbar')).toBeVisible();
  await expect(page.getByLabel('Tìm kiếm tài khoản tải xuống')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Quản lý HĐĐT', exact: true }).locator('img')).toHaveCSS('filter', 'grayscale(1) saturate(0) opacity(0.72)');
  await expect(page.locator('.artifact-toolbar-card')).toHaveCSS('border-radius', '6px');
  await expect(page.getByLabel('Tìm kiếm tài khoản tải xuống')).toHaveCSS('border-radius', '6px');
  await expect(page.locator('.artifact-direction-select .compact-select')).toHaveCSS('white-space', 'nowrap');
  await expect(page.locator('.artifact-direction-select .compact-select')).toHaveCSS('border-radius', '6px');
  await expect(page.locator('.artifact-account-table')).toHaveCSS('border-radius', '6px');
  await expect(page.locator('.artifact-account-table')).toHaveCSS('overflow-y', 'scroll');
  await expect(page.locator('.artifact-account-table')).toHaveCSS('scrollbar-gutter', 'stable');
  const dateBox = await page.locator('.artifact-toolbar-card .date-range-trigger').boundingBox();
  const directionBox = await page.locator('.artifact-toolbar-card .artifact-direction-field').boundingBox();
  const folderBox = await page.locator('.artifact-toolbar-card .invoice-export-folder').boundingBox();
  expect(dateBox).not.toBeNull();
  expect(directionBox).not.toBeNull();
  expect(folderBox).not.toBeNull();
  expect(Math.abs((directionBox?.x ?? 0) - (folderBox?.x ?? 0))).toBeLessThanOrEqual(1);
  await expect(page.getByRole('heading', { name: 'XML/HTML/PDF' })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'PDF', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Thêm tài khoản' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Đồng bộ dữ liệu' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Xóa tài khoản/ })).toHaveCount(0);
  await expect(page.locator('.artifact-account-row strong', { hasText: 'Công ty Mẫu' })).toBeVisible();
  await expect(page.locator('body')).not.toContainText(/artifact/i);
  await expect(page.getByRole('button', { name: 'Mở thư mục' })).toHaveCount(0);
  const downloadButton = page.getByRole('button', { name: 'Tải xuống', exact: true });
  const stopButton = page.getByRole('button', { name: 'Dừng tải', exact: true });
  await expect(downloadButton).toHaveClass(/sync-button/);
  await expect(downloadButton.locator('.invoice-action-icon')).toHaveCount(1);
  await expect(downloadButton).toHaveCSS('border-radius', '6px');
  await expect(stopButton).toHaveCount(0);
  await expect(page.locator('.artifact-quantity-value[data-kind="xml"]')).toHaveText('4/12');
  await expect(page.locator('.artifact-quantity-value[data-kind="html"]')).toHaveText('3/12');
  await expect(page.locator('.artifact-quantity-value[data-kind="pdf"]')).toHaveText('2/12');
  await expect(page.getByRole('checkbox', { name: 'XML', exact: true })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'HTML', exact: true })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'PDF', exact: true })).not.toBeChecked();
  await expect(page.locator('.artifact-toolbar-field')).toHaveCount(3);
  await expect(page.locator('.artifact-quantity-header')).toContainText('Số lượng hóa đơnXMLHTMLPDF');
  const quantityHeaderLayout = await page.locator('.artifact-quantity-header').evaluate((header) => ({
    height: header.getBoundingClientRect().height,
    centers: [...header.querySelectorAll('b')].map((cell) => {
      const bounds = cell.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(cell);
      const textBounds = range.getBoundingClientRect();
      return {
        x: Math.abs((bounds.left + bounds.width / 2) - (textBounds.left + textBounds.width / 2)),
        y: Math.abs((bounds.top + bounds.height / 2) - (textBounds.top + textBounds.height / 2)),
      };
    }),
  }));
  expect(quantityHeaderLayout.height).toBe(58);
  expect(Math.max(...quantityHeaderLayout.centers.map((center) => center.x))).toBeLessThanOrEqual(1);
  expect(Math.max(...quantityHeaderLayout.centers.map((center) => center.y))).toBeLessThanOrEqual(1);
  await expect(page.locator('.artifact-coverage-badge')).toContainText('Đã đồng bộ');
  const verticalLayout = await page.locator('.artifact-account-content').evaluate((content) => {
    const table = content.querySelector<HTMLElement>('.artifact-account-table');
    const footer = content.querySelector<HTMLElement>('.pagination');
    const contentBox = content.getBoundingClientRect();
    const tableBox = table?.getBoundingClientRect();
    const footerBox = footer?.getBoundingClientRect();
    return {
      contentBottom: contentBox.bottom,
      tableHeight: tableBox?.height ?? 0,
      tableBottom: tableBox?.bottom ?? 0,
      footerTop: footerBox?.top ?? 0,
      footerBottom: footerBox?.bottom ?? 0,
    };
  });
  expect(verticalLayout.tableHeight).toBeGreaterThan(200);
  expect(verticalLayout.tableBottom).toBeLessThanOrEqual(verticalLayout.footerTop);
  expect(Math.abs(verticalLayout.contentBottom - verticalLayout.footerBottom)).toBeLessThanOrEqual(1);
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot('unified-artifact-account-1500x1024.png', {
    animations: 'disabled', maxDiffPixelRatio: 0.02, threshold: 0.25,
  });
  await downloadButton.click();
  await expect(stopButton).toHaveClass(/stop-button/);
  await expect(stopButton.locator('.stop-button-icon')).toHaveCount(1);
  await expect(stopButton).toHaveCSS('border-radius', '6px');
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
  await expect(page.locator('.artifact-progress-cards').getByRole('button')).toHaveCount(0);
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
  await expect(page.locator('.artifact-coverage-badge')).toContainText('Chưa đồng bộ');
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect(page.getByRole('alertdialog')).toContainText('chưa được đồng bộ đầy đủ');
  const starts = await page.evaluate(() => (window as typeof window & { forbiddenStarts: { jobStarts: unknown[]; batchStarts: unknown[] } }).forbiddenStarts);
  expect(starts.jobStarts).toHaveLength(0);
  expect(starts.batchStarts).toHaveLength(0);
});

test('PDF can be selected alone and only global batch cancellation is exposed', async ({ page }) => {
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
  await page.getByRole('checkbox', { name: 'XML', exact: true }).click();
  await page.getByRole('checkbox', { name: 'HTML', exact: true }).click();
  await page.getByRole('checkbox', { name: 'PDF', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Tải xuống', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect(page.locator('.artifact-progress-card[data-kind="pdf"]')).toBeVisible();
  const singleLayout = await page.locator('.artifact-progress-cards').evaluate((container) => ({
    container: container.getBoundingClientRect().width,
    card: container.querySelector<HTMLElement>('.artifact-progress-card')?.getBoundingClientRect().width ?? 0,
  }));
  expect(Math.abs(singleLayout.container - singleLayout.card)).toBeLessThanOrEqual(1);
  await expect(page.locator('.artifact-progress-card[data-kind="pdf"]').getByRole('button')).toHaveCount(0);
  await page.getByRole('button', { name: 'Dừng tải', exact: true }).click();
  const calls = await page.evaluate(() => (window as typeof window & { pdfOnlyCalls: { starts: Array<Record<string, unknown>>; cancellations: unknown[] } }).pdfOnlyCalls);
  expect(calls.starts[0]).toMatchObject({ kinds: ['pdf'], pdf_concurrency: 100 });
  expect(calls.cancellations).toEqual([undefined]);
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
  await page.locator('.artifact-date-field .date-range-trigger').click();
  await page.getByLabel('Từ ngày nhập tay').fill('01/08/2026');
  await page.getByLabel('Đến ngày nhập tay').fill('23/08/2026');
  await page.getByRole('button', { name: 'Áp dụng' }).click();
  await page.getByRole('button', { name: 'Quản lý HĐĐT', exact: true }).click();
  await page.locator('.invoice-date-field .date-range-trigger').click();
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
  await expect(page.locator('.artifact-coverage-badge')).toContainText('01/04/2026 - 31/05/2026');
  await page.waitForTimeout(180);
  await expect(page.locator('.artifact-coverage-badge')).toContainText('01/04/2026 - 31/05/2026');
  await expect(page.locator('.artifact-quantity-value[data-kind="xml"]')).toHaveText('10/20');

  for (let index = 0; index < 2; index += 1) {
    await page.getByRole('button', { name: 'Quản lý HĐĐT', exact: true }).click();
    await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Bán ra' })).toBeVisible();
    await expect(page.locator('.artifact-coverage-badge')).toContainText('01/04/2026 - 31/05/2026');
  }

  await page.getByRole('button', { name: 'Bán ra' }).click();
  await page.getByLabel('Loại hóa đơn').getByText('Mua vào', { exact: true }).click();
  await expect(page.locator('.artifact-coverage-badge')).toContainText('Đã đồng bộ');
  await expect(page.locator('.artifact-quantity-value[data-kind="xml"]')).toHaveText('40/40');
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
  const status = page.locator('.artifact-coverage-badge');
  await expect(status).toContainText('01/02/2026 - 31/05/2026');
  await expect(status).toContainText('01/03/2026 - 31/05/2026');
  await expect(status).toContainText('01/04/2026 - 31/05/2026', { timeout: 4_500 });
  await expect(status).toContainText('01/05/2026 - 31/05/2026', { timeout: 4_500 });
  await expect(status).toContainText('Đã đồng bộ', { timeout: 4_500 });
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

test('coverage stays checking until the authoritative response arrives', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        coverage: async (request: unknown) => { await new Promise((resolve) => setTimeout(resolve, 1_000)); return { ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: false, missing_ranges: [{ date_from: '2026-04-01', date_to: '2026-08-31' }] }] }; },
        snapshot: async (request: unknown) => ({ ...(request as object), accounts: [] }), startBatch: async () => ({ task_id: 'unused', status: 'running' }), batchStatus: async () => ({}), cancelBatch: async () => ({ cancelled: true }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });
  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  await expect(page.locator('.artifact-coverage-badge')).toContainText('Đang kiểm tra');
  await expect(page.locator('.artifact-coverage-badge')).not.toContainText('Chưa đồng bộ');
  await expect(page.locator('.artifact-coverage-badge')).toContainText('01/04/2026 - 31/08/2026', { timeout: 3_000 });
});

test('slow and transient status polls never overlap or stop the live batch', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    const stats = { inFlight: 0, maxInFlight: 0, calls: 0 };
    Object.defineProperty(window, 'artifactPollStats', { value: stats });
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        snapshot: async (request: unknown) => ({ ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 10, cached: { xml: 0, html: 0, pdf: 0 } }] }),
        startBatch: async () => ({ task_id: 'serial-poll', status: 'running' }),
        batchStatus: async () => {
          stats.calls += 1; stats.inFlight += 1; stats.maxInFlight = Math.max(stats.maxInFlight, stats.inFlight);
          const call = stats.calls;
          await new Promise((resolve) => setTimeout(resolve, 1_000));
          stats.inFlight -= 1;
          if (call === 1) throw Object.assign(new Error('Runtime request timed out.'), { code: 'runtime_timeout' });
          return { task_id: 'serial-poll', status: 'running', current_account_id: accountValue.connection_id, accounts: { [accountValue.connection_id]: { connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 10, cached: { xml: 1, html: 1, pdf: 0 }, status: 'downloading', failure_count: 0 } }, formats: { xml: { status: 'running', processed: 1, total: 10, percent: 10, current_invoice: null, failed: 0 }, html: { status: 'running', processed: 1, total: 10, percent: 10, current_invoice: null, failed: 0 } }, warning_count: 0 };
        },
        cancelBatch: async () => ({ cancelled: true }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });
  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { artifactPollStats: { calls: number } }).artifactPollStats.calls)).toBeGreaterThanOrEqual(2);
  const stats = await page.evaluate(() => (window as typeof window & { artifactPollStats: { maxInFlight: number } }).artifactPollStats);
  expect(stats.maxInFlight).toBe(1);
  await expect(page.getByRole('button', { name: 'Dừng tải', exact: true })).toBeEnabled();
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
});

test('missing original appears immediately in the structured account failure table without failing the batch', async ({ page }) => {
  await page.addInitScript(({ accountValue }) => {
    let statusCalls = 0;
    Object.defineProperty(window, 'failureStatusCalls', { get: () => statusCalls });
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [accountValue] },
      jobs: { resumeAll: async () => [], latestAll: async () => [], status: async () => ({}), summary: async () => ({}), cancel: async () => ({}), clear: async () => undefined },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      artifacts: {
        snapshot: async (request: unknown) => ({ ...(request as object), accounts: [{ connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 100, cached: { xml: 0, html: 0, pdf: 0 } }] }),
        startBatch: async () => ({ task_id: 'failure-list', status: 'running' }),
        batchStatus: async () => { statusCalls += 1; return { task_id: 'failure-list', status: 'running', current_account_id: accountValue.connection_id, accounts: { [accountValue.connection_id]: { connection_id: accountValue.connection_id, ready: true, missing_ranges: [], total: 100, cached: { xml: 20, html: 20, pdf: 0 }, status: 'downloading', failure_count: 1 } }, formats: { xml: { status: 'running', processed: 20, total: 100, percent: 21, current_invoice: null, failed: 0, skipped: 1 }, html: { status: 'running', processed: 20, total: 100, percent: 21, current_invoice: null, failed: 0, skipped: 1 } }, warning_count: 0 }; },
        batchFailures: async () => ({ task_id: 'failure-list', connection_id: accountValue.connection_id, total: 1, offset: 0, limit: 50, items: [{ account_id: accountValue.connection_id, invoice_key: 'purchase|query|0101|AA|21|1', date: '2026-08-21', direction: 'purchase', khmshdon: '1', khhdon: 'AA', shdon: '21', nbmst: '0101', partner_name: 'Đối tác mẫu', affected_formats: ['xml', 'html'], category: 'missing_original', message: 'Không tồn tại hồ sơ gốc' }] }),
        cancelBatch: async () => ({ cancelled: true }), selectDirectory: async () => 'C:\\MIA', openDirectory: async () => true,
      },
    } });
  }, { accountValue: account });
  await page.goto('/', { waitUntil: 'commit' });
  await page.getByRole('button', { name: 'XML/HTML/PDF', exact: true }).click();
  await page.getByRole('button', { name: 'Tải xuống', exact: true }).click();
  const failures = page.getByRole('button', { name: 'Xem kết quả' });
  await expect(failures).toBeVisible();
  await failures.click();
  await expect(page.getByRole('heading', { name: 'Xem kết quả' })).toBeVisible();
  await expect(page.getByLabel('Bảng hóa đơn không tạo được file')).toContainText('Không tồn tại hồ sơ gốc');
  await expect(page.getByLabel('Bảng hóa đơn không tạo được file')).toContainText('XML, HTML');
  const callsBefore = await page.evaluate(() => (window as typeof window & { failureStatusCalls: number }).failureStatusCalls);
  await page.waitForTimeout(900);
  expect(await page.evaluate(() => (window as typeof window & { failureStatusCalls: number }).failureStatusCalls)).toBeGreaterThan(callsBefore);
  await page.getByRole('button', { name: /Quay lại XML\/HTML\/PDF/ }).click();
  await expect(page.getByRole('checkbox', { name: 'XML', exact: true })).toBeVisible();
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
});
