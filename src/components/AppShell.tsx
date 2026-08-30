import { useEffect, useRef, useState, type PropsWithChildren } from 'react';
import logo from '../assets/figma/logo.png';
import invoicesIcon from '../assets/figma/nav-invoices.png';
import xmlIcon from '../assets/figma/nav-xml.png';
import materialIcon from '../assets/figma/nav-material.png';
import logsIcon from '../assets/figma/nav-logs.png';
import settingsIcon from '../assets/figma/nav-settings.png';

export type NavigationKey = 'invoices' | 'xml-html' | 'logs' | 'mvt' | 'settings' | 'guide';

interface AppShellProps extends PropsWithChildren {
  active: NavigationKey;
  onNavigate(value: NavigationKey): void;
  showTopbar?: boolean;
}

const navigation = [
  ['invoices', 'Quản lý HĐĐT', invoicesIcon],
  ['xml-html', 'XML/HTML/PDF', xmlIcon],
  ['logs', 'Lịch sử tải xuống', logsIcon],
  ['mvt', 'Tra cứu MVT', materialIcon],
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
    <span className="nav-icon-frame">{key === 'guide' ? <BookIcon /> : <img src={icon} alt="" />}</span>
    <span>{label}</span>
  </button>;
}

function NotificationIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a5 5 0 0 0-5 5v2.7c0 .8-.3 1.6-.8 2.2L4.8 15a1 1 0 0 0 .8 1.6h12.8a1 1 0 0 0 .8-1.6l-1.4-2.1a3.9 3.9 0 0 1-.8-2.2V8a5 5 0 0 0-5-5Zm-2 15.5h4a2 2 0 0 1-4 0Z" /></svg>;
}

function HeadsetIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 13v-2a8 8 0 0 1 16 0v5a3 3 0 0 1-3 3h-3v-2h3a1 1 0 0 0 1-1v-5a6 6 0 0 0-12 0v2H4Zm1 0h2a1 1 0 0 1 1 1v3a1 1 0 0 1-1 1H5a2 2 0 0 1-2-2v-1a2 2 0 0 1 2-2Zm14 0a2 2 0 0 1 2 2v1a2 2 0 0 1-2 2h-2a1 1 0 0 1-1-1v-3a1 1 0 0 1 1-1h2Z" /></svg>;
}

function BookIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H11a2 2 0 0 1 2 2v15.2a3.5 3.5 0 0 0-2-.7H6.5A2.5 2.5 0 0 0 4 21V4.5Zm16 0A2.5 2.5 0 0 0 17.5 2H15a1 1 0 0 0-1 1v16.2a3.5 3.5 0 0 1 2-.7h1.5A2.5 2.5 0 0 1 20 21V4.5Z" /></svg>;
}

function maskedKey(value: string | null) {
  if (!value) return 'Chưa có';
  return `${'•'.repeat(12)}${value.slice(-4)}`;
}

function AccountPopover({ onToast }: {
  onToast(message: string): void;
}) {
  const [canonicalKey, setCanonicalKey] = useState<string | null>(null);
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    void window.miaRuntime?.license?.details().then((value) => setCanonicalKey(value.canonical_key)).catch(() => undefined);
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

  const displayKey = visible ? (revealedKey || 'Chưa có') : maskedKey(canonicalKey);
  return <div className="account-popover" role="dialog" aria-label="Thông tin tài khoản">
    <div className="account-key-block">
      <span>Key xác thực</span>
      <code title={visible ? displayKey : undefined}>{displayKey}</code>
      <div><button type="button" onClick={() => void reveal()}>{visible ? 'Ẩn' : 'Hiện'}</button><button type="button" onClick={() => void copyKey()}>Sao chép</button></div>
    </div>
    <span className="account-version">Phiên bản: <strong>MIA 4.0.1</strong></span>
  </div>;
}

export function AppShell({ active, onNavigate, showTopbar = true, children }: AppShellProps) {
  const [profileOpen, setProfileOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const profile = useRef<HTMLDivElement>(null);

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
      <div className="brand"><div className="brand-row"><img src={logo} alt="" className="brand-logo" /><strong>MIA WT</strong></div><span>Giải pháp tải HDDT hàng loạt</span></div>
      <nav className="app-navigation" aria-label="Chức năng chính">{navigation.map((item) => <NavigationButton key={item[0]} item={item} active={active} onNavigate={onNavigate} />)}</nav>
      <div className="sidebar-bottom">
        <section className="support-card" aria-label="Hỗ trợ khách hàng">
          <span className="support-card-icon"><HeadsetIcon /></span>
          <div><strong>Hỗ trợ tận tâm</strong><span>Chúng tôi luôn sẵn sàng<br />hỗ trợ bạn</span></div>
          <button type="button" onClick={() => void window.miaRuntime?.external?.open('https://chat.zalo.me/')}>Liên hệ ngay</button>
        </section>
        <footer className="sidebar-footer"><span>© 2026 Wetech JSC.</span><span>Phiên bản MIA 4.0.1</span></footer>
      </div>
    </aside>
    <main className="workspace" data-topbar={showTopbar}>
      {showTopbar ? <header className="topbar">
        <div className="topbar-company"><h1 title="CÔNG TY CỔ PHẦN GIẢI PHÁP VÀ CÔNG NGHỆ SỐ WETECH">CÔNG TY CỔ PHẦN GIẢI PHÁP VÀ CÔNG NGHỆ SỐ WETECH</h1><span>Giải pháp số cho doanh nghiệp hiện đại</span></div>
        <div className="topbar-actions">
          <div className="support-hotline"><span className="support-hotline-icon"><HeadsetIcon /></span><div><span>Hỗ trợ khách hàng</span><strong>0383.466.992 - 0865.219.286</strong></div></div>
          <button className="notification-button" type="button" aria-label="Thông báo"><NotificationIcon /></button>
          <div className="profile-menu" ref={profile}>
            <button className="profile-button" type="button" aria-label="Thông tin tài khoản" aria-expanded={profileOpen} onClick={() => setProfileOpen((current) => !current)}>W</button>
            <span className="profile-chevron" aria-hidden="true" />
            {profileOpen ? <AccountPopover onToast={setToast} /> : null}
          </div>
        </div>
      </header> : null}
      <div className="workspace-content">{children}</div>
      {toast ? <div className="shell-toast" role="status">{toast}</div> : null}
    </main>
  </div>;
}
