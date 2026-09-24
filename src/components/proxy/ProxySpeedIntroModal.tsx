import { useEffect, useRef, useState } from 'react';
import { ProxyExplanation } from './ProxyExplanation';
import { ProxyPurchaseGuide } from './ProxyPurchaseGuide';
import { ProxyRecommendation } from './ProxyRecommendation';
import proxyRocket from '../../assets/proxy-guide/proxy-rocket.png';
import '../../styles/proxy-speed-intro.css';

const navigation = [
  ['proxy-why', '1. Vì sao phải chờ?'],
  ['proxy-flow', '2. Proxy hoạt động thế nào?'],
  ['proxy-recommendation', '3. Cần mua bao nhiêu?'],
  ['proxy-purchase', '4. Hướng dẫn mua'],
] as const;

export function ProxySpeedIntroModal({ onClose }: { onClose(): void }) {
  const dialogRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key !== 'Tab') return;
      const controls = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [href]') ?? [])];
      if (!controls.length) return;
      const first = controls[0]!;
      const last = controls[controls.length - 1]!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDown);
    dialogRef.current?.querySelector<HTMLElement>('.proxy-modal-close')?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
      previousFocus?.focus();
    };
  }, [onClose]);

  function goTo(id: string) {
    bodyRef.current?.querySelector(`#${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function openPurchasePage() {
    if (opening) return;
    setOpening(true);
    setError('');
    try {
      const opener = window.miaRuntime?.proxyProvider?.openPurchasePage;
      if (!opener) throw Object.assign(new Error('proxy_provider_unavailable'), { code: 'proxy_provider_unavailable' });
      await opener();
    } catch (cause) {
      setError('MIA chưa thể mở trang đăng ký bằng trình duyệt mặc định. Vui lòng thử lại hoặc tải log lỗi để gửi kỹ thuật.');
    } finally {
      setOpening(false);
    }
  }

  return <div className="proxy-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section ref={dialogRef} className="proxy-speed-modal" role="dialog" aria-modal="true" aria-labelledby="proxy-modal-title">
      <header className="proxy-modal-header">
        <div className="proxy-modal-brand"><span aria-hidden="true"><img src={proxyRocket} alt="" /></span><div><h1 id="proxy-modal-title">TĂNG TỐC TẢI NHIỀU MST</h1><p>Hiểu cách tăng số tài khoản MIA có thể tải đồng thời</p></div></div>
        <button className="proxy-modal-close" type="button" onClick={onClose} aria-label="Đóng hướng dẫn">×</button>
      </header>
      <div className="proxy-modal-workspace">
        <nav className="proxy-modal-navigation" aria-label="Nội dung hướng dẫn">
          <span>NỘI DUNG HƯỚNG DẪN</span>
          {navigation.map(([id, label]) => <button type="button" key={id} onClick={() => goTo(id)}>{label}</button>)}
        </nav>
        <div ref={bodyRef} className="proxy-modal-body">
          <main className="proxy-modal-content">
            <ProxyExplanation />
            <ProxyRecommendation />
            <ProxyPurchaseGuide onPurchase={() => void openPurchasePage()} opening={opening} error={error} />
          </main>
        </div>
      </div>
      <footer className="proxy-modal-footer"><span>Thông tin này chỉ giúp bạn lựa chọn số luồng phù hợp; không thay đổi dữ liệu hoặc tác vụ đang chạy.</span><button type="button" onClick={onClose}>Đóng hướng dẫn</button></footer>
    </section>
  </div>;
}
