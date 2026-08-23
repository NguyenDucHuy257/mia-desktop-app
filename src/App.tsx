import { useEffect, useMemo, useRef, useState } from 'react';
import { AppShell, type NavigationKey } from './components/AppShell';
import { AddAccountPage } from './features/accounts/AddAccountPage';
import { InvoiceManagementPage } from './features/invoices/InvoiceManagementPage';
import { createAccountConnectionGateway } from './features/accounts/account-gateway';
import { useBatchJobLifecycle } from './features/jobs/use-batch-job-lifecycle';
import type { AccountConnection } from './lib/api/contracts';
import { ResultsPage } from './features/results/ResultsPage';
import { useResultExportLifecycle } from './features/results/use-result-export-lifecycle';
import { UtilityPage } from './features/artifacts/ArtifactPages';
import { XmlHtmlPage, type ArtifactSelectionState } from './features/artifacts/XmlHtmlPage';
import { useArtifactDownloadLifecycle } from './features/artifacts/use-artifact-download-lifecycle';
import './styles/delete-progress.css';
import './styles/invoice-storage-polish.css';
import './styles/result-export-progress.css';

const DEFAULT_EXPORT_FOLDER = 'C:\\MIACrawl\\Export\\PDF\\T10_2023';

const labels: Record<Exclude<NavigationKey, 'invoices'>, string> = {
  'xml-html': 'XML/HTML',
  materials: 'Mã vật tư',
  logs: 'Nhật ký',
  settings: 'Cài đặt',
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

export default function App() {
  const [active, setActive] = useState<NavigationKey>('invoices');
  const [view, setView] = useState<'navigation' | 'add-account' | 'results'>('navigation');
  const [connectionId, setConnectionId] = useState('');
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  const [accounts, setAccounts] = useState<AccountConnection[] | null>(null);
  const [exportFolder, setExportFolder] = useState(DEFAULT_EXPORT_FOLDER);
  const [pdfConcurrency, setPdfConcurrency] = useState(5);
  const [artifactSelection, setArtifactSelection] = useState<ArtifactSelectionState>({ dateFrom: '2023-10-01', dateTo: '2023-10-31', directions: ['purchase', 'sold'] });
  const [resultRange, setResultRange] = useState<{ dateFrom: string; dateTo: string } | null>(null);
  const [deleteProgress, setDeleteProgress] = useState<DeleteProgress>({ active: false, total: 0, completed: 0, failed: 0 });
  const gateway = useMemo(() => createAccountConnectionGateway(), []);
  const invoiceJobs = useBatchJobLifecycle();
  const resultExports = useResultExportLifecycle();
  const artifactDownloads = useArtifactDownloadLifecycle();
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

  async function refreshAccounts() {
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
    } catch {
      setAccounts(null);
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
    void refreshAccounts();
    void window.miaRuntime?.preferences?.get()
      .then((preferences) => { setExportFolder(preferences.exportFolder || DEFAULT_EXPORT_FOLDER); setPdfConcurrency(preferences.pdfConcurrency ?? 5); })
      .catch(() => undefined);
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
        showTopbar={view === 'navigation' && active === 'invoices'}
      >
        {view === 'add-account' ? (
          <AddAccountPage gateway={gateway} onBack={() => setView('navigation')} onConnectionCreated={(id) => { setConnectionId(id); void refreshAccounts(); }} />
        ) : view === 'results' ? (
          <ResultsPage
            connectionId={connectionId}
            exportFolder={exportFolder}
            initialDateFrom={resultRange?.dateFrom}
            initialDateTo={resultRange?.dateTo}
            crawlItem={invoiceJobs.items[connectionId]}
            resultExports={resultExports}
            onBack={() => setView('navigation')}
          />
        ) : active === 'invoices' ? (
          <InvoiceManagementPage
            jobLifecycle={invoiceJobs}
            resultExports={resultExports}
            accounts={accounts}
            connectionId={connectionId}
            selectedAccountIds={selectedAccountIds}
            exportFolder={exportFolder}
            onExportFolder={updateExportFolder}
            onAddAccount={() => setView('add-account')}
            onDeleteAccount={async (id) => { deleteAccount(id); }}
            onSelectAccount={(id) => setSelectedAccountIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])}
            onSelectAccounts={setSelectedAccountIds}
            onViewResults={(id, dateFrom, dateTo) => { setConnectionId(id); setResultRange({ dateFrom, dateTo }); setView('results'); }}
            initialDateFrom={artifactSelection.dateFrom}
            initialDateTo={artifactSelection.dateTo}
            onDateRangeChange={(dateFrom, dateTo) => setArtifactSelection((current) => ({ ...current, dateFrom, dateTo }))}
          />
        ) : active === 'xml-html' ? <XmlHtmlPage accounts={accounts ?? []} selectedConnectionIds={selectedAccountIds} onSelectAccount={(id) => setSelectedAccountIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} onSelectAccounts={setSelectedAccountIds} folder={exportFolder} onFolder={updateExportFolder} lifecycle={artifactDownloads} selection={artifactSelection} onSelectionChange={setArtifactSelection} pdfConcurrency={pdfConcurrency} />
          : <UtilityPage title={labels[active]} description={active === 'materials' ? 'Quản lý danh mục mã vật tư.' : active === 'logs' ? 'Theo dõi lịch sử hoạt động cục bộ.' : 'Thiết lập ứng dụng MIA WT.'} onPdfConcurrencyChange={setPdfConcurrency} />}
      </AppShell>
      <DeleteProgressPopup progress={deleteProgress} />
    </>
  );
}
