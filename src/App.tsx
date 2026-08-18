import { useEffect, useMemo, useState } from 'react';
import { AppShell, type NavigationKey } from './components/AppShell';
import { AddAccountPage } from './features/accounts/AddAccountPage';
import { InvoiceManagementPage } from './features/invoices/InvoiceManagementPage';
import { createAccountConnectionGateway } from './features/accounts/account-gateway';
import type { AccountConnection } from './lib/api/contracts';
import { ResultsPage } from './features/results/ResultsPage';
import { ArtifactDownloaderPage, PdfDownloaderPage, UtilityPage } from './features/artifacts/ArtifactPages';

const labels: Record<Exclude<NavigationKey, 'invoices'>, string> = {
  xml: 'XML Downloader',
  html: 'HTML Downloader',
  pdf: 'PDF Downloader',
  materials: 'Mã vật tư',
  logs: 'Nhật ký',
  settings: 'Cài đặt',
};

export default function App() {
  const [active, setActive] = useState<NavigationKey>('invoices');
  const [view, setView] = useState<'navigation' | 'add-account' | 'results'>('navigation');
  const [connectionId, setConnectionId] = useState('');
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  const [accounts, setAccounts] = useState<AccountConnection[] | null>(null);
  const [exportFolder, setExportFolder] = useState('C:\\MIACrawl\\Export\\PDF\\T10_2023');
  const gateway = useMemo(() => createAccountConnectionGateway(), []);

  async function refreshAccounts() {
    try {
      const items = await gateway.list();
      setAccounts(items);
      setConnectionId((current) => current || items[0]?.connection_id || '');
      setSelectedAccountIds((current) => {
        const available = new Set(items.map((item) => item.connection_id));
        const retained = current.filter((id) => available.has(id));
        return retained.length ? retained : items[0] ? [items[0].connection_id] : [];
      });
    } catch {
      setAccounts(null);
    }
  }

  useEffect(() => { void refreshAccounts(); }, []);

  function navigate(value: NavigationKey) {
    setActive(value);
    setView('navigation');
  }

  return (
    <AppShell
      active={active}
      onNavigate={navigate}
      showTopbar={view === 'navigation' && active === 'invoices'}
    >
      {view === 'add-account' ? (
        <AddAccountPage gateway={gateway} onBack={() => setView('navigation')} onConnectionCreated={(id) => { setConnectionId(id); void refreshAccounts(); }} />
      ) : view === 'results' ? (
        <ResultsPage connectionId={connectionId} onBack={() => setView('navigation')} />
      ) : active === 'invoices' ? (
        <InvoiceManagementPage accounts={accounts} connectionId={connectionId} selectedAccountIds={selectedAccountIds} onAddAccount={() => setView('add-account')} onDeleteAccount={async (id) => { await gateway.revoke(id); if (connectionId === id) setConnectionId(''); await refreshAccounts(); }} onSelectAccount={(id) => setSelectedAccountIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id])} onSelectAccounts={setSelectedAccountIds} onViewResults={(id) => { setConnectionId(id); setView('results'); }} />
      ) : active === 'xml' ? <ArtifactDownloaderPage kind="xml" folder={exportFolder} onFolder={setExportFolder} connectionIds={selectedAccountIds} />
        : active === 'html' ? <ArtifactDownloaderPage kind="html" folder={exportFolder} onFolder={setExportFolder} connectionIds={selectedAccountIds} />
          : active === 'pdf' ? <PdfDownloaderPage folder={exportFolder} onFolder={setExportFolder} connectionIds={selectedAccountIds} />
            : <UtilityPage title={labels[active]} description={active === 'materials' ? 'Quản lý danh mục mã vật tư.' : active === 'logs' ? 'Theo dõi lịch sử hoạt động cục bộ.' : 'Thiết lập ứng dụng MIA WT.'} />}
    </AppShell>
  );
}
