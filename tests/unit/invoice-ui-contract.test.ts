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

  it('uses a vector stop icon and the eight-column sync-state table', async () => {
    const [component, icons, styles] = await Promise.all([
      source('src/features/invoices/InvoiceManagementPage.tsx'),
      source('src/components/InvoiceActionIcons.tsx'),
      source('src/styles/invoice-refresh.css'),
    ]);
    expect(component).toContain('<StopIcon />');
    expect(icons).toContain('className="stop-button-icon"');
    expect(component).not.toContain("assets/figma/stop.png");
    expect(component).toContain('title={row.company}');
    expect(styles).toContain('minmax(170px, 1.2fr)');
    expect(component).toContain('artifact-quantity-header invoice-count-header');
    expect(component).toContain('<b>Tổng quan</b><b>Chi tiết</b>');
    expect(styles).toContain('grid-column: span 2');
    expect(component).toContain('invoice-count-value invoice-count-value--overview');
    expect(component).toContain('invoice-count-value invoice-count-value--detail');
    expect(component).toContain('row.syncState?.detail_invoice_count ?? 0');
    expect(component).not.toContain('<small>Tổng quan</small>');
    expect(component).not.toContain('<small>Chi tiết</small>');
    expect(component).not.toContain('row.actionsReady ?');
    expect(component).toContain('<button className="row-result-button"');
    expect(component).not.toContain('monthProgress');
  });

  it('uses the shared artifact toolbar language without changing invoice sync actions', async () => {
    const [component, styles] = await Promise.all([
      source('src/features/invoices/InvoiceManagementPage.tsx'),
      source('src/styles/invoice-refresh.css'),
    ]);
    expect(component).toContain('invoice-sync-toolbar-card');
    expect(component).toContain('1. Loại hóa đơn');
    expect(component).toContain('2. Khoảng thời gian');
    expect(component).toContain('3. Loại bảng kê');
    expect(component).toContain('<StorageFolderPicker');
    expect(component).toContain("startJob('new')");
    expect(component).toContain("startJob('supplement')");
    expect(styles).toContain('.invoice-page .invoice-sync-toolbar-card');
    expect(styles).toContain('grid-template-columns: minmax(220px, .9fr)');
    expect(styles).toContain('.invoice-page .invoice-sync-toolbar-card .storage-folder-picker.invoice-export-folder');
    expect(styles).toContain('.invoice-page .invoice-sync-toolbar-card .sync-menu-wrap');
    expect(styles).toContain('width: clamp(380px, 36vw, 520px)');
    expect(styles).toContain('right: 0;');
    expect(styles).toContain('overflow-wrap: break-word');
  });

  it('offers a snapshot-safe sync then download action', async () => {
    const [component, lifecycle, styles] = await Promise.all([
      source('src/features/invoices/InvoiceManagementPage.tsx'),
      source('src/features/jobs/use-batch-job-lifecycle.ts'),
      source('src/styles/invoice-refresh.css'),
    ]);
    expect(component).toContain('Đồng bộ &amp; tải xuống');
    expect(component).toContain("startJob('new', true)");
    expect(component).toContain("startJob('supplement', true)");
    expect(component).toContain('pendingAutoExport.current');
    expect(component).toContain("status !== 'completed' && status !== 'completed_with_warning'");
    expect(component).toContain('executeResultExport(snapshot, true)');
    expect(component).toContain('connectionIds: [...selectedAccountIds]');
    expect(lifecycle).toContain('return true;');
    expect(lifecycle).toContain('return false;');
    expect(styles).toContain('.invoice-sync-export-button');
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

  it('keeps backend-backed result filters and session-only Excel exclusions', async () => {
    const [page, popover, selection, bridge] = await Promise.all([
      source('src/features/results/ResultsPage.tsx'),
      source('src/features/results/ColumnFilterPopover.tsx'),
      source('src/features/results/result-selection.ts'),
      source('src/lib/runtime-bridge.ts'),
    ]);
    expect(page).toContain('ColumnFilterPopover');
    expect(page).toContain('Loại khỏi tải xuống');
    expect(page).toContain('Dữ liệu nguồn không bị xóa hoặc thay đổi.');
    expect(page).toContain('requestedScopes.map(scope => [scope, filtersByMode[scope]])');
    expect(page).toContain("mode === 'reconciliation' ? { keys: [], rules: [] } : exclusion");
    expect(popover).toContain('Sắp xếp tăng dần');
    expect(popover).toContain('(Chọn tất cả)');
    expect(selection).toContain('exclusionFromSelection');
    expect(bridge).toContain('facets(query: ResultFacetQuery)');
  });

  it('uses real account pages, a stable calendar control, and the HDDT back copy', async () => {
    const [invoicePage, datePicker, addAccount] = await Promise.all([
      source('src/features/invoices/InvoiceManagementPage.tsx'),
      source('src/components/DateRangePicker.tsx'),
      source('src/features/accounts/AddAccountPage.tsx'),
    ]);
    expect(invoicePage).toContain('ACCOUNT_PAGE_SIZE = 20');
    expect(invoicePage).toContain('pageRows.map');
    expect(invoicePage).toContain('Chọn tất cả tài khoản trên mọi trang');
    expect(invoicePage).toContain('function moveAccountPage(offset: number)');
    expect(invoicePage).not.toContain('[1, 2, 3].map');
    expect(datePicker).toContain('date-range-calendar-control');
    expect(addAccount).toContain('Quay lại Quản lý HĐĐT');
    expect(addAccount).not.toContain('Quay lại Quản lý tải');
  });

  it('uses the shared accessible password control and exposes all three result source modes', async () => {
    const [accountPage, passwordInput, resultsPage, styles] = await Promise.all([
      source('src/features/accounts/AddAccountPage.tsx'),
      source('src/components/PasswordInput.tsx'),
      source('src/features/results/ResultsPage.tsx'),
      source('src/styles/global.css'),
    ]);
    expect(accountPage).toContain('<PasswordInput');
    expect(passwordInput).toContain("type={showPassword ? 'text' : 'password'}");
    expect(passwordInput).toContain("aria-label={showPassword ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}");
    expect(passwordInput).toContain('aria-pressed={showPassword}');
    expect(passwordInput).toContain('type="button"');
    expect(passwordInput).toContain('event.preventDefault()');
    expect(styles).toContain('.account-field .password-input-control input { padding-right: 48px; }');
    expect(styles).toContain('.account-field > span:not(.password-input-control)');
    expect(resultsPage).toContain('<option value="query">Hóa đơn điện tử</option>');
    expect(resultsPage).toContain('<option value="sco-query">Máy tính tiền</option>');
    expect(resultsPage).toContain('<option value="combined">HĐĐT &amp; Máy tính tiền</option>');
    expect(resultsPage).toContain("query_types: ['query', 'sco-query']");
    expect(resultsPage).not.toContain('Mua vào và bán ra');
  });

  it('reuses the invoice-management action button for XML HTML downloads', async () => {
    const [results, page, artifactStyles, invoiceStyles] = await Promise.all([
      source('src/features/results/ResultsPage.tsx'),
      source('src/features/artifacts/XmlHtmlPage.tsx'),
      source('src/styles/xml-html.css'),
      source('src/styles/invoice-refresh.css'),
    ]);
    expect(results).toContain('results-export-trigger primary-download-button');
    expect(page).toContain('sync-button artifact-download-button');
    expect(invoiceStyles).toContain('.invoice-page .sync-button:not(:disabled):hover');
    expect(invoiceStyles).toContain('.invoice-page .sync-button:not(:disabled):active');
    expect(artifactStyles).not.toContain('.primary-download-button');
    expect(artifactStyles).not.toContain('linear-gradient');
  });

  it('uses one account-based XML HTML PDF surface without sync controls', async () => {
    const [page, invoicePage, styles] = await Promise.all([
      source('src/features/artifacts/XmlHtmlPage.tsx'),
      source('src/features/invoices/InvoiceManagementPage.tsx'),
      source('src/styles/xml-html.css'),
    ]);
    expect(page).toContain('Tải XML, HTML và PDF từ dữ liệu hóa đơn đã đồng bộ.');
    expect(page).not.toContain('Tải artifact');
    expect(page).toContain('artifact-toolbar-card');
    expect(page).toContain('<StorageFolderPicker');
    expect(page).not.toContain('Đồng bộ dữ liệu');
    expect(page).not.toContain('Thêm tài khoản');
    expect(page).toContain("useState<InvoiceArtifactKind[]>(['xml', 'html'])");
    expect(page).toContain('PDF');
    expect(page).toContain('artifact-account-row--head');
    expect(page).toContain('Dừng tải');
    expect(page).toContain('direction: InvoiceDirection');
    expect(page).toContain('directions: [selection.direction]');
    expect(page).toContain('compact-select compact-select--direction');
    expect(page).toContain("import { OptionCheck } from '../../components/OptionCheck'");
    expect(invoicePage).toContain("import { OptionCheck } from '../../components/OptionCheck'");
    expect(page).not.toContain('Vui lòng chọn ít nhất Mua vào hoặc Bán ra.');
    expect(page).not.toContain('Mở thư mục');
    expect(styles).toContain('.artifact-progress-cards[data-count=');
    expect(styles).toContain("[data-kind='pdf']");
    expect(styles).toContain('grid-template-columns: 52px 115px');
    expect(page).toContain('1. Loại hóa đơn');
    expect(page).toContain('2. Khoảng thời gian');
    expect(page).toContain('3. Chọn định dạng cần tải');
    expect(page).toContain('artifact-format-group');
    expect(page).toContain('artifact-quantity-header');
    expect(page).toContain('Trạng thái đồng bộ');
    expect(page).toContain('Xem kết quả');
    expect(styles).not.toContain('translateY');
    expect(styles).not.toContain('scale(');
  });

  it('queues one VAT workbook per selected account instead of rejecting batch selection', async () => {
    const page = await source('src/features/artifacts/VatReturnExportPage.tsx');
    expect(page).not.toContain('selectedConnectionIds.length !== 1');
    expect(page).not.toContain('Vui lòng chỉ chọn một tài khoản cho mỗi workbook');
    expect(page).toContain('for (let index = 0; index < exportIds.length; index += 1)');
    expect(page).toContain('connection_ids: [id]');
    expect(page).toContain('Đang xuất ${index + 1}/${exportIds.length}');
  });

  it('uses compact full-width equal artifact progress columns without invoice-detail copy', async () => {
    const [page, styles] = await Promise.all([
      source('src/features/artifacts/XmlHtmlPage.tsx'),
      source('src/styles/xml-html.css'),
    ]);
    expect(styles).toContain('width: 100%; display: grid');
    expect(styles).toContain('repeat(var(--artifact-card-count, 3), minmax(0, 1fr))');
    expect(styles).toContain("[data-count='1']");
    expect(styles).toContain("[data-count='2']");
    expect(styles).toContain("[data-count='3']");
    expect(styles).toContain('height: 68px');
    expect(styles).toContain('min-height: 0');
    expect(page).not.toContain('Đang xử lý:');
    expect(page).not.toContain('hóa đơn</small>');
    expect(page).not.toContain('Dừng XML');
    expect(page).not.toContain('Dừng HTML');
    expect(page).not.toContain('Dừng PDF');
  });

  it('uses serial recoverable artifact polling and the existing Results/action styles for failures', async () => {
    const [page, lifecycle] = await Promise.all([
      source('src/features/artifacts/XmlHtmlPage.tsx'),
      source('src/features/artifacts/use-artifact-download-lifecycle.ts'),
    ]);
    expect(lifecycle).not.toContain('setInterval');
    expect(lifecycle).toContain('window.setTimeout');
    expect(lifecycle).toContain('transient monitoring failure');
    expect(page).toContain('className="row-result-button"');
    expect(page).toContain('results-table--excel-schema');
    expect(page).toContain('<h1>Xem kết quả</h1>');
    expect(page).toContain('Bảng hóa đơn không tạo được file');
  });
});
