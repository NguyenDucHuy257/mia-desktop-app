import { useEffect, useMemo, useRef, useState } from 'react';
import { AppShell, type NavigationKey } from './components/AppShell';
import { AddAccountPage } from './features/accounts/AddAccountPage';
import { InvoiceManagementPage } from './features/invoices/InvoiceManagementPage';
import { createAccountConnectionGateway } from './features/accounts/account-gateway';
import { useBatchJobLifecycle } from './features/jobs/use-batch-job-lifecycle';
import type { AccountConnection, InvoiceDirection } from './lib/api/contracts';
import { ResultsPage } from './features/results/ResultsPage';
import { useResultExportLifecycle } from './features/results/use-result-export-lifecycle';
import { UtilityPage } from './features/artifacts/ArtifactPages';
import { XmlHtmlPage, type ArtifactSelectionState } from './features/artifacts/XmlHtmlPage';
import { useArtifactDownloadLifecycle } from './features/artifacts/use-artifact-download-lifecycle';
import { VatReturnExportPage } from './features/artifacts/VatReturnExportPage';
import { LicenseGate } from './features/licensing/LicenseGate';
import { OfflineAuthGate } from './features/offline-auth/OfflineAuthGate';
import { currentYearDateRange } from './components/date-input-utils';
import type { WorkspaceTask } from './lib/workspace-task';
import { diagnosticLog } from './lib/diagnostic-logger';
import './styles/delete-progress.css';
import './styles/invoice-storage-polish.css';
import './styles/result-export-progress.css';
import './styles/invoice-unified-controls.css';

const DEFAULT_EXPORT_FOLDER = 'C:\\MIACrawl\\Export\\PDF\\T10_2023';

const labels: Record<Exclude<NavigationKey, 'invoices'>, string> = {
  'xml-html': 'XML/HTML',
  'vat-return': 'Xuất tờ khai thuế GTGT',
  'pdf-lookup': 'Tra cứu PDF gốc',
  mvt: 'Tra cứu MVT',
  logs: 'Lịch sử tải xuống',
  settings: 'Cài đặt hệ thống',
  guide: 'Hướng dẫn sử dụng',
};

type DeleteProgress = {
  active: boolean;
  total: number;
  completed: number;
  failed: number;
};

function DeleteProgressPopup({ progress }: { progress: DeleteProgress }) {
  if (!progress.active || progress.total < 1) return null;
  const current = Math.min(progress.completed + progress.failed + 1, progress.total);
  return (
    <div className="delete-progress-popup" role="status" aria-live="polite">
      <span className="delete-progress-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" focusable="false">
          <path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-2 6h10l-.7 11H7.7L7 9Zm3 2v7h2v-7h-2Zm4 0v7h2v-7h-2Z" />
        </svg>
      </span>
      <span className="delete-progress-copy">
        <strong>Đang xóa {current}/{progress.total} tài khoản</strong>
        <span>Đang dọn job, log, cơ sở dữ liệu và dữ liệu tải xuống…</span>
      </span>
      <span className="delete-progress-spinner" aria-hidden="true" />
    </div>
  );
}

