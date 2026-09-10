import { type FormEvent, useState } from 'react';
import { PasswordInput, errorText } from './OfflineAuthGate';

export function OfflinePasswordSettingsCard() {
  const [editing, setEditing] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  function close() {
    setEditing(false); setCurrentPassword(''); setNewPassword(''); setConfirmation(''); setMessage('');
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setSaving(true); setMessage('');
    try {
      await window.miaRuntime!.offlineAuth.change(currentPassword, newPassword, confirmation);
      setCurrentPassword(''); setNewPassword(''); setConfirmation('');
      setMessage('Đã đổi mật khẩu đăng nhập trên máy.');
      setEditing(false);
    } catch (error) { setMessage(errorText(error)); }
    finally { setSaving(false); }
  }

  return <section className="offline-password-settings-card">
    <header><div><h2>Mật khẩu đăng nhập</h2><p>Ngăn người khác mở dữ liệu và chạy tác vụ khi sử dụng máy của bạn.</p></div><span>Chỉ lưu trên máy</span></header>
    {!editing ? <button type="button" onClick={() => { setEditing(true); setMessage(''); }}>Đổi mật khẩu</button> : <form onSubmit={(event) => void submit(event)}>
      <PasswordInput autoFocus label="Mật khẩu hiện tại" value={currentPassword} onChange={setCurrentPassword} autoComplete="current-password" />
      <PasswordInput label="Mật khẩu mới" value={newPassword} onChange={setNewPassword} autoComplete="new-password" />
      <PasswordInput label="Nhập lại mật khẩu mới" value={confirmation} onChange={setConfirmation} autoComplete="new-password" />
      <div><button type="button" onClick={close}>Hủy</button><button type="submit" disabled={saving}>{saving ? 'Đang lưu…' : 'Lưu mật khẩu mới'}</button></div>
    </form>}
    {message ? <small className={message.startsWith('Đã ') ? 'success' : 'error'} role="status">{message}</small> : null}
  </section>;
}
