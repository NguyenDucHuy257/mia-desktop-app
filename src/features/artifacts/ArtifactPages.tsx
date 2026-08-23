import { useEffect, useState } from 'react';
import { NoticeDialog } from '../../components/NoticeDialog';

export function UtilityPage({ title, description, onPdfConcurrencyChange }: {
  title: string;
  description: string;
  onPdfConcurrencyChange?(value: number): void;
}) {
  const [query, setQuery] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [retries, setRetries] = useState(5);
  const [pdfConcurrency, setPdfConcurrency] = useState(5);
  const isSettings = title === 'Cài đặt';
  const isLogs = title === 'Nhật ký';

  useEffect(() => {
    if (isSettings) void window.miaRuntime?.preferences?.get().then((value) => {
      setRetries(value.retries);
      setPdfConcurrency(value.pdfConcurrency ?? 5);
    }).catch(() => undefined);
    if (isLogs) void window.miaRuntime?.logs?.list().then(setLogs).catch(() => setMessage('Không thể đọc nhật ký cục bộ.'));
  }, [isLogs, isSettings]);

  const visibleLogs = logs.filter((line) => line.toLocaleLowerCase('vi').includes(query.toLocaleLowerCase('vi')));

  async function saveSettings() {
    try {
      const current = await window.miaRuntime?.preferences?.get();
      const saved = current ? await window.miaRuntime?.preferences?.set({
        ...current, concurrency: 1, retries, pdfConcurrency,
      }) : undefined;
      if (!saved) throw new Error('preferences_unavailable');
      setRetries(saved.retries);
      setPdfConcurrency(saved.pdfConcurrency);
      onPdfConcurrencyChange?.(saved.pdfConcurrency);
      setMessage('Đã lưu cài đặt trên máy. Tác vụ mới sẽ áp dụng cấu hình này.');
    } catch {
      setMessage('Không thể lưu cài đặt. Giá trị PDF phải nằm trong khoảng 1–100.');
    }
  }

  return <section className="utility-page">
    <h1>{title}</h1><p>{description}</p>
    {isSettings ? <div className="utility-panel">
      <label>Chế độ xử lý<select value={1} disabled aria-label="Chế độ xử lý tuần tự"><option value={1}>Tuần tự (1 tài khoản/lần)</option></select></label>
      <label>Số lần thử lại<input type="number" min="0" max="5" value={retries} onChange={(event) => setRetries(Number(event.target.value))} /></label>
      <label>Số PDF xử lý đồng thời<input type="number" min="1" max="100" aria-label="Số PDF xử lý đồng thời" value={pdfConcurrency} onChange={(event) => setPdfConcurrency(Number(event.target.value))} /></label>
      <button type="button" onClick={() => void saveSettings()}>Lưu cài đặt</button>
    </div> : isLogs ? <div className="utility-panel">
      <label>Tìm kiếm<input aria-label="Tìm kiếm Nhật ký" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <button type="button" onClick={() => void window.miaRuntime?.logs?.list().then(setLogs).catch(() => setMessage('Không thể làm mới nhật ký.'))}>Làm mới</button>
      {visibleLogs.length ? <ol className="utility-log-list">{visibleLogs.map((line, index) => <li key={`${index}:${line}`}>{line}</li>)}</ol> : <div className="utility-empty"><strong>Chưa có nhật ký phù hợp</strong></div>}
    </div> : <div className="utility-panel"><div className="utility-empty"><strong>Chưa có nguồn dữ liệu mã vật tư</strong><span>Runtime crawler hiện không cung cấp danh mục mã vật tư. Không có dữ liệu giả được hiển thị.</span></div></div>}
    {message ? <NoticeDialog kind={message.startsWith('Đã ') ? 'success' : 'notice'} message={message} onClose={() => setMessage(null)} /> : null}
  </section>;
}
