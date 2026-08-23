import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const root = process.cwd();
const source = (filename: string) => readFile(path.join(root, filename), 'utf8');

describe('invoice result control presentation', () => {
  it('keeps real bulk progress inside the compact blue export button', async () => {
    const [component, styles] = await Promise.all([
      source('src/features/invoices/InvoiceManagementPage.tsx'),
      source('src/styles/result-export-progress.css'),
    ]);
    expect(component).toContain('invoice-export-button-fill');
    expect(component).toContain('resultExports.percent');
    expect(component).not.toContain("? `Đang tạo Excel");
    expect(component).not.toContain('<ResultExportProgressBar');
    expect(styles).toContain(".invoice-export-all-button[data-exporting='true']");
    expect(styles).toContain('background: #2563b8');
    expect(styles).toContain('color: #fff');
  });

  it('uses a vector stop icon and reserves a wider company column', async () => {
    const [component, styles] = await Promise.all([
      source('src/features/invoices/InvoiceManagementPage.tsx'),
      source('src/styles/invoice-refresh.css'),
    ]);
    expect(component).toContain('<StopIcon />');
    expect(component).toContain('className="stop-button-icon"');
    expect(component).not.toContain("assets/figma/stop.png");
    expect(component).toContain('title={row.company}');
    expect(styles).toContain('minmax(170px, 1.2fr)');
  });

  it('uses one cumulative sync progress bar without monthly progress UI', async () => {
    const component = await source('src/features/invoices/InvoiceManagementPage.tsx');
    expect(component).toContain('formatSourceJobProgress(job)');
    expect(component).toContain('progress-track progress-track--overall');
    expect(component).not.toContain('monthProgress');
    expect(component).not.toContain('month-progress');
    expect(component).not.toContain('Tiến trình tổng');
  });

  it('uses blue result actions and blue-to-gold scroll thumbs', async () => {
    const [page, luxury] = await Promise.all([
      source('src/features/results/ResultsPage.tsx'),
      source('src/styles/results-luxury.css'),
    ]);
    expect(page).not.toContain('results-export-trigger--gold');
    expect(page).not.toContain('results-export-popover--gold');
    expect(luxury).toContain('#3b82f6 0%, #38a7d8 48%, #d6ad42 100%');
    expect(luxury).toContain('::-webkit-scrollbar-thumb:horizontal');
    expect(page).toContain('ColumnFilterPopover');
    expect(page).toContain('Chọn tất cả hóa đơn phù hợp bộ lọc trên mọi trang');
    expect(page).toContain('Loại khỏi tải xuống');
    expect(page).toContain('results-row--total');
  });

  it('nests SVG filter controls in each header and portals the rounded menus', async () => {
    const [page, popover, styles] = await Promise.all([
      source('src/features/results/ResultsPage.tsx'),
      source('src/features/results/ColumnFilterPopover.tsx'),
      source('src/styles/results-luxury.css'),
    ]);
    expect(page).toContain('className="result-header-cell"');
    expect(page).toContain('className="result-header-title"');
    expect(popover).toContain('<FilterIcon />');
    expect(popover).toContain('createPortal(popover, document.body)');
    expect(popover).toContain("event.key === 'Escape'");
    expect(popover).not.toContain('â–¼');
    expect(styles).toContain('.result-column-filter-menu');
    expect(styles).toContain('border-radius: var(--mia-radius-ui)');
    expect(styles).toContain('background: #fff');
    expect(styles).toContain('border: 1px solid #d0d5dd');
    expect(styles).toContain('.results-exclude-confirm');
  });

  it('keeps the result schema mounted for empty filtered pages', async () => {
    const page = await source('src/features/results/ResultsPage.tsx');
    expect(page).toContain("{columns.length ? <div className=\"results-table");
    expect(page).toContain('className="results-table-empty"');
    expect(page).toContain('Không có dữ liệu phù hợp với bộ lọc hiện tại.');
    expect(page).not.toContain('{items.length && columns.length ?');
  });

  it('uses one six-pixel radius token for non-circular interface surfaces', async () => {
    const tokens = await source('src/styles/tokens.css');
    expect(tokens).toContain('--mia-radius-ui: 6px');
    expect(tokens).toContain('--mia-radius-sm: var(--mia-radius-ui)');
    const styleNames = await import('node:fs/promises').then(({ readdir }) => readdir(path.join(root, 'src/styles')));
    const styles = await Promise.all(styleNames.filter(name => name.endsWith('.css')).map(name => source(`src/styles/${name}`)));
    const declarations = styles.flatMap(css => [...css.matchAll(/border-radius:\s*([^;]+);/g)].map(match => match[1].trim()));
    expect(declarations.every(value => value.includes('var(--mia-radius-ui)') || ['999px', '50%', 'inherit'].includes(value))).toBe(true);
  });

  it('uses real account pages, a stable calendar control, and the HDDT back copy', async () => {
    const [invoicePage, datePicker, addAccount] = await Promise.all([
      source('src/features/invoices/InvoiceManagementPage.tsx'),
      source('src/components/DateRangePicker.tsx'),
      source('src/features/accounts/AddAccountPage.tsx'),
    ]);
    expect(invoicePage).toContain('ACCOUNT_PAGE_SIZE = 20');
    expect(invoicePage).toContain('pageRows.map');
    expect(invoicePage).not.toContain('[1, 2, 3].map');
    expect(datePicker).toContain('date-range-calendar-control');
    expect(addAccount).toContain('Quay lại Quản lý HDDT');
    expect(addAccount).not.toContain('Quay lại Quản lý tải');
  });

  it('keeps account tabs geometrically stable and exposes the two sync modes', async () => {
    const [page, styles, invoiceStyles] = await Promise.all([
      source('src/features/invoices/InvoiceManagementPage.tsx'),
      source('src/styles/global.css'),
      source('src/styles/invoice-refresh.css'),
    ]);
    expect(styles).toContain('.account-tabs {');
    expect(styles).toContain('gap: 16px');
    expect(styles).toContain('border-bottom: 2px solid transparent');
    expect(styles).not.toContain('.account-tab[data-active="false"] { margin-left');
    expect(page).toContain("useState<InvoiceDirection>('purchase')");
    expect(page).not.toContain('setDirections');
    expect(page).toContain('Đồng bộ mới');
    expect(page).toContain('Đồng bộ bổ sung');
    expect(page).not.toContain('<span>Tải mới dữ liệu</span>');
    expect(page).toContain('<span>Trạng thái đồng bộ</span><span>Số lượng hóa đơn</span>');
    expect(invoiceStyles).toContain('.invoice-page .sync-mode-menu');
    expect(invoiceStyles).toContain('border-radius: var(--mia-radius-ui)');
    expect(invoiceStyles).toContain('background: #fff');
    expect(invoiceStyles).toContain('border: 1px solid #d0d5dd');
  });

  it('keeps Results and XML HTML download buttons blue in every interactive state', async () => {
    const [results, page, styles] = await Promise.all([
      source('src/features/results/ResultsPage.tsx'),
      source('src/features/artifacts/XmlHtmlPage.tsx'),
      source('src/styles/xml-html.css'),
    ]);
    expect(results).toContain('results-export-trigger primary-download-button');
    expect(page).toContain('primary-download-button xml-html-download');
    expect(styles).toContain('.primary-download-button:hover:not(:disabled)');
    expect(styles).toContain('.primary-download-button:active:not(:disabled)');
    expect(styles).toContain('background: #2563b8');
    expect(styles).toContain('color: #fff');
    expect(styles).not.toContain('.primary-download-button { background: linear-gradient');
  });

  it('uses the shared XML HTML control block and exact lifecycle copy', async () => {
    const [page, styles] = await Promise.all([
      source('src/features/artifacts/XmlHtmlPage.tsx'),
      source('src/styles/xml-html.css'),
    ]);
    expect(page).toContain('Tra cứu XML/HTML các hóa đơn đã/chưa đồng bộ');
    expect(page).toContain('xml-html-control-block');
    expect(page).toContain('<StorageFolderPicker');
    expect(page).toContain('Đồng bộ dữ liệu');
    expect(page).toContain('Tải xuống kết quả');
    expect(page).toContain('Dừng tải');
    expect(styles).toContain('.xml-html-company');
    expect(styles).toContain('font-size: 12px !important');
    expect(styles).toContain('width: clamp(245px, 25vw, 390px)');
    expect(styles).toContain('background: #f0fdf4');
    expect(styles).toContain('scrollbar-gutter: stable');
    expect(styles).toContain('width: 100%; min-width: 1345px');
    expect(page).toContain('title={cell === 8 ? undefined : String(value)}');
  });
});
