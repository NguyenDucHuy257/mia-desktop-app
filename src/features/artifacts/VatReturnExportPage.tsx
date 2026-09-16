import { useEffect, useMemo, useRef, useState } from 'react';
import { DateRangePicker } from '../../components/DateRangePicker';
import { NoticeDialog, type NoticeKind } from '../../components/NoticeDialog';
import { StorageFolderPicker } from '../../components/StorageFolderPicker';
import { DownloadIcon } from '../../components/InvoiceActionIcons';
import { pageBounds, paginationTokens } from '../../components/pagination-utils';
import searchIcon from '../../assets/figma/search.png';
import type { AccountConnection } from '../../lib/api/contracts';
import type { VatReturnCoverageAccount, VatReturnIssue, VatReturnMissingRange } from '../../lib/runtime-bridge';
import { workspaceTaskConflictMessage, type WorkspaceTask } from '../../lib/workspace-task';
import { CoverageBadge, formatDate } from './ArtifactCoverageBadge';
import '../../styles/xml-html.css';

const PAGE_SIZE = 20;
const ALL_ACCOUNT_ROWS = 1_000_000;
type CoverageState = 'loading' | 'ready' | 'error';
type StatusFilter = '' | 'ready' | 'not_ready' | 'checking' | 'error';

export interface VatReturnSelectionState { dateFrom: string; dateTo: string }
interface VatReturnFeedback { kind: Exclude<NoticeKind, 'notice'>; message: string; path?: string; canContinue?: boolean }

function validIsoDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

export function vatReturnExportErrorFeedback(error: unknown): VatReturnFeedback {
  const runtimeError = error as Error & { code?: string };
  const code = String(runtimeError?.code ?? /^\[([^\]]+)\]/.exec(runtimeError?.message ?? '')?.[1] ?? '');
  if (code === 'vat_return_export_failed' || code === 'internal_error') {
    return { kind: 'error', message: 'Xuất tờ khai GTGT thất bại do lỗi xử lý nội bộ. Vui lòng xem Nhật ký tại thời điểm xuất để xác định nguyên nhân. File kết quả chưa được xác nhận tạo thành công.' };
  }
  const warningText = `${code} ${runtimeError?.message ?? ''}`;
  if (warningText.includes('vat_return_purchase_reduction_invalid:')) {
    const count = /"count"\s*:\s*(\d+)/.exec(warningText)?.[1];
    return { kind: 'warning', canContinue: true, message: `${count ? `${count} dòng` : 'Một số dòng'} chi tiết Mua vào 8% thiếu tên hàng, thành tiền hoặc tiền thuế. Đây có thể là dòng diễn giải.\nNếu tiếp tục, ô trống sẽ giữ trống và không cộng giá trị vào tổng; dòng có số tiền không hợp lệ sẽ được bỏ qua. Vui lòng kiểm tra file sau khi tải.\nBạn có muốn tiếp tục tải tờ khai?` };
  }
  if (code === 'artifact_task_active') {
    return { kind: 'warning', message: 'Đang có một tiến trình tải hoặc xuất file khác. Vui lòng chờ tiến trình hiện tại hoàn tất.' };
  }
  if (code.startsWith('vat_return_destination_file_locked:')) {
    try {
      const detail = JSON.parse(code.slice('vat_return_destination_file_locked:'.length)) as { filename?: string; path?: string };
      const filename = detail.filename || 'tờ khai thuế GTGT';
      return {
        kind: 'error',
        message: `Không thể ghi đè tờ khai thuế GTGT vì file đang được mở.\nVui lòng đóng file:\n${filename}\nSau đó thử xuất lại.`,
        path: detail.path,
      };
    } catch { /* use the sanitized broker message below */ }
  }
  return { kind: 'error', message: `Không thể tạo tờ khai thuế GTGT:\n${runtimeError?.message || 'Exporter thất bại.'}` };
}

