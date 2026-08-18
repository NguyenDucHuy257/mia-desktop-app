import { useEffect, useMemo, useState } from 'react';
import { AppShell, type NavigationKey } from './components/AppShell';
import { AddAccountPage } from './features/accounts/AddAccountPage';
import { InvoiceManagementPage } from './features/invoices/InvoiceManagementPage';
import { createAccountConnectionGateway } from './features/accounts/account-gateway';
import type { AccountConnection } from './lib/api/contracts';

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
  const [view, setView] = useState<'navigation' | 'add-account'>('navigation');
  const [connectionId, setConnectionId] = useState('');
  const [accounts, setAccounts] = useState<AccountConnection[] | null>(null);
  const gateway = useMemo(() => createAccountConnectionGateway(), []);

  async function refreshAccounts() {
    try {
      const items = await gateway.list();
      setAccounts(items);
      setConnectionId((current) => current || items[0]?.connection_id || '');
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
      showTopbar={view !== 'add-account'}
    >
      {view === 'add-account' ? (
        <AddAccountPage gateway={gateway} onBack={() => setView('navigation')} onConnectionCreated={(id) => { setConnectionId(id); void refreshAccounts(); }} />
      ) : active === 'invoices' ? (
        <InvoiceManagementPage accounts={accounts} connectionId={connectionId} onAddAccount={() => setView('add-account')} onDeleteAccount={async (id) => { await gateway.revoke(id); if (connectionId === id) setConnectionId(''); await refreshAccounts(); }} onSelectAccount={setConnectionId} />
      ) : (
        <section className="placeholder-page" aria-label={labels[active]}>
          <h1>{labels[active]}</h1>
          <p>Màn hình này sẽ được chuyển từ frame Figma tương ứng ở phase kế tiếp.</p>
        </section>
      )}
    </AppShell>
  );
}
