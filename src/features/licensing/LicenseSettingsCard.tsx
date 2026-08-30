import { useEffect, useState } from 'react';
import type { LicenseDetails } from '../../lib/runtime-bridge';

function formatDate(value: string | null) {
  if (!value) return 'Chưa xác định';
  const [year, month, day] = value.slice(0, 10).split('-');
  return year && month && day ? `${day}/${month}/${year}` : value;
}

export function LicenseSettingsCard() {
  const [details, setDetails] = useState<LicenseDetails | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  async function refresh() {
    try { setDetails(await window.miaRuntime!.license.details()); setMessage(null); }
    catch { setMessage('Không thể đọc trạng thái bản quyền.'); }
  }
  useEffect(() => { if (window.miaRuntime?.license) void refresh(); }, []);
  if (!window.miaRuntime?.license) return null;
  const active = details?.active;
  return <section className="license-settings-card">
    <header><div><h2>Bản quyền</h2><p>Thông tin kích hoạt và thiết bị đang liên kết.</p></div><span data-active={active}>{active ? 'Đã kích hoạt' : details?.mode === 'disabled' ? 'Chưa bật' : 'Cần kiểm tra'}</span></header>
    <dl>
      <div><dt>Số điện thoại</dt><dd>{details?.phone || 'Chưa bổ sung'}</dd></div>
      <div><dt>Hạn sử dụng</dt><dd>{formatDate(details?.expires_at || null)}</dd></div>
      <div><dt>Thiết bị</dt><dd>{details?.device_bound ? 'Đã liên kết' : 'Chưa liên kết'}</dd></div>
      <div><dt>Mã bản quyền</dt><dd>{details?.canonical_key || 'Chưa có'}</dd></div>
    </dl>
    <button type="button" onClick={() => void refresh()}>Kiểm tra lại</button>
    {message ? <small>{message}</small> : null}
  </section>;
}