export async function loadVatReturnCoverage(connectionIds: string[], selection: VatReturnSelectionState) {
  const chunks: string[][] = [];
  for (let index = 0; index < connectionIds.length; index += 50) chunks.push(connectionIds.slice(index, index + 50));
  const transientCodes = new Set(['runtime_timeout', 'runtime_not_running', 'database_locked', 'database_unavailable']);
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const accounts: VatReturnCoverageAccount[] = [];
      // Keep local SQLite reads bounded: chunks are intentionally sequential
      // so opening the VAT page cannot create competing database scans.
      for (const connection_ids of chunks) {
        const result = await window.miaRuntime!.artifacts.vatReturnCoverage({
          connection_ids, date_from: selection.dateFrom, date_to: selection.dateTo,
        });
        accounts.push(...result.accounts);
      }
      return accounts;
    } catch (error) {
      const code = String((error as Error & { code?: string })?.code || '');
      if (attempt >= 2 || !transientCodes.has(code)) throw error;
      await new Promise((resolve) => window.setTimeout(resolve, 250 * (attempt + 1)));
    }
  }
  return [];
}

function SelectionBox({ checked, indeterminate = false }: { checked: boolean; indeterminate?: boolean }) {
  return <span className="selection-box" data-checked={checked || indeterminate}>{indeterminate ? '−' : checked ? '✓' : ''}</span>;
}

function missingText(missing: VatReturnMissingRange[]) {
  return missing.map((item) => `${item.scope === 'overview' ? 'Tổng quan' : 'Chi tiết'}: ${formatDate(item.date_from)} - ${formatDate(item.date_to)}`).join('; ');
}

function accountMissing(account: AccountConnection, coverage?: VatReturnCoverageAccount) {
  if (!coverage) return `${account.username}: Không thể kiểm tra coverage.`;
  return (['purchase', 'sold'] as const).flatMap((direction) => {
    const value = coverage[direction];
    if (value.ready) return [];
    return [`${account.username} – ${direction === 'purchase' ? 'Mua vào' : 'Bán ra'} – ${missingText(value.missing)}`];
  }).join('\n');
}

