import { type FormEvent, type PropsWithChildren, useEffect, useState } from 'react';
import logo from '../../assets/figma/logo.png';
import type { LicenseStateResponse } from '../../lib/runtime-bridge';
import '../../styles/license.css';

const copy: Record<string, { title: string; description: string }> = {
  checking: { title: 'Đang kiểm tra bản quyền', description: 'Đang nhận diện thiết bị và kiểm tra trạng thái sử dụng...' },
  migrating: { title: 'Đang nâng cấp bản quyền', description: 'MIA đang nhận diện bản quyền hiện tại. Bạn không cần nhập lại key.' },
  expired: { title: 'Bản quyền đã hết hạn', description: 'Vui lòng liên hệ bộ phận hỗ trợ để gia hạn sử dụng.' },
  revoked: { title: 'Bản quyền đã bị thu hồi', description: 'Vui lòng liên hệ bộ phận hỗ trợ để kiểm tra trạng thái bản quyền.' },
  verification_required: { title: 'Cần xác minh thêm', description: 'MIA đã tìm thấy nhiều hồ sơ bản quyền phù hợp. Vui lòng liên hệ hỗ trợ để xác minh.' },
  error: { title: 'Không thể kiểm tra bản quyền', description: 'Vui lòng kiểm tra kết nối và thử lại. Dữ liệu bản quyền trên máy vẫn được giữ nguyên.' },
};

function LicenseFrame({ state, onRetry }: { state: LicenseStateResponse; onRetry(): Promise<void> }) {
  const content = copy[state.state] || copy.error;
  const retryable = ['error', 'expired', 'revoked', 'verification_required'].includes(state.state);
  return <main className="license-gate-page">
    <section className="license-gate-card" role="status" aria-live="polite">
      <img src={logo} alt="" className="license-gate-logo" />
      <strong className="license-gate-brand">MIA WT</strong>
      <h1>{content.title}</h1>
      <p>{content.description}</p>
      {state.state === 'checking' || state.state === 'migrating' ? <div className="license-progress" aria-hidden="true"><span /></div> : null}
      {retryable ? <button type="button" className="license-primary-button" onClick={() => void onRetry()}>Kiểm tra lại</button> : null}
      {state.reason ? <small>Mã trạng thái: {state.reason}</small> : null}
    </section>
  </main>;
}

function PhoneForm({ onSubmit }: { onSubmit(phone: string): Promise<void> }) {
  const [phone, setPhone] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const normalizedPhone = phone.trim();
    if (!/^0[0-9]{9}$/.test(normalizedPhone) || normalizedPhone === '0000000000') {
      setMessage('Vui lòng nhập số điện thoại hợp lệ gồm 10 chữ số và bắt đầu bằng 0.');
      return;
    }
    setSubmitting(true);
    setMessage(null);
    try { await onSubmit(normalizedPhone); }
    catch { setMessage('Không thể gửi thông tin bản quyền. Vui lòng thử lại.'); }
    finally { setSubmitting(false); }
  }
  return <main className="license-gate-page"><form className="license-gate-card" onSubmit={(event) => void submit(event)}>
    <img src={logo} alt="" className="license-gate-logo" />
    <strong className="license-gate-brand">MIA WT</strong>
    <h1>Số điện thoại</h1>
    <p>Số điện thoại được dùng để quản lý bản quyền và hỗ trợ khôi phục thiết bị.</p>
    <label className="license-phone-field">Số điện thoại
      <input autoFocus inputMode="numeric" autoComplete="tel" maxLength={10} placeholder="Ví dụ: 0981234567" value={phone} onChange={(event) => setPhone(event.target.value.replace(/\D/g, '').slice(0, 10))} />
    </label>
    {message ? <span className="license-form-error">{message}</span> : null}
    <button type="submit" className="license-primary-button" disabled={submitting}>{submitting ? 'Đang xử lý...' : 'Tiếp tục'}</button>
  </form></main>;
}

function ActivationPage({ state, onRetry }: { state: LicenseStateResponse; onRetry(): Promise<void> }) {
  const key = state.activation_key || '';
  const [copied, setCopied] = useState(false);
  async function copyKey() {
    if (!key) return;
    await navigator.clipboard.writeText(key);
    setCopied(true);
  }
  return <main className="license-gate-page"><section className="license-gate-card">
    <img src={logo} alt="" className="license-gate-logo" />
    <strong className="license-gate-brand">MIA WT</strong>
    <h1>Thiết bị chưa được kích hoạt</h1>
    <p>Gửi mã dưới đây cho bộ phận hỗ trợ. Mã này được giữ ổn định khi bạn mở lại ứng dụng.</p>
    <div className="license-activation-key"><span>Mã kích hoạt</span><code>{key || 'Đang tạo mã...'}</code></div>
    <div className="license-gate-actions">
      <button type="button" className="license-secondary-button" disabled={!key} onClick={() => void copyKey()}>{copied ? 'Đã sao chép' : 'Sao chép mã'}</button>
      <button type="button" className="license-primary-button" onClick={() => void onRetry()}>Kiểm tra lại</button>
    </div>
  </section></main>;
}

export function LicenseGate({ children }: PropsWithChildren) {
  const bridge = typeof window === 'undefined' ? undefined : window.miaRuntime?.license;
  const [state, setState] = useState<LicenseStateResponse | null>(bridge ? null : { state: 'active', active: true, mode: 'browser' });
  async function initialize() {
    if (!bridge) return;
    setState((current) => current?.active ? current : { state: 'checking', active: false });
    try { setState(await bridge.initialize()); }
    catch (error) { setState({ state: 'error', active: false, reason: String((error as { code?: string })?.code || 'internal_error') }); }
  }
  async function submitPhone(phone: string) {
    if (!bridge) return;
    setState(await bridge.submitPhone(phone));
  }
  useEffect(() => { void initialize(); }, []);
  if (!state || state.state === 'checking' || state.state === 'migrating') return <LicenseFrame state={state || { state: 'checking', active: false }} onRetry={initialize} />;
  if (state.active) return <>{children}</>;
  if (state.state === 'phone_required') return <PhoneForm onSubmit={submitPhone} />;
  if (state.state === 'activation_required') return <ActivationPage state={state} onRetry={async () => { if (bridge) setState(await bridge.retry()); }} />;
  return <LicenseFrame state={state} onRetry={initialize} />;
}
