import { type FormEvent, type PropsWithChildren, useEffect, useState } from 'react';
import logo from '../../assets/figma/logo.png';
import type { LicenseStateResponse } from '../../lib/runtime-bridge';
import '../../styles/license.css';
import { ExportSupportLogButton, InlineErrorWithSupport } from '../../components/ExportSupportLogButton';
import { LicensePolicyContext, LicenseUpdateContext } from './LicensePolicyContext';

const copy: Record<string, { title: string; description: string }> = {
  checking: { title: 'Đang kiểm tra bản quyền', description: 'Đang nhận diện thiết bị và kiểm tra trạng thái sử dụng...' },
  migrating: { title: 'Đang nâng cấp bản quyền', description: 'MIA đang nhận diện bản quyền hiện tại. Bạn không cần nhập lại key.' },
  expired: { title: 'Bản quyền đã hết hạn', description: 'Vui lòng liên hệ bộ phận hỗ trợ để gia hạn sử dụng.' },
  revoked: { title: 'Bản quyền đã bị thu hồi', description: 'Vui lòng liên hệ bộ phận hỗ trợ để kiểm tra trạng thái bản quyền.' },
  verification_required: { title: 'Cần xác minh thêm', description: 'MIA đã tìm thấy nhiều hồ sơ bản quyền phù hợp. Vui lòng liên hệ hỗ trợ để xác minh.' },
  error: { title: 'Không thể kiểm tra bản quyền', description: 'Vui lòng kiểm tra kết nối và thử lại. Dữ liệu bản quyền trên máy vẫn được giữ nguyên.' },
};

const reasonCopy: Record<string, { title: string; description: string }> = {
  client_update_required: {
    title: 'Cần cập nhật MIA TOOL 2026',
    description: 'Key giới hạn VIP/TEST yêu cầu phiên bản 4.0.8 trở lên để bảo đảm đúng phạm vi MST. Vui lòng cài bản mới trước khi tiếp tục.',
  },
  license_policy_missing: {
    title: 'Máy chủ chưa trả quyền sử dụng',
    description: 'Cần cập nhật máy chủ bản quyền để trả giới hạn MST và thời gian. Key đang có vẫn được giữ nguyên, không cần cấp lại.',
  },
  license_policy_invalid: {
    title: 'Cấu hình quyền key chưa hợp lệ',
    description: 'Vui lòng kiểm tra loại key và danh sách MST được cấp phép trên máy chủ. TEST/TEST1 cần 1 MST; TEST2 tối đa 2 MST.',
  },
  hardware_mismatch_below_50_percent: {
    title: 'Thiết bị không khớp bản quyền',
    description: 'Thiết bị không khớp tối thiểu 3 thông tin phần cứng và ít nhất 50% hồ sơ đã lưu. Bản quyền trên thiết bị cũ không thể được sử dụng trên máy này.',
  },
  insufficient_hardware: {
    title: 'Không đủ thông tin thiết bị',
    description: 'Không thể thu thập tối thiểu 3 thông tin phần cứng hợp lệ. Vui lòng thử lại hoặc liên hệ bộ phận hỗ trợ.',
  },
  mia_v2_not_deployed: {
    title: 'Máy chủ chưa hỗ trợ MIA V2',
    description: 'Bản mở rộng tool=MIA chưa được triển khai trên máy chủ bản quyền dùng chung. Dữ liệu bản quyền trên máy vẫn được giữ nguyên.',
  },
  license_network_error: {
    title: 'Không thể kết nối máy chủ bản quyền',
    description: 'Vui lòng kiểm tra kết nối mạng và thử lại. Dữ liệu bản quyền trên máy vẫn được giữ nguyên.',
  },
  license_timeout: {
    title: 'Máy chủ bản quyền phản hồi quá chậm',
    description: 'Vui lòng thử lại sau. Dữ liệu bản quyền trên máy vẫn được giữ nguyên.',
  },
};

export function licenseActionError(error: unknown, fallback: string) {
  const value = error as { code?: string; message?: string; status?: number; requestId?: string };
  const messages: Record<string, string> = {
    recovery_code_invalid: 'Mã xác nhận không đúng hoặc không thuộc yêu cầu hiện tại.',
    recovery_code_expired: 'Mã xác nhận đã hết hạn. Vui lòng gửi mã mới.',
    recovery_code_locked: 'Mã xác nhận đã bị khóa do nhập sai quá số lần.',
    recovery_wait_before_resend: 'Vui lòng chờ ít nhất 60 giây trước khi gửi lại mã.',
    recovery_rate_limited: 'Đã yêu cầu quá nhiều mã. Vui lòng thử lại sau.',
    recovery_email_unavailable: 'Server chưa thể gửi email xác nhận. Vui lòng gửi log cho kỹ thuật.',
    recovery_license_invalid: 'Bản quyền hoặc thiết bị không còn hợp lệ tại bước xác nhận.',
    recovery_not_configured: 'Server chưa cấu hình dịch vụ khôi phục mật khẩu.',
  };
  const detail = messages[value?.code || ''] || value?.message?.replace(/^\[[^\]]+\]\s*/, '') || fallback;
  return value?.requestId ? `${detail} Mã yêu cầu: ${value.requestId}` : detail;
}

