import { mkdirSync } from 'node:fs';
import { expect, test } from './licensed-test';

const viewports = [
  { width: 1920, height: 554 },
  { width: 1366, height: 768 },
  { width: 1024, height: 768 },
  { width: 800, height: 600 },
];

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const account = { connection_id: 'conn_results_header', username: '0100000000', company_name: 'Công ty kết quả', status: 'connected', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false };
    const record = { job_id: 'job_results_header', connection_id: account.connection_id, intent: {}, idempotency_key: 'results-header', created_at: 'now', updated_at: 'now', status: 'completed' };
    const result = async () => ({
      items: [{ row_id: 1, direction: 'purchase', invoice_key: 'purchase|invoice|1', fields: { stt: 1, shdon: '000001', nbten: 'Công ty bán hàng' } }],
      columns: ['stt', 'shdon', 'nbten'], column_labels: { stt: 'STT', shdon: 'Số hóa đơn', nbten: 'Tên người bán' },
      total_count: 1, aggregate: { matching_row_count: 1, row_count: 1, invoice_count: 1, totals: {} },
      pagination: { limit: 50, has_more: false, next_cursor: null },
    });
    Object.defineProperty(window, 'miaRuntime', { value: {
      accountConnections: { list: async () => [account], create: async () => account, get: async () => account, reconnect: async () => account, revoke: async () => undefined },
      jobs: {
        resume: async () => record, resumeAll: async () => [record], latestAll: async () => [record],
        syncStates: async () => ({}), status: async () => ({ ...record, stage: null, overall_percent: 100, current_month: null, error: null }),
        summary: async () => ({ job_id: record.job_id, status: 'completed', warning_count: 0, stages: [], coverage_plan: {}, work: {}, post_processing: {} }),
        start: async () => ({ record, accepted: {} }), cancel: async () => ({}), clear: async () => undefined,
      },
      preferences: { get: async () => ({ concurrency: 1, retries: 5, pdfConcurrency: 5, exportFolder: 'C:\\MIA' }), set: async (value: unknown) => value },
      results: { overview: result, details: result, facets: async () => ({ values: [], truncated: false, column_type: 'text' }) },
      artifacts: {
        export: async () => ({ count: 1, files: ['C:\\MIA\\result.xlsx'] }), selectDirectory: async () => 'C:\\MIA',
        list: async () => ({ items: [], pagination: { limit: 200, has_more: false, next_cursor: null } }),
        targets: async () => ({ keys: [], total: 0 }), cancel: async () => ({ cancelled: true }), openDirectory: async () => true,
        onInvoiceProgress: () => () => undefined, onExportProgress: () => () => undefined,
      },
      external: { open: async () => true },
    } });
  });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Xem kết quả' }).click();
  await expect(page.getByRole('heading', { name: 'Kết quả hóa đơn' })).toBeVisible();
  await page.locator('.results-back img').evaluate((image: HTMLImageElement) => image.decode());
});

test('results header keeps the back link in place and uses compact spacing between its lines', async ({ page }) => {
  const phase = process.env.RESULTS_SCREENSHOT_PHASE === 'before' ? 'before' : 'after';
  mkdirSync('outputs/results-header-alignment', { recursive: true });
  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    const metrics = await page.evaluate(() => {
      const box = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
      const pageBox = box('.results-page--figma');
      const back = box('.results-back');
      const title = box('.results-header--figma h1');
      const description = box('.results-header--figma p');
      const exportButton = box('.results-export-trigger');
      const tabs = box('.results-tabs--figma');
      const toolbar = box('.results-filters--figma');
      const table = box('.results-table--excel-schema');
      const pager = box('.results-pager');
      const icon = document.querySelector<HTMLImageElement>('.results-back img')!;
      return {
        backTopFromPage: back.top - pageBox.top,
        backTitleGap: title.top - back.bottom,
        titleDescriptionGap: description.top - title.bottom,
        descriptionTabsGap: tabs.top - description.bottom,
        exportTitleCenterDelta: (exportButton.top + exportButton.height / 2) - (title.top + title.height / 2),
        tabsDescriptionOverlap: description.bottom - tabs.top,
        toolbarTabsOverlap: tabs.bottom - toolbar.top,
        tableHeight: table.height,
        tableBottom: table.bottom,
        pagerBottom: pager.bottom,
        pageRight: pageBox.right,
        descriptionRight: description.right,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        documentScrollWidth: document.documentElement.scrollWidth,
        pageScrollWidth: document.querySelector<HTMLElement>('.results-page--figma')!.scrollWidth,
        pageClientWidth: document.querySelector<HTMLElement>('.results-page--figma')!.clientWidth,
        directChildren: [...document.querySelector<HTMLElement>('.results-page--figma')!.children].map(node => ({
          className: node.className, clientWidth: node.clientWidth, scrollWidth: node.scrollWidth,
          right: node.getBoundingClientRect().right,
        })),
        tableOverflowX: getComputedStyle(document.querySelector('.results-table--excel-schema')!).overflowX,
        tableOverflowY: getComputedStyle(document.querySelector('.results-table--excel-schema')!).overflowY,
        iconSource: icon.getAttribute('src') || '',
        iconLoaded: icon.complete && icon.naturalWidth > 0,
      };
    });
    await page.screenshot({ path: `outputs/results-header-alignment/${phase}-${viewport.width}x${viewport.height}.png` });
    console.log(`${phase} ${viewport.width}x${viewport.height}`, JSON.stringify(metrics));
    if (phase === 'before') continue;
    expect(metrics.backTopFromPage).toBeGreaterThanOrEqual(5);
    expect(metrics.backTopFromPage).toBeLessThanOrEqual(7);
    expect(metrics.backTitleGap).toBeGreaterThanOrEqual(8);
    expect(metrics.backTitleGap).toBeLessThanOrEqual(10);
    expect(metrics.titleDescriptionGap).toBeGreaterThanOrEqual(2);
    expect(metrics.titleDescriptionGap).toBeLessThanOrEqual(4);
    expect(metrics.descriptionTabsGap).toBeGreaterThanOrEqual(12);
    expect(metrics.descriptionTabsGap).toBeLessThanOrEqual(16);
    expect(Math.abs(metrics.exportTitleCenterDelta)).toBeLessThanOrEqual(10);
    expect(metrics.tabsDescriptionOverlap).toBeLessThanOrEqual(-12);
    expect(metrics.toolbarTabsOverlap).toBeLessThanOrEqual(0);
    expect(metrics.tableHeight).toBeGreaterThanOrEqual(80);
    expect(metrics.tableBottom).toBeLessThanOrEqual(metrics.viewportHeight);
    expect(metrics.pagerBottom).toBeLessThanOrEqual(metrics.viewportHeight);
    expect(metrics.descriptionRight).toBeLessThanOrEqual(metrics.pageRight);
    expect(metrics.pageScrollWidth).toBeLessThanOrEqual(metrics.pageClientWidth);
    expect(metrics.tableOverflowX).toBe('scroll');
    expect(metrics.tableOverflowY).toBe('scroll');
    expect(metrics.iconLoaded).toBe(true);
    expect(metrics.iconSource).toContain('back.png');
  }
});
