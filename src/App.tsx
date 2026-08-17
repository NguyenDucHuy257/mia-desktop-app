import { useState } from 'react';
import { AppShell, type NavigationKey } from './components/AppShell';
import { AddAccountPage } from './features/accounts/AddAccountPage';
import { InvoiceManagementPage } from './features/invoices/InvoiceManagementPage';
import { ResultsPage } from './features/results/ResultsPage';

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
  const [resultJobId, setResultJobId] = useState('');

  function navigate(value: NavigationKey) {
    setActive(value);
    setView('navigation');
  }

  return (
    <AppShell
      active={active}
      onNavigate={navigate}
      showTopbar={view === 'navigation'}
    >
      {view === 'add-account' ? (
        <AddAccountPage onBack={() => setView('navigation')} onConnectionCreated={setConnectionId} />
      ) : view === 'results' ? (
        <ResultsPage jobId={resultJobId} onBack={() => setView('navigation')} />
      ) : active === 'invoices' ? (
        <InvoiceManagementPage connectionId={connectionId} onAddAccount={() => setView('add-account')} onViewResults={(jobId) => { setResultJobId(jobId); setView('results'); }} />
      ) : (
        <section className="placeholder-page" aria-label={labels[active]}>
          <h1>{labels[active]}</h1>
          <p>Màn hình này sẽ được chuyển từ frame Figma tương ứng ở phase kế tiếp.</p>
        </section>
      )}
    </AppShell>
  );
}
