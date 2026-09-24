import { useState } from 'react';
import { NoticeDialog } from './NoticeDialog';

export function AccountErrorDownloadButton({ snapshot }: { snapshot: Record<string, unknown> }) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; message: string } | null>(null);
  async function download() {
    if (busy) return;
    setBusy(true);
    try {
      const exporter = window.miaRuntime?.logs?.exportAccount;
      if (!exporter) throw new Error('diagnostics_unavailable');
      const result = await exporter(snapshot);
      if (result.saved) setNotice({ kind: 'success', message: 'Đã tải mã lỗi. Vui lòng gửi file JSON vừa lưu cho bộ phận kỹ thuật.' });
    } catch {
      setNotice({ kind: 'error', message: 'Không lưu được mã lỗi. Vui lòng thử lại và chọn thư mục có quyền ghi.' });
    } finally { setBusy(false); }
  }
  return <>
    <button className="row-result-button" type="button" disabled={busy} onClick={() => void download()}>{busy ? 'Đang tải mã lỗi…' : 'Tải mã lỗi'}</button>
    {notice && <NoticeDialog kind={notice.kind} message={notice.message} onClose={() => setNotice(null)} />}
  </>;
}
