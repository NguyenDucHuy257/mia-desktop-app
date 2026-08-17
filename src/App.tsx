import { useState } from 'react';
import { AppShell, type NavigationKey } from './components/AppShell';
import { InvoiceManagementPage } from './features/invoices/InvoiceManagementPage';

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

  return (
    <AppShell active={active} onNavigate={setActive}>
      {active === 'invoices' ? (
        <InvoiceManagementPage />
      ) : (
        <section className="placeholder-page" aria-label={labels[active]}>
          <h1>{labels[active]}</h1>
          <p>Màn hình này sẽ được chuyển từ frame Figma tương ứng ở phase kế tiếp.</p>
        </section>
      )}
    </AppShell>
  );
}
