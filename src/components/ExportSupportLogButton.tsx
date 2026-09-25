import { useState } from 'react';

export function ExportSupportLogButton() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const exportLog = typeof window === 'undefined' ? undefined : window.miaRuntime?.logs?.exportSupport;
  async function save() {
    if (!exportLog || busy) return;
    setBusy(true);
    setMessage('');
    try {
      const result = await exportLog();
      if (result.saved) setMessage('Đã lưu log. Vui lòng gửi file này cho bộ phận hỗ trợ.');
    } catch { setMessage('Không lưu được log. Vui lòng thử lại và chọn thư mục có quyền ghi.'); }
    finally { setBusy(false); }
  }
  if (!exportLog) return null;
  return <div>
    <button type="button" className="license-secondary-button" disabled={busy} onClick={() => void save()}>{busy ? 'Đang lưu log…' : 'Tải log lỗi'}</button>
    {message ? <p role="status">{message}</p> : null}
  </div>;
}

export function InlineErrorWithSupport({ message, className }: { message: string; className?: string }) {
  return <div className="inline-error-with-support">
    <div className={className} role="alert">{message}</div>
    <ExportSupportLogButton />
  </div>;
}
