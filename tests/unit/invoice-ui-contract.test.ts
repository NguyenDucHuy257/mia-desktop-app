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
});
