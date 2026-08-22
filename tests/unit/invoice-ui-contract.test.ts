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
    expect(styles).toContain('background: #e8f0fc');
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
    expect(styles).toContain('minmax(240px, 340px)');
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
