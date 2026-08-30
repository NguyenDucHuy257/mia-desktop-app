import { useEffect, useMemo, useRef, useState, type PropsWithChildren } from 'react';
import packageInfo from '../../package.json';
import logo from '../assets/figma/logo.png';
import invoicesIcon from '../assets/figma/nav-invoices.png';
import xmlIcon from '../assets/figma/nav-xml.png';
import materialIcon from '../assets/figma/nav-material.png';
import logsIcon from '../assets/figma/nav-logs.png';
import settingsIcon from '../assets/figma/nav-settings.png';

export type NavigationKey = 'invoices' | 'xml-html' | 'logs' | 'materials' | 'settings' | 'guide';

export interface ShellAccount {
  companyName: string | null;
  taxCode: string | null;
}

interface AppShellProps extends PropsWithChildren {
  active: NavigationKey;
  account?: ShellAccount | null;
  onNavigate(value: NavigationKey): void;
  showTopbar?: boolean;
}

const navigation = [
  ['invoices', 'Quản lý HĐĐT', invoicesIcon],
  ['xml-html', 'XML/HTML/PDF', xmlIcon],
  ['logs', 'Lịch sử tải xuống', logsIcon],
  ['materials', 'Danh sách MST', materialIcon],
  ['settings', 'Cài đặt hệ thống', settingsIcon],
  ['guide', 'Hướng dẫn sử dụng', settingsIcon],
] as const;

function NavigationButton({ item, active, onNavigate }: {
  item: readonly [NavigationKey, string, string];
  active: NavigationKey;
  onNavigate(value: NavigationKey): void;
}) {
  const [key, label, icon] = item;
  return <button type="button" className="nav-button" data-active={active === key} aria-current={active === key ? 'page' : undefined} onClick={() => onNavigate(key)}>
    <span className="nav-icon-frame"><img src={icon} alt="" /></span>
    <span>{label}</span>
  </button>;
}

function NotificationIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a5 5 0 0 0-5 5v2.7c0 .8-.3 1.6-.8 2.2L4.8 15a1 1 0 0 0 .8 1.6h12.8a1 1 0 0 0 .8-1.6l-1.4-2.1a3.9 3.9 0 0 1-.8-2.2V8a5 5 0 0 0-5-5Zm-2 15.5h4a2 2 0 0 1-4 0Z" /></svg>;
}

function maskedKey(value: string | null) {
  if (!value) return 'Chưa có';
  return `${'•'.repeat(12)}${value.slice(-4)}`;
}

function AccountPopover({ account, onClose, onToast }: {
  account: ShellAccount | null;
  onClose(): void;
  onToast(message: string): void;
}) {
  const [details, setDetails] = useState<{ phone: string | null; canonical_key: string | null }>({ phone: null, canonical_key: null });
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    void window.miaRuntime?.license?.details().then((value) => setDetails({ phone: value.phone, canonical_key: value.canonical_key })).catch(() => undefined);
  }, []);

  async function reveal() {
    if (visible) { setVisible(false); return; }
    try {
      const key = await window.miaRuntime?.license?.revealKey();
      setRevealedKey(key || null);
      setVisible(true);
    } catch { onToast('Không thể đọc key xác thực.'); }
  }

  async function copyKey() {
    try {
      const key = revealedKey || await window.miaRuntime?.license?.revealKey();
      if (!key) throw new Error('key_unavailable');
      await navigator.clipboard.writeText(key);
      onToast('Đã sao chép key xác thực');
    } catch { onToast('Không thể sao chép key xác thực.'); }
  }

  const displayKey = visible ? (revealedKey || 'Chưa có') : maskedKey(details.canonical_key);
  return <div className="account-popover" role="dialog" aria-label="Thông tin tài khoản">
    <div className="account-popover-heading">
      <strong title={account?.companyName || 'MIA WT'}>{account?.companyName || 'MIA WT'}</strong>
      {account?.taxCode ? <span>MST: {account.taxCode}</span> : null}
      {details.phone ? <span>SĐT: {details.phone}</span> : null}
    </div>
    <div className="account-key-block">
      <span>Key xác thực</span>
      <code title={visible ? displayKey : undefined}>{displayKey}</code>
      <div><button type="button" onClick={() => void reveal()}>{visible ? 'Ẩn' : 'Hiện'}</button><button type="button" onClick={() => void copyKey()}>Sao chép</button></div>
    </div>
    <span className="account-version">Phiên bản: {packageInfo.version}</span>
    <button className="account-logout" type="button" disabled title="MIA không có phiên đăng nhập ứng dụng chung">Đăng xuất</button>
    <button className="account-popover-close" type="button" aria-label="Đóng thông tin tài khoản" onClick={onClose}>×</button>
  </div>;
}

export function AppShell({ active, account = null, onNavigate, showTopbar = true, children }: AppShellProps) {
  const [profileOpen, setProfileOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const profile = useRef<HTMLDivElement>(null);
  const companyName = account?.companyName || 'MIA WT';
  const avatarText = useMemo(() => companyName.trim().charAt(0).toLocaleUpperCase('vi') || 'M', [companyName]);

  useEffect(() => {
    if (!profileOpen) return;
    const onPointerDown = (event: MouseEvent) => { if (!profile.current?.contains(event.target as Node)) setProfileOpen(false); };
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setProfileOpen(false); };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => { document.removeEventListener('mousedown', onPointerDown); document.removeEventListener('keydown', onKeyDown); };
  }, [profileOpen]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  return <div className="app-frame">
    <aside className="sidebar">
      <div className="brand"><div className="brand-row"><img src={logo} alt="" className="brand-logo" /><strong>MIA WT</strong></div><span>Kế toán thông minh</span></div>
      <nav className="app-navigation" aria-label="Chức năng chính">{navigation.map((item) => <NavigationButton key={item[0]} item={item} active={active} onNavigate={onNavigate} />)}</nav>
      <section className="support-card" aria-label="Hỗ trợ khách hàng">
        <strong>Hỗ trợ tận tâm</strong><span>Chúng tôi luôn sẵn sàng hỗ trợ bạn</span>
        <button type="button" onClick={() => void window.miaRuntime?.external?.open('https://chat.zalo.me/')}>Liên hệ ngay</button>
      </section>
    </aside>
    <main className="workspace" data-topbar={showTopbar}>
      {showTopbar ? <header className="topbar">
        <div className="topbar-company"><h1 title={companyName}>{companyName}</h1><span>Giải pháp số cho doanh nghiệp hiện đại</span></div>
        <div className="topbar-actions">
          <div className="support-hotline"><span>Hỗ trợ khách hàng</span><strong>0865 219 286 · 0383 466 992</strong></div>
          <button className="notification-button" type="button" aria-label="Thông báo"><NotificationIcon /></button>
          <div className="profile-menu" ref={profile}>
            <button className="profile-button" type="button" aria-label="Thông tin tài khoản" aria-expanded={profileOpen} onClick={() => setProfileOpen((current) => !current)}>{avatarText}</button>
            {profileOpen ? <AccountPopover account={account} onClose={() => setProfileOpen(false)} onToast={setToast} /> : null}
          </div>
        </div>
      </header> : null}
      <div className="workspace-content">{children}</div>
      {toast ? <div className="shell-toast" role="status">{toast}</div> : null}
    </main>
  </div>;
}