function VatIssueDialog({ account, selection, onClose }: { account: AccountConnection; selection: VatReturnSelectionState; onClose(): void }) {
  const [items, setItems] = useState<VatReturnIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({});
  const [message, setMessage] = useState('');
  async function reload() {
    setLoading(true); setMessage('');
    try {
      const result = await window.miaRuntime!.artifacts.vatReturnIssues({ connection_id: account.connection_id, date_from: selection.dateFrom, date_to: selection.dateTo });
      setItems(result.items);
      setDrafts(Object.fromEntries(result.items.map((item) => [item.issue_id, Object.fromEntries(item.fields.map((field) => [field.name, field.value]))])));
    } catch { setMessage('Không thể đọc danh sách dữ liệu cần kiểm tra.'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void reload(); }, [account.connection_id, selection.dateFrom, selection.dateTo]);
  async function save(item: VatReturnIssue) {
    try {
      await window.miaRuntime!.artifacts.vatReturnIssueUpdate({ connection_id: account.connection_id, source: item.source, record_id: item.record_id, values: drafts[item.issue_id] || {} });
      setEditing(null); await reload(); setMessage('Đã lưu dữ liệu hóa đơn.');
    } catch { setMessage('Không thể lưu dữ liệu hóa đơn. Vui lòng kiểm tra giá trị vừa nhập.'); }
  }
  return <div className="vat-issue-backdrop" role="presentation"><section className="vat-issue-dialog" role="dialog" aria-modal="true" aria-labelledby="vat-issue-title">
    <header><div><h2 id="vat-issue-title">Kết quả kiểm tra dữ liệu GTGT</h2><p>{account.username} · Nhấp chuột phải vào ô cần sửa.</p></div><button type="button" aria-label="Đóng" onClick={onClose}>×</button></header>
    {message ? <div className="vat-issue-message" role="status">{message}</div> : null}
    <div className="vat-issue-table-wrap"><table className="vat-issue-table"><thead><tr><th>Loại</th><th>Số hóa đơn</th><th>Ngày</th><th>Vấn đề</th><th>Dữ liệu có thể sửa</th><th /></tr></thead><tbody>{items.map((item) => <tr key={item.issue_id}><td>{item.direction === 'purchase' ? 'Mua vào' : 'Bán ra'}</td><td>{item.invoice_number || '—'}</td><td>{formatDate(item.invoice_date)}</td><td>{item.reason}</td><td><div className="vat-issue-fields">{item.fields.map((field) => { const editKey = `${item.issue_id}:${field.name}`; return <label key={field.name} onContextMenu={(event) => { event.preventDefault(); setEditing(editKey); }}><span>{field.label}</span>{editing === editKey ? <input autoFocus value={drafts[item.issue_id]?.[field.name] ?? ''} onChange={(event) => setDrafts((current) => ({ ...current, [item.issue_id]: { ...(current[item.issue_id] || {}), [field.name]: event.target.value } }))} /> : <button type="button" title="Nhấp chuột phải để sửa" onContextMenu={(event) => { event.preventDefault(); setEditing(editKey); }}>{drafts[item.issue_id]?.[field.name] || '0'}</button>}</label>; })}</div></td><td><button className="vat-issue-save" type="button" onClick={() => void save(item)}>Lưu</button></td></tr>)}</tbody></table>
      {!loading && !items.length ? <div className="vat-issue-empty">Không còn hóa đơn nào cần kiểm tra trong khoảng đã chọn.</div> : null}{loading ? <div className="vat-issue-empty">Đang kiểm tra dữ liệu…</div> : null}</div>
    <footer><button type="button" onClick={onClose}>Đóng</button></footer>
  </section></div>;
}

export function VatReturnExportPage({ accounts, selectedConnectionIds, onSelectAccount, onSelectAccounts, folder, onFolder, selection, onSelectionChange, coverageRevision, activeWorkspaceTask, onExportingChange }: {
  accounts: AccountConnection[];
  selectedConnectionIds: string[];
  onSelectAccount(id: string): void;
  onSelectAccounts(ids: string[]): void;
  folder: string;
  onFolder(value: string): void;
  selection: VatReturnSelectionState;
  onSelectionChange(value: VatReturnSelectionState): void;
  coverageRevision: number;
  activeWorkspaceTask?: WorkspaceTask | null;
  onExportingChange?(active: boolean): void;
}) {
  const [coverage, setCoverage] = useState<Record<string, VatReturnCoverageAccount>>({});
  const [coverageState, setCoverageState] = useState<CoverageState>('loading');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [feedback, setFeedback] = useState<VatReturnFeedback | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportStates, setExportStates] = useState<Record<string, string>>({});
  const [issueAccountId, setIssueAccountId] = useState<string | null>(null);
  const generation = useRef(0);
  const initialForm = useRef({ selection, folder });
  const accountIds = useMemo(() => accounts.map((account) => account.connection_id), [accounts]);

  useEffect(() => {
    const token = ++generation.current;
    setCoverage({});
    if (!accountIds.length) { setCoverageState('ready'); return; }
    setCoverageState('loading');
    void loadVatReturnCoverage(accountIds, selection).then((items) => {
      if (token !== generation.current) return;
      setCoverage(Object.fromEntries(items.map((item) => [item.connection_id, item])));
      setCoverageState('ready');
    }).catch(() => { if (token === generation.current) setCoverageState('error'); });
    return () => { if (token === generation.current) generation.current += 1; };
  }, [accountIds, coverageRevision, selection.dateFrom, selection.dateTo]);

  const rows = accounts.map((account) => {
    const item = coverage[account.connection_id];
    const status: StatusFilter = coverageState === 'loading' ? 'checking' : coverageState === 'error' || !item ? 'error' : item.purchase.ready && item.sold.ready ? 'ready' : 'not_ready';
    return { account, coverage: item, status };
  });
  const filteredRows = rows.filter(({ account, status }) => {
    const term = search.trim().toLocaleLowerCase('vi');
    return (!term || `${account.username} ${account.company_name ?? ''}`.toLocaleLowerCase('vi').includes(term)) && (!statusFilter || status === statusFilter);
  });
  const { currentPage, totalPages, start, end } = pageBounds(filteredRows.length, page, pageSize);
  const pageRows = filteredRows.slice(start, end);
  const tokens = paginationTokens(totalPages, currentPage);
  const filteredIds = filteredRows.map((row) => row.account.connection_id);
  const selectedFiltered = filteredIds.filter((id) => selectedConnectionIds.includes(id));
  useEffect(() => { if (page !== currentPage) setPage(currentPage); }, [currentPage, page]);

  async function chooseFolder() { const selected = await window.miaRuntime?.artifacts.selectDirectory(); if (selected) onFolder(selected); }
  function resetForm() { onSelectionChange({ ...initialForm.current.selection }); onFolder(initialForm.current.folder); setFeedback(null); }
  async function prepareExport(allowIncomplete = false) {
    const conflict = workspaceTaskConflictMessage(activeWorkspaceTask ?? null, 'vat-return-export');
    if (conflict) { setFeedback({ kind: 'warning', message: conflict }); return; }
    if (exporting) { setFeedback({ kind: 'warning', message: 'Đang xuất tờ khai thuế GTGT. Vui lòng chờ tiến trình hiện tại hoàn tất.' }); return; }
    const exportIds = [...new Set(selectedConnectionIds)].filter((id) => accounts.some((account) => account.connection_id === id));
    if (!exportIds.length) { setFeedback({ kind: 'warning', message: 'Vui lòng chọn ít nhất một tài khoản để xuất tờ khai thuế GTGT.' }); return; }
    if (!validIsoDate(selection.dateFrom) || !validIsoDate(selection.dateTo) || selection.dateFrom > selection.dateTo) { setFeedback({ kind: 'warning', message: 'Khoảng ngày xuất tờ khai không hợp lệ. Vui lòng kiểm tra ngày bắt đầu và ngày kết thúc.' }); return; }
    if (!folder.trim()) { setFeedback({ kind: 'warning', message: 'Vui lòng chọn thư mục lưu trữ.' }); return; }
    if (coverageState !== 'ready') { setFeedback({ kind: 'error', message: 'Không thể xác nhận dữ liệu đã đồng bộ. Vui lòng thử lại.' }); return; }
    const missing = exportIds.flatMap((id) => {
      const account = accounts.find((item) => item.connection_id === id);
      return account ? accountMissing(account, coverage[id]) : [];
    }).filter(Boolean);
    if (missing.length) {
      setFeedback({ kind: 'warning', message: `Chưa thể xuất workbook vì thiếu dữ liệu:\n${missing.join('\n')}\nVui lòng sang Quản lý HĐĐT để đồng bộ bổ sung.` });
      return;
    }
    setExporting(true);
    onExportingChange?.(true);
    const completedFiles: string[] = [];
    const failures: Array<{ id: string; feedback: VatReturnFeedback }> = [];
    try {
      for (let index = 0; index < exportIds.length; index += 1) {
        const id = exportIds[index]!;
        setExportStates((current) => ({ ...current, [id]: `Đang xuất ${index + 1}/${exportIds.length}…` }));
        try {
          // The runtime deliberately accepts one account per workbook. Queue
          // selected accounts here so exports never compete for the same file.
          const result = await window.miaRuntime!.artifacts.vatReturnExport({
            destination: folder, connection_ids: [id],
            date_from: selection.dateFrom, date_to: selection.dateTo,
            ...(allowIncomplete ? { allow_incomplete: true } : {}),
          });
          if (result.count !== 1 || !result.files[0]) {
            throw new Error('Exporter không trả về file kết quả hợp lệ.');
          }
          completedFiles.push(result.files[0]);
          setExportStates((current) => ({ ...current, [id]: 'Hoàn thành' }));
        } catch (error) {
          const failure = vatReturnExportErrorFeedback(error);
          failures.push({ id, feedback: failure });
          setExportStates((current) => ({ ...current, [id]: failure.canContinue ? 'Chờ xác nhận' : 'Xuất thất bại' }));
          if (failure.canContinue) break;
        }
      }

      const confirmable = failures.find((item) => item.feedback.canContinue);
      if (confirmable) {
        const account = accounts.find((item) => item.connection_id === confirmable.id);
        setFeedback({
          ...confirmable.feedback,
          message: `${account?.username || confirmable.id}: ${confirmable.feedback.message}`,
        });
      } else if (failures.length === 1 && exportIds.length === 1) {
        setFeedback(failures[0]!.feedback);
      } else if (failures.length) {
        const failedLabels = failures.map(({ id }) => accounts.find((item) => item.connection_id === id)?.username || id);
        setFeedback({
          kind: 'error',
          message: `Đã xuất ${completedFiles.length}/${exportIds.length} tờ khai. Không thể xuất: ${failedLabels.join(', ')}.`,
          path: folder,
        });
      } else {
        setFeedback({
          kind: 'success',
          message: exportIds.length === 1
            ? 'Đã tạo tờ khai thuế GTGT thành công.'
            : `Đã tạo ${completedFiles.length} tờ khai thuế GTGT thành công theo thứ tự.`,
          path: exportIds.length === 1 ? completedFiles[0] : folder,
        });
      }
    } finally {
      setExporting(false);
      onExportingChange?.(false);
    }
  }

  return <section className="artifact-account-page vat-return-page invoice-page" aria-labelledby="vat-return-title">
    <header className="artifact-account-header"><h1 id="vat-return-title">Hỗ trợ lập tờ khai thuế GTGT</h1><p>Tổng hợp số liệu hóa đơn và tạo file Excel tham khảo. Vui lòng kiểm tra, đối chiếu trước khi kê khai chính thức.</p></header>
    <section className="artifact-toolbar-card vat-return-toolbar toolbar-card" aria-label="Thiết lập xuất tờ khai thuế GTGT">
      <div className="artifact-toolbar-field artifact-date-field"><label>1. Khoảng thời gian</label><DateRangePicker disabled={exporting} dateFrom={selection.dateFrom} dateTo={selection.dateTo} onChange={(dateFrom, dateTo) => onSelectionChange({ dateFrom, dateTo })} /></div>
      <div className="artifact-toolbar-field artifact-storage-field"><label>2. Đường dẫn lưu trữ</label><StorageFolderPicker className="invoice-export-folder" value={folder} onChange={onFolder} onBrowse={chooseFolder} ariaLabel="Đường dẫn lưu trữ" /></div>
      <div className="artifact-toolbar-actions"><button className="add-account artifact-reset-button" type="button" disabled={exporting} onClick={resetForm}><span aria-hidden="true">↻</span>Đặt lại</button><button className="sync-button artifact-download-button vat-export-button" type="button" disabled={!selectedConnectionIds.length || coverageState === 'loading' || exporting} onClick={() => void prepareExport()}><DownloadIcon />{exporting ? 'Đang xuất tờ khai…' : 'Xuất tờ khai thuế GTGT'}</button></div>
    </section>
    <section className="artifact-account-content">
      <div className="artifact-account-filters filters"><div className="filters-left"><label className="search-box"><img src={searchIcon} alt="" /><input aria-label="Tìm kiếm tài khoản tờ khai" placeholder="Tìm kiếm MST, Tên công ty..." value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} /></label><select className="status-filter" aria-label="Lọc trạng thái tờ khai" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value as StatusFilter); setPage(1); }}><option value="">Tất cả trạng thái</option><option value="checking">Đang kiểm tra</option><option value="ready">Đã đồng bộ</option><option value="not_ready">Chưa đồng bộ</option><option value="error">Không thể kiểm tra</option></select></div></div>
      <div className="artifact-account-table vat-return-table data-card">
        <div className="artifact-account-row artifact-account-row--head table-header table-grid"><button className="selection-button" type="button" aria-label="Chọn tất cả tài khoản đã lọc" onClick={() => onSelectAccounts(selectedFiltered.length === filteredIds.length ? selectedConnectionIds.filter((id) => !filteredIds.includes(id)) : [...new Set([...selectedConnectionIds, ...filteredIds])])}><SelectionBox checked={filteredIds.length > 0 && selectedFiltered.length === filteredIds.length} indeterminate={selectedFiltered.length > 0 && selectedFiltered.length < filteredIds.length} /></button><span>MST</span><span>Tên công ty</span><span className="vat-coverage-header"><strong>Trạng thái đồng bộ</strong><span><b>Mua vào</b><b>Bán ra</b></span></span><span>Tiến trình</span><span>Xem kết quả</span></div>
        <div className="artifact-account-body table-body">{pageRows.map(({ account, coverage: item }) => <div className="artifact-account-row table-row table-grid" key={account.connection_id}><button className="selection-button" type="button" aria-label={`Chọn ${account.username}`} onClick={() => onSelectAccount(account.connection_id)}><SelectionBox checked={selectedConnectionIds.includes(account.connection_id)} /></button><span>{account.username}</span><strong title={account.company_name ?? ''}>{account.company_name || '—'}</strong><span className="vat-direction-coverages"><CoverageBadge snapshot={item?.purchase} state={coverageState} dateFrom={selection.dateFrom} dateTo={selection.dateTo} /><CoverageBadge snapshot={item?.sold} state={coverageState} dateFrom={selection.dateFrom} dateTo={selection.dateTo} /></span><span className="artifact-row-progress" role="status"><em>{exportStates[account.connection_id] ?? 'Chưa xuất'}</em></span><span className="artifact-row-action row-action-group"><button className="row-result-button" type="button" onClick={() => setIssueAccountId(account.connection_id)}>Xem kết quả</button></span></div>)}</div>
        {coverageState === 'loading' && !pageRows.length ? <div className="artifact-table-state">Đang kiểm tra dữ liệu cục bộ...</div> : null}{coverageState === 'error' ? <div className="artifact-table-state">Không thể kiểm tra coverage tờ khai.</div> : null}{coverageState === 'ready' && !pageRows.length ? <div className="artifact-table-state">Không có tài khoản phù hợp.</div> : null}
      </div>
      <footer className="pagination"><label>Số tài khoản/trang: <select aria-label="Số tài khoản mỗi trang" value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}><option value={10}>10</option><option value={20}>20</option><option value={50}>50</option><option value={100}>100</option><option value={ALL_ACCOUNT_ROWS}>Toàn bộ</option></select></label><span>{`Hiển thị ${filteredRows.length ? start + 1 : 0}–${end} trên tổng ${filteredRows.length} tài khoản`}</span><div><span>Chọn trang:</span><button type="button" aria-label="Trang trước" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>‹</button>{tokens.map((token, index) => token === 'ellipsis' ? <span key={`ellipsis-${index}`}>...</span> : <button type="button" key={token} data-active={currentPage === token} onClick={() => setPage(token)}>{token}</button>)}<button type="button" aria-label="Trang sau" disabled={currentPage === totalPages} onClick={() => setPage(currentPage + 1)}>›</button></div></footer>
    </section>
    {feedback ? <NoticeDialog kind={feedback.kind} message={feedback.message} path={feedback.path} actionLabel={feedback.canContinue ? 'Tiếp tục tải' : undefined} onAction={feedback.canContinue ? () => { setFeedback(null); void prepareExport(true); } : undefined} onClose={() => setFeedback(null)} /> : null}
    {issueAccountId ? <VatIssueDialog account={accounts.find((item) => item.connection_id === issueAccountId)!} selection={selection} onClose={() => setIssueAccountId(null)} /> : null}
  </section>;
}