function WorkspaceApp() {
  const defaultDateRange = useMemo(() => currentYearDateRange(), []);
  const [active, setActive] = useState<NavigationKey>('invoices');
  const [view, setView] = useState<'navigation' | 'add-account' | 'results'>('navigation');
  const [connectionId, setConnectionId] = useState('');
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  const [accounts, setAccounts] = useState<AccountConnection[] | null>(null);
  const [exportFolder, setExportFolder] = useState(DEFAULT_EXPORT_FOLDER);
  const [pdfConcurrency, setPdfConcurrency] = useState(5);
  const [artifactSelection, setArtifactSelection] = useState<ArtifactSelectionState>({ ...defaultDateRange, direction: 'purchase' });
  const [resultRange, setResultRange] = useState<{ dateFrom: string; dateTo: string; direction: InvoiceDirection } | null>(null);
  const [deleteProgress, setDeleteProgress] = useState<DeleteProgress>({ active: false, total: 0, completed: 0, failed: 0 });
  const [vatReturnExporting, setVatReturnExporting] = useState(false);
  const gateway = useMemo(() => createAccountConnectionGateway(), []);
  const invoiceJobs = useBatchJobLifecycle();
  const resultExports = useResultExportLifecycle();
  const artifactDownloads = useArtifactDownloadLifecycle();
  const activeWorkspaceTask: WorkspaceTask | null = invoiceJobs.active
    ? 'sync'
    : artifactDownloads.active
      ? 'artifact-download'
      : resultExports.active
        ? 'result-export'
        : vatReturnExporting
          ? 'vat-return-export'
          : null;
  const deleteQueue = useRef<string[]>([]);
  const deletingIds = useRef(new Set<string>());
  const deleteWorkerActive = useRef(false);
  const preferenceWrite = useRef<Promise<void>>(Promise.resolve());

  function updateExportFolder(value: string) {
    setExportFolder(value);
    const preferences = window.miaRuntime?.preferences;
    if (!preferences) return;
    preferenceWrite.current = preferenceWrite.current
      .catch(() => undefined)
      .then(async () => {
        const current = await preferences.get();
        await preferences.set({ ...current, exportFolder: value });
      });
  }

  async function refreshAccounts(): Promise<boolean> {
    try {
      const items = await gateway.list();
      const visibleItems = items.filter((item) => !deletingIds.current.has(item.connection_id));
      setAccounts(visibleItems);
      setConnectionId((current) => current || visibleItems[0]?.connection_id || '');
      setSelectedAccountIds((current) => {
        const available = new Set(visibleItems.map((item) => item.connection_id));
        const retained = current.filter((id) => available.has(id));
        return retained.length ? retained : visibleItems[0] ? [visibleItems[0].connection_id] : [];
      });
      return true;
    } catch (error) {
      setAccounts(null);
      diagnosticLog('initial_account_list_failed', {
        code: (error as { code?: string })?.code ?? 'unknown_error',
      }, 'warn');
      return false;
    }
  }

  async function drainDeleteQueue() {
    if (deleteWorkerActive.current) return;
    deleteWorkerActive.current = true;
    try {
      while (deleteQueue.current.length > 0) {
        const id = deleteQueue.current[0];
        try {
          await gateway.revoke(id);
          setDeleteProgress((current) => ({ ...current, completed: current.completed + 1 }));
        } catch {
          setDeleteProgress((current) => ({ ...current, failed: current.failed + 1 }));
        } finally {
          deleteQueue.current.shift();
          deletingIds.current.delete(id);
        }
      }
      await refreshAccounts();
    } finally {
      deleteWorkerActive.current = false;
      setDeleteProgress((current) => ({ ...current, active: false }));
      if (deleteQueue.current.length > 0) void drainDeleteQueue();
    }
  }

  function deleteAccount(id: string) {
    if (deletingIds.current.has(id)) return;
    deletingIds.current.add(id);
    deleteQueue.current.push(id);
    setAccounts((current) => current?.filter((item) => item.connection_id !== id) ?? current);
    setSelectedAccountIds((current) => current.filter((value) => value !== id));
    setConnectionId((current) => current === id ? '' : current);
    setDeleteProgress((current) => current.active
      ? { ...current, total: current.total + 1 }
      : { active: true, total: 1, completed: 0, failed: 0 });
    void drainDeleteQueue();
  }

  useEffect(() => {
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const loadInitialAccounts = async (attempt = 0) => {
      const loaded = await refreshAccounts();
      if (disposed || loaded) return;
      // Do not leave the first screen looking empty because the local runtime
      // needed another moment to open its encrypted session/database.
      if (attempt < 9) {
        retryTimer = setTimeout(() => void loadInitialAccounts(attempt + 1), 500);
      }
    };
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') void refreshAccounts();
    };
    void loadInitialAccounts();
    window.addEventListener('focus', refreshWhenVisible);
    document.addEventListener('visibilitychange', refreshWhenVisible);
    void window.miaRuntime?.preferences?.get()
      .then((preferences) => { setExportFolder(preferences.exportFolder || DEFAULT_EXPORT_FOLDER); setPdfConcurrency(preferences.pdfConcurrency ?? 5); })
      .catch(() => undefined);
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      window.removeEventListener('focus', refreshWhenVisible);
      document.removeEventListener('visibilitychange', refreshWhenVisible);
    };
  }, []);

  function navigate(value: NavigationKey) {
    setActive(value);
    setView('navigation');
  }

  return (
    <>
      <AppShell
        active={active}
        onNavigate={navigate}
        showTopbar={view === 'navigation'}
      >
        {view === 'add-account' ? (
          <AddAccountPage gateway={gateway} onBack={() => setView('navigation')} onConnectionCreated={(id) => { setConnectionId(id); void refreshAccounts(); }} />
        ) : view === 'results' ? (
          <ResultsPage
            connectionId={connectionId}
            exportFolder={exportFolder}
            initialDateFrom={resultRange?.dateFrom}
            initialDateTo={resultRange?.dateTo}
            initialDirection={resultRange?.direction}
            crawlItem={invoiceJobs.items[connectionId]}
            resultExports={resultExports}
            activeWorkspaceTask={activeWorkspaceTask}
            onBack={() => setView('navigation')}
          />
        ) : active === 'invoices' ? (
          <InvoiceManagementPage
            jobLifecycle={invoiceJobs}
            resultExports={resultExports}
            activeWorkspaceTask={activeWorkspaceTask}
            accounts={accounts}
            connectionId={connectionId}
            selectedAccountIds={selectedAccountIds}
            exportFolder={exportFolder}
            onExportFolder={updateExportFolder}
            onAddAccount={() => setView('add-account')}
            onDeleteAccount={async (id) => { deleteAccount(id); }}
            onSelectAccount={(id) => setSelectedAccountIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])}
            onSelectAccounts={setSelectedAccountIds}
            onViewResults={(id, dateFrom, dateTo, resultDirection) => { setConnectionId(id); setResultRange({ dateFrom, dateTo, direction: resultDirection }); setView('results'); }}
            initialDateFrom={artifactSelection.dateFrom}
            initialDateTo={artifactSelection.dateTo}
            initialDirection={artifactSelection.direction}
            onDateRangeChange={(dateFrom, dateTo) => setArtifactSelection((current) => ({ ...current, dateFrom, dateTo }))}
            onDirectionChange={(direction) => setArtifactSelection((current) => ({ ...current, direction }))}
          />
        ) : active === 'xml-html' ? <XmlHtmlPage accounts={accounts ?? []} selectedConnectionIds={selectedAccountIds} onSelectAccount={(id) => setSelectedAccountIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} onSelectAccounts={setSelectedAccountIds} folder={exportFolder} onFolder={updateExportFolder} lifecycle={artifactDownloads} selection={artifactSelection} onSelectionChange={setArtifactSelection} coverageRevision={invoiceJobs.coverageRevision} pdfConcurrency={pdfConcurrency} activeWorkspaceTask={activeWorkspaceTask} />
          : active === 'vat-return' ? <VatReturnExportPage accounts={accounts ?? []} selectedConnectionIds={selectedAccountIds} onSelectAccount={(id) => setSelectedAccountIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} onSelectAccounts={setSelectedAccountIds} folder={exportFolder} onFolder={updateExportFolder} selection={{ dateFrom: artifactSelection.dateFrom, dateTo: artifactSelection.dateTo }} onSelectionChange={({ dateFrom, dateTo }) => setArtifactSelection((current) => ({ ...current, dateFrom, dateTo }))} coverageRevision={invoiceJobs.coverageRevision} activeWorkspaceTask={activeWorkspaceTask} onExportingChange={setVatReturnExporting} />
          : <UtilityPage title={labels[active]} description={active === 'mvt' || active === 'pdf-lookup' || active === 'logs' || active === 'guide' ? '' : 'Thiết lập ứng dụng MIA TOOL 2026.'} onPdfConcurrencyChange={setPdfConcurrency} />}
      </AppShell>
      <DeleteProgressPopup progress={deleteProgress} />
    </>
  );
}

export default function App() {
  return <LicenseGate><OfflineAuthGate><WorkspaceApp /></OfflineAuthGate></LicenseGate>;
}