export function licenseAllowsWorkspace(state: LicenseStateResponse | null) {
  return Boolean(state
    && state.state === 'active'
    && state.active === true
    && state.valid === true
    && state.expired === false
    && state.reason === 'ok');
}

function LicenseFrame({ state, onRetry }: { state: LicenseStateResponse; onRetry(): Promise<void> }) {
  const content = reasonCopy[state.reason || ''] || copy[state.state] || copy.error;
  const retryable = ['error', 'expired', 'revoked', 'verification_required'].includes(state.state);
  return <main className="license-gate-page">
    <section className="license-gate-card" role="status" aria-live="polite">
      <img src={logo} alt="" className="license-gate-logo" />
      <strong className="license-gate-brand">MIA TOOL 2026</strong>
      <h1>{content.title}</h1>
      <p>{content.description}</p>
      {state.state === 'checking' || state.state === 'migrating' ? <div className="license-progress" aria-hidden="true"><span /></div> : null}
      {retryable ? <button type="button" className="license-primary-button" onClick={() => void onRetry()}>Kiểm tra lại</button> : null}
      {state.reason ? <small>Mã trạng thái: {state.reason}</small> : null}
      {retryable ? <ExportSupportLogButton /> : null}
    </section>
  </main>;
}

function PhoneForm({ legacy = false, initialPhone = '', initialEmail = '', onSubmit }: { legacy?: boolean; initialPhone?: string; initialEmail?: string; onSubmit(phone: string, email: string): Promise<void> }) {
  const [phone, setPhone] = useState(initialPhone);
  const [email, setEmail] = useState(initialEmail);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const normalizedPhone = phone.trim();
    const normalizedEmail = email.trim().toLocaleLowerCase('en-US');
    if (!/^0[0-9]{9}$/.test(normalizedPhone)
      || ['0000000000', '0865219286', '0383466992'].includes(normalizedPhone)) {
      setMessage('Vui lòng nhập số điện thoại hợp lệ gồm 10 chữ số và bắt đầu bằng 0.');
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      setMessage('Vui lòng nhập địa chỉ email hợp lệ để khôi phục mật khẩu.');
      return;
    }
    setSubmitting(true);
    setMessage(null);
    try { await onSubmit(normalizedPhone, normalizedEmail); }
    catch { setMessage('Không thể gửi thông tin bản quyền. Vui lòng thử lại.'); }
    finally { setSubmitting(false); }
  }
  return <main className="license-gate-page"><form className="license-gate-card" onSubmit={(event) => void submit(event)}>
    <img src={logo} alt="" className="license-gate-logo" />
    <strong className="license-gate-brand">MIA TOOL 2026</strong>
    <h1>{legacy ? 'Bổ sung số điện thoại' : 'Kích hoạt bản quyền'}</h1>
    <p>{legacy
      ? 'MIA đã nhận diện bản quyền hiện tại trên thiết bị này. Vui lòng bổ sung số điện thoại để hoàn tất nâng cấp bản quyền. Bạn không cần cấp lại key.'
      : 'Số điện thoại được dùng để quản lý bản quyền và hỗ trợ khôi phục thiết bị.'}</p>
    <label className="license-phone-field">Số điện thoại đăng ký
      <input autoFocus inputMode="numeric" autoComplete="tel" maxLength={10} placeholder="Ví dụ: 0981234567" value={phone} onChange={(event) => setPhone(event.target.value.replace(/\D/g, '').slice(0, 10))} />
    </label>
    <label className="license-phone-field">Email khôi phục
      <input type="email" autoComplete="email" maxLength={254} placeholder="ten@congty.com" required value={email} onChange={(event) => setEmail(event.target.value)} />
    </label>
    {message ? <InlineErrorWithSupport className="license-form-error" message={message} /> : null}
    <button type="submit" className="license-primary-button" disabled={submitting}>{submitting ? 'Đang xử lý...' : 'Cập nhật'}</button>
  </form></main>;
}

