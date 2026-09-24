import { ProxyGuideStep } from './ProxyGuideStep';
import { proxyGuideSteps } from './proxy-guide-config';

export function ProxyPurchaseGuide({ onPurchase, opening, error }: { onPurchase(): void; opening: boolean; error: string }) {
  return <section className="proxy-guide-section proxy-purchase-guide" id="proxy-purchase">
    <span className="proxy-section-number">06</span>
    <div className="proxy-section-heading"><h2>Hướng dẫn mua Proxy</h2><p>Làm lần lượt sáu bước dưới đây. Nếu số dư đã đủ, bạn có thể bỏ qua bước nạp tiền.</p></div>
    <div className="proxy-purchase-steps">{proxyGuideSteps.map((step) => <ProxyGuideStep key={step.number} step={step} />)}</div>
    <div className="proxy-purchase-action">
      <button type="button" disabled={opening} onClick={onPurchase}>{opening ? 'Đang mở trình duyệt…' : 'Mở trang đăng ký Proxy'}</button>
      <span>Bạn sẽ được chuyển đến website của nhà cung cấp Proxy bằng trình duyệt mặc định trên máy.</span>
      <small>Đây là liên kết giới thiệu của MIA.</small>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  </section>;
}
