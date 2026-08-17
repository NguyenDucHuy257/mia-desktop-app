import type { PropsWithChildren } from 'react';
import logo from '../assets/figma/logo.png';
import invoicesIcon from '../assets/figma/nav-invoices.png';
import xmlIcon from '../assets/figma/nav-xml.png';
import htmlIcon from '../assets/figma/nav-html.png';
import pdfIcon from '../assets/figma/nav-pdf.png';
import materialIcon from '../assets/figma/nav-material.png';
import logsIcon from '../assets/figma/nav-logs.png';
import settingsIcon from '../assets/figma/nav-settings.png';
import userIcon from '../assets/figma/user.png';

export type NavigationKey =
  | 'invoices'
  | 'xml'
  | 'html'
  | 'pdf'
  | 'materials'
  | 'logs'
  | 'settings';

interface AppShellProps extends PropsWithChildren {
  active: NavigationKey;
  onNavigate(value: NavigationKey): void;
  showTopbar?: boolean;
}

const primary = [
  ['invoices', 'Quản lý HDDT', invoicesIcon],
  ['xml', 'XML', xmlIcon],
  ['html', 'HTML', htmlIcon],
  ['pdf', 'PDF', pdfIcon],
] as const;

const secondary = [
  ['materials', 'Mã vật tư', materialIcon],
  ['logs', 'Nhật ký', logsIcon],
  ['settings', 'Cài đặt', settingsIcon],
] as const;

function NavigationButton({
  item,
  active,
  onNavigate,
}: {
  item: readonly [NavigationKey, string, string];
  active: NavigationKey;
  onNavigate(value: NavigationKey): void;
}) {
  const [key, label, icon] = item;
  return (
    <button
      type="button"
      className="nav-button"
      data-active={active === key}
      aria-current={active === key ? 'page' : undefined}
      onClick={() => onNavigate(key)}
    >
      <span className="nav-icon-frame">
        <img src={icon} alt="" />
      </span>
      <span>{label}</span>
    </button>
  );
}

export function AppShell({ active, onNavigate, showTopbar = true, children }: AppShellProps) {
  return (
    <div className="app-frame">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-row">
            <img src={logo} alt="" className="brand-logo" />
            <strong>MIA WT</strong>
          </div>
          <span>Kế toán thông minh</span>
        </div>
        <nav className="primary-nav" aria-label="Chức năng chính">
          {primary.map((item) => (
            <NavigationButton key={item[0]} item={item} active={active} onNavigate={onNavigate} />
          ))}
        </nav>
        <nav className="secondary-nav" aria-label="Tiện ích">
          {secondary.map((item) => (
            <NavigationButton key={item[0]} item={item} active={active} onNavigate={onNavigate} />
          ))}
        </nav>
      </aside>
      <main className="workspace" data-topbar={showTopbar}>
        {showTopbar ? (
          <header className="topbar">
            <h1>CÔNG TY GIẢI PHÁP SỐ WETECH - MIA WT</h1>
            <button className="profile-button" type="button" aria-label="Tài khoản người dùng">
              <img src={userIcon} alt="" />
            </button>
          </header>
        ) : null}
        {children}
      </main>
    </div>
  );
}
