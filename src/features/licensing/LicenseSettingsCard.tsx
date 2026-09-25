import { type FormEvent, useEffect, useState } from 'react';
import type { LicenseDetails } from '../../lib/runtime-bridge';
import { InlineErrorWithSupport } from '../../components/ExportSupportLogButton';
import { licenseActionError } from './LicenseGate';

function formatDate(value: string | null) {
  if (!value) return 'Chưa xác định';
  const [year, month, day] = value.slice(0, 10).split('-');
  return year && month && day ? `${day}/${month}/${year}` : value;
}

function packageName(details: LicenseDetails | null) {
  if (!details?.plan) return 'Chưa xác định';
  if (details.max_tax_codes === null) return `${details.plan} · Nhiều MST`;
  return `${details.plan} · ${details.max_tax_codes === 1 ? '01 MST' : `${details.max_tax_codes} MST`}`;
}

export function LicenseSettingsCard() {
  const [details, setDetails] = useState<LicenseDetails | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [challenge, setChallenge] = useState('');
  const [code, setCode] = useState('');
  const [maskedEmail, setMaskedEmail] = useState('');
  const [saving, setSaving] = useState(false);
  async function refresh() {
    try {
      const value = await window.miaRuntime!.license.details();
      setDetails(value); setPhone(value.phone_value || ''); setEmail(value.email || ''); setMessage(null);
    }
    catch (error) { setMessage(licenseActionError(error, 'Không thể đọc trạng thái bản quyền.')); }
  }
  useEffect(() => { if (window.miaRuntime?.license) void refresh(); }, []);
  if (!window.miaRuntime?.license) return null;
  const active = details?.active;
  async function updateContact(event: FormEvent) {
    event.preventDefault(); setSaving(true); setMessage(null);
    try {
      if (!challenge) {
        const result = await window.miaRuntime!.license.requestContactVerification(phone, email);
        setChallenge(result.challenge_id); setMaskedEmail(result.masked_email);
        setMessage(`Đã gửi mã xác nhận tới ${result.masked_email}.`);
      } else {
        await window.miaRuntime!.license.confirmContact(challenge, code);
        setEditing(false); setChallenge(''); setCode('');
        await refresh(); setMessage('Đã cập nhật email khôi phục.');
      }
    } catch (error) { setMessage(licenseActionError(error, 'Không thể cập nhật thông tin.')); }
    finally { setSaving(false); }
  }
  return <section className="license-settings-card">
    <header><div><h2>Bản quyền</h2><p>Thông tin kích hoạt và thiết bị đang liên kết.</p></div><span data-active={active}>{active ? 'Đã kích hoạt' : details?.mode === 'disabled' ? 'Chưa bật' : 'Cần kiểm tra'}</span></header>
    <dl>
      <div><dt>Số điện thoại</dt><dd>{details?.phone || 'Chưa bổ sung'}</dd></div>
      <div><dt>Email khôi phục</dt><dd>{details?.email || 'Chưa bổ sung'}</dd></div>
      <div><dt>Hạn sử dụng</dt><dd>{formatDate(details?.expires_at || null)}</dd></div>
      <div><dt>Gói đăng ký</dt><dd>{packageName(details)}</dd></div>
      <div><dt>Ngày kích hoạt sử dụng</dt><dd>{formatDate(details?.activated_at || null)}</dd></div>
      <div><dt>Thiết bị</dt><dd>{details?.device_bound ? 'Đã liên kết' : 'Chưa liên kết'}</dd></div>
      <div><dt>Mã bản quyền</dt><dd>{details?.canonical_key || 'Chưa có'}</dd></div>
    </dl>
    {!editing ? <div className="license-settings-actions"><button type="button" onClick={() => void refresh()}>Kiểm tra lại</button><button type="button" onClick={() => { setEditing(true); setMessage(null); }}>Cập nhật</button></div>
      : <form className="license-contact-form" onSubmit={(event) => void updateContact(event)}>
        {!challenge ? <><label>Số điện thoại<input inputMode="numeric" autoComplete="tel" maxLength={10} value={phone} onChange={(event) => setPhone(event.target.value.replace(/\D/g, '').slice(0, 10))} required /></label>
          <label>Email khôi phục<input type="email" autoComplete="email" maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} required /></label></>
          : <label>Mã xác nhận gửi tới {maskedEmail}<input autoFocus inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, '').slice(0, 6))} required /></label>}
        <div><button type="button" onClick={() => { setEditing(false); setChallenge(''); setCode(''); }}>Hủy</button><button type="submit" disabled={saving}>{saving ? 'Đang xử lý…' : challenge ? 'Xác nhận' : 'Gửi mã xác nhận'}</button></div>
      </form>}
    {message ? message.startsWith('Đã ')
      ? <small role="status">{message}</small>
      : <InlineErrorWithSupport className="license-form-error" message={message} /> : null}
  </section>;
}
