import { type FormEvent, type PropsWithChildren, useEffect, useId, useState } from 'react';
import logo from '../../assets/figma/logo.png';
import type { OfflineAuthState } from '../../lib/runtime-bridge';
import '../../styles/offline-auth.css';
import { InlineErrorWithSupport } from '../../components/ExportSupportLogButton';

function errorText(error: unknown) {
  const value = error as { code?: string; message?: string };
  const messages: Record<string, string> = {
    offline_password_invalid: 'Mật khẩu phải có từ 8 đến 128 ký tự.',
    offline_password_confirmation_mismatch: 'Mật khẩu xác nhận không khớp.',
    offline_password_incorrect: 'Mật khẩu không đúng. Vui lòng thử lại.',
    offline_auth_rate_limited: 'Bạn đã nhập sai nhiều lần. Vui lòng chờ rồi thử lại.',
    offline_auth_state_corrupt: 'Dữ liệu mật khẩu trên máy bị lỗi. Vui lòng liên hệ hỗ trợ.',
    recovery_code_invalid: 'Mã xác nhận không đúng.',
    recovery_code_expired: 'Mã xác nhận đã hết hạn. Vui lòng gửi mã mới.',
    recovery_code_locked: 'Mã xác nhận đã bị khóa do nhập sai quá số lần.',
    recovery_email_not_registered: 'Thiết bị chưa đăng ký email khôi phục.',
    recovery_wait_before_resend: 'Vui lòng chờ trước khi gửi lại mã.',
    recovery_rate_limited: 'Đã yêu cầu quá nhiều mã. Vui lòng thử lại sau.',
    recovery_email_unavailable: 'Không thể gửi email xác nhận lúc này.',
  };
  return messages[value?.code || ''] || value?.message?.replace(/^\[[^\]]+\]\s*/, '') || 'Không thể xác thực mật khẩu trên máy.';
}

function PasswordInput({ label, value, onChange, autoComplete, autoFocus = false }: {
  label: string; value: string; onChange(value: string): void; autoComplete: string; autoFocus?: boolean;
}) {
  const [visible, setVisible] = useState(false);
  const inputId = useId();
  return <div className="offline-password-field">
    <label htmlFor={inputId}>{label}</label>
    <span><input id={inputId} autoFocus={autoFocus} type={visible ? 'text' : 'password'} minLength={8} maxLength={128} required autoComplete={autoComplete} value={value} onChange={(event) => onChange(event.target.value)} />
      <button type="button" aria-label={visible ? `Ẩn ${label.toLocaleLowerCase('vi')}` : `Hiện ${label.toLocaleLowerCase('vi')}`} onClick={() => setVisible((current) => !current)}>{visible ? 'Ẩn' : 'Hiện'}</button></span>
  </div>;
}

export function PasswordRecovery({ onRecovered, onCancel }: { onRecovered?(state: OfflineAuthState): void; onCancel(): void }) {
  const bridge = window.miaRuntime?.offlineAuth;
  const [challenge, setChallenge] = useState('');
  const [maskedEmail, setMaskedEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function requestCode() {
    if (!bridge || busy) return;
    setBusy(true); setMessage('');
    try {
      const result = await bridge.requestRecovery();
      setChallenge(result.challenge_id); setMaskedEmail(result.masked_email);
    } catch (error) { setMessage(errorText(error)); }
    finally { setBusy(false); }
  }
  async function recover(event: FormEvent) {
    event.preventDefault();
    if (!bridge || busy) return;
    setBusy(true); setMessage('');
    try { onRecovered?.(await bridge.recover(challenge, code, password, confirmation)); }
    catch (error) { setMessage(errorText(error)); }
    finally { setBusy(false); }
  }
  if (!challenge) return <div className="offline-recovery-panel">
    <h2>Khôi phục mật khẩu</h2><p>MIA sẽ gửi mã xác nhận đến email đã đăng ký với thiết bị này.</p>
    {message ? <InlineErrorWithSupport className="offline-auth-error" message={message} /> : null}
    <div><button type="button" onClick={onCancel}>Hủy</button><button type="button" disabled={busy} onClick={() => void requestCode()}>{busy ? 'Đang gửi…' : 'Gửi mã xác nhận'}</button></div>
  </div>;
  return <form className="offline-recovery-panel" onSubmit={(event) => void recover(event)}>
    <h2>Tạo mật khẩu mới</h2><p>Nhập mã 6 số đã gửi tới <strong>{maskedEmail}</strong>.</p>
    <div className="offline-password-field"><label>Mã xác nhận</label><span><input autoFocus inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} /></span></div>
    <PasswordInput label="Mật khẩu mới" value={password} onChange={setPassword} autoComplete="new-password" />
    <PasswordInput label="Nhập lại mật khẩu mới" value={confirmation} onChange={setConfirmation} autoComplete="new-password" />
    {message ? <InlineErrorWithSupport className="offline-auth-error" message={message} /> : null}
    <div><button type="button" onClick={onCancel}>Hủy</button><button type="submit" disabled={busy}>{busy ? 'Đang cập nhật…' : 'Đổi mật khẩu'}</button></div>
  </form>;
}