function EmailVerificationForm({ state, onDone }: { state: LicenseStateResponse; onDone(value: LicenseStateResponse): void }) {
  const bridge = window.miaRuntime!.license;
  const [phone, setPhone] = useState(state.details?.phone || '');
  const [email, setEmail] = useState(state.details?.pending_email || state.details?.email || '');
  const [challenge, setChallenge] = useState('');
  const [masked, setMasked] = useState('');
  const [code, setCode] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function send(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage('');
    try {
      const result = await bridge.requestContactVerification(phone, email);
      setChallenge(result.challenge_id); setMasked(result.masked_email);
    } catch (error) { setMessage(licenseActionError(error, 'Không thể gửi mã xác nhận.')); }
    finally { setBusy(false); }
  }
  async function confirm(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage('');
    try { onDone(await bridge.confirmContact(challenge, code)); }
    catch (error) { setMessage(licenseActionError(error, 'Không thể xác nhận mã.')); }
    finally { setBusy(false); }
  }
  return <main className="license-gate-page"><form className="license-gate-card" onSubmit={(event) => void (challenge ? confirm(event) : send(event))}>
    <img src={logo} alt="" className="license-gate-logo" /><strong className="license-gate-brand">MIA TOOL 2026</strong>
    <h1>Xác minh email khôi phục</h1>
    {!challenge ? <><p>Email được dùng để nhận mã khi bạn quên mật khẩu đăng nhập trên máy.</p>
      <label className="license-phone-field">Số điện thoại đăng ký<input inputMode="numeric" autoComplete="tel" maxLength={10} value={phone} onChange={(event) => setPhone(event.target.value.replace(/\D/g, '').slice(0, 10))} /></label>
      <label className="license-phone-field">Email khôi phục<input autoFocus type="email" autoComplete="email" maxLength={254} required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
    </> : <><p>Mã gồm 6 chữ số đã được gửi tới <strong>{masked}</strong>. Mã có hiệu lực trong 10 phút.</p>
      <label className="license-phone-field">Mã xác nhận<input autoFocus inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="[0-9]{6}" required value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} /></label>
    </>}
    {message ? <InlineErrorWithSupport className="license-form-error" message={message} /> : null}
    <button type="submit" className="license-primary-button" disabled={busy}>{busy ? 'Đang xử lý...' : challenge ? 'Xác nhận email' : 'Gửi mã xác nhận'}</button>
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
    <strong className="license-gate-brand">MIA TOOL 2026</strong>
    <h1>Mã kích hoạt chưa được cấp quyền</h1>
    <p>Vui lòng gửi mã bên dưới cho bộ phận hỗ trợ để kích hoạt phần mềm trên thiết bị này.</p>
    <div className="license-activation-key"><span>Mã kích hoạt</span><code>{key || 'Đang tạo mã...'}</code></div>
    <div className="license-gate-actions">
      <button type="button" className="license-secondary-button" disabled={!key} onClick={() => void copyKey()}>{copied ? 'Đã sao chép' : 'Sao chép mã'}</button>
      <button type="button" className="license-primary-button" onClick={() => void onRetry()}>Kiểm tra lại</button>
    </div>
  </section></main>;
}

export function LicenseGate({ children }: PropsWithChildren) {
  const bridge = typeof window === 'undefined' ? undefined : window.miaRuntime?.license;
  const [state, setState] = useState<LicenseStateResponse | null>(bridge ? null : { state: 'error', active: false, valid: false, expired: false, reason: 'LICENSE_REQUIRED', mode: 'browser' });
  async function initialize() {
    if (!bridge) return;
    setState((current) => current?.active ? current : { state: 'checking', active: false });
    try { setState(await bridge.initialize()); }
    catch (error) { setState({ state: 'error', active: false, reason: String((error as { code?: string })?.code || 'internal_error') }); }
  }
  async function submitPhone(phone: string, email: string) {
    if (!bridge) return;
    setState(await bridge.submitPhone(phone, email));
  }
  useEffect(() => { void initialize(); }, []);
  if (!state || state.state === 'checking' || state.state === 'migrating') return <LicenseFrame state={state || { state: 'checking', active: false }} onRetry={initialize} />;
  if (licenseAllowsWorkspace(state)) return <LicensePolicyContext.Provider value={state.entitlements ?? null}>
    <LicenseUpdateContext.Provider value={state.update ?? null}>
      <div className="licensed-workspace">
      {state.entitlements?.trial ? <div className="license-trial-banner" role="status">Key {state.entitlements.plan}: tối đa {state.entitlements.max_tax_codes} MST đã cấp phép, chỉ sử dụng dữ liệu 01/08/2026 – 31/08/2026.</div> : null}
      {children}
      </div>
    </LicenseUpdateContext.Provider>
  </LicensePolicyContext.Provider>;
  if (state.state === 'phone_required' || state.state === 'legacy_phone_required') {
    return <PhoneForm legacy={state.state === 'legacy_phone_required'} onSubmit={submitPhone} />;
  }
  if (state.state === 'email_required') return <EmailVerificationForm state={state} onDone={setState} />;
  if (state.state === 'activation_required') return <ActivationPage state={state} onRetry={async () => { if (bridge) setState(await bridge.retry()); }} />;
  return <LicenseFrame state={state} onRetry={initialize} />;
}