export function OfflineAuthGate({ children }: PropsWithChildren) {
  const bridge = window.miaRuntime?.offlineAuth;
  const [state, setState] = useState<OfflineAuthState | null>(null);
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [recovering, setRecovering] = useState(false);

  useEffect(() => {
    if (!bridge) { setMessage('Không tìm thấy lớp bảo vệ mật khẩu cục bộ.'); return; }
    void bridge.status().then(setState).catch((error) => setMessage(errorText(error)));
  }, [bridge]);

  useEffect(() => {
    if (!bridge || !state?.retry_after_seconds) return undefined;
    const timer = window.setInterval(() => {
      void bridge.status().then(setState).catch(() => undefined);
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [bridge, state?.retry_after_seconds]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!bridge || submitting) return;
    setSubmitting(true);
    setMessage('');
    try {
      const next = state?.state === 'setup_required'
        ? await bridge.create(password, confirmation)
        : await bridge.unlock(password);
      setPassword('');
      setConfirmation('');
      setState(next);
    } catch (error) {
      setMessage(errorText(error));
      try { setState(await bridge.status()); } catch { /* Keep the actionable submit error. */ }
    } finally {
      setSubmitting(false);
    }
  }

  if (state?.unlocked) return <>{children}</>;
  if (state && recovering) return <main className="offline-auth-page"><section className="offline-auth-card">
    <div className="offline-auth-brand"><img src={logo} alt="" /><span><strong>MIA TOOL 2026</strong><small>Bảo vệ dữ liệu trên máy</small></span></div>
    <PasswordRecovery onCancel={() => setRecovering(false)} onRecovered={setState} />
  </section></main>;
  const setup = state?.state === 'setup_required';
  return <main className="offline-auth-page">
    <form className="offline-auth-card" onSubmit={(event) => void submit(event)}>
      <div className="offline-auth-brand"><img src={logo} alt="" /><span><strong>MIA TOOL 2026</strong><small>Bảo vệ dữ liệu trên máy</small></span></div>
      <div className="offline-auth-shield" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 2 4.5 5v5.8c0 4.7 3.1 9 7.5 10.2 4.4-1.2 7.5-5.5 7.5-10.2V5L12 2Zm0 5a3 3 0 0 1 1 5.8V16h-2v-3.2A3 3 0 0 1 12 7Z" /></svg></div>
      <h1>{setup ? 'Tạo mật khẩu đăng nhập' : 'Đăng nhập trên máy này'}</h1>
      <p>{setup ? 'Key đã được xác thực. Hãy tạo mật khẩu riêng để người khác sử dụng máy không thể mở dữ liệu và chạy tác vụ của bạn.' : 'Nhập mật khẩu đã tạo trên máy để mở không gian làm việc.'}</p>
      {!state && !message ? <div className="offline-auth-loading"><span /></div> : null}
      {state ? <>
        <PasswordInput autoFocus label={setup ? 'Mật khẩu mới' : 'Mật khẩu'} value={password} onChange={setPassword} autoComplete={setup ? 'new-password' : 'current-password'} />
        {setup ? <PasswordInput label="Nhập lại mật khẩu" value={confirmation} onChange={setConfirmation} autoComplete="new-password" /> : null}
        {setup ? <small className="offline-auth-hint">Tối thiểu 8 ký tự. Mật khẩu chỉ dùng trên máy này và không được gửi lên server.</small> : null}
        {state.retry_after_seconds > 0 ? <small className="offline-auth-wait">Thử lại sau khoảng {state.retry_after_seconds} giây.</small> : null}
        {message ? <InlineErrorWithSupport className="offline-auth-error" message={message} /> : null}
        <button className="offline-auth-submit" type="submit" disabled={submitting || state.retry_after_seconds > 0}>{submitting ? 'Đang xử lý…' : setup ? 'Tạo mật khẩu và tiếp tục' : 'Đăng nhập'}</button>
        {!setup ? <button className="offline-auth-forgot" type="button" onClick={() => { setRecovering(true); setMessage(''); }}>Quên mật khẩu? Gửi mail xác nhận để tạo mới mật khẩu</button> : null}
      </> : message ? <InlineErrorWithSupport className="offline-auth-error" message={message} /> : null}
    </form>
  </main>;
}

export { PasswordInput, errorText };
