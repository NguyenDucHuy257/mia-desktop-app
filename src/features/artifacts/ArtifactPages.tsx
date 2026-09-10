import { useEffect, useState } from 'react';
import { NoticeDialog } from '../../components/NoticeDialog';
import type { DiagnosticLogEntry } from '../../lib/runtime-bridge';
import { LicenseSettingsCard } from '../licensing/LicenseSettingsCard';
import { OfflinePasswordSettingsCard } from '../offline-auth/OfflinePasswordSettingsCard';

const EVENT_LABELS: Record<string, string> = {
  account_login_requested: 'Đang đăng nhập tài khoản',
  account_login_succeeded: 'Đăng nhập tài khoản thành công',
  account_login_failed: 'Đăng nhập tài khoản thất bại',
  sync_clicked: 'Người dùng yêu cầu đồng bộ',
  job_start_requested: 'Đang tạo tác vụ đồng bộ',
  job_started: 'Đã tạo tác vụ đồng bộ',
  job_start_failed: 'Không thể tạo tác vụ đồng bộ',
  job_progress: 'Tiến trình đồng bộ',
  job_poll_failed: 'Không thể cập nhật tiến trình đồng bộ',
  results_opened: 'Mở kết quả hóa đơn',
  results_request: 'Tra cứu kết quả hóa đơn',
  results_failed: 'Tra cứu kết quả thất bại',
  results_export_requested: 'Bắt đầu tải kết quả Excel',
  results_export_completed: 'Tải kết quả Excel hoàn tất',
  results_export_failed: 'Tải kết quả Excel thất bại',
  artifact_download_requested: 'Bắt đầu tải XML/HTML/PDF',
  artifact_download_completed: 'Tải XML/HTML/PDF hoàn tất',
  artifact_download_failed: 'Tải XML/HTML/PDF thất bại',
  source_confirmed_unavailable: 'Không lấy được gói dữ liệu sau 7 lần thử',
  source_retry_exhausted: 'Máy chủ không phản hồi sau các lần thử',
  artifact_copy_failed: 'Không thể sao chép tệp dữ liệu',
  artifact_dependency_missing: 'Thiếu dữ liệu cần thiết để tạo tệp',
  pdf_failed: 'Không thể tạo tệp PDF',
  renderer_uncaught_error: 'Lỗi giao diện chưa được xử lý',
  renderer_unhandled_rejection: 'Lỗi tác vụ giao diện chưa được xử lý',
};

function logTitle(entry: DiagnosticLogEntry) {
  return EVENT_LABELS[entry.event] ?? entry.event.replace(/[_.-]+/g, ' ');
}

function formatTimestamp(value: string) {
  if (!value) return 'Không rõ thời gian';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('vi-VN');
}

export function UtilityPage({ title, description, onPdfConcurrencyChange }: {
  title: string;
  description: string;
  onPdfConcurrencyChange?(value: number): void;
}) {
  const [query, setQuery] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [logs, setLogs] = useState<DiagnosticLogEntry[]>([]);
  const [retries, setRetries] = useState(5);
  const [pdfConcurrency, setPdfConcurrency] = useState(5);
  const isSettings = title === 'Cài đặt' || title === 'Cài đặt hệ thống';
  const isLogs = title === 'Nhật ký';
  const isPausedHistory = title === 'Lịch sử tải xuống';
  const isGuide = title === 'Hướng dẫn sử dụng';
  const isComingSoon = isGuide || isPausedHistory || title === 'Tra cứu MVT' || title === 'Tra cứu PDF gốc';

  useEffect(() => {
    if (isSettings) void window.miaRuntime?.preferences?.get().then((value) => {
      setRetries(value.retries);
      setPdfConcurrency(value.pdfConcurrency ?? 5);
    }).catch(() => undefined);
    if (isLogs) void refreshLogs();
  }, [isLogs, isSettings]);

  const diagnosticLogs = logs.filter((entry) => entry.level === 'error' || entry.level === 'warn');
  const visibleLogs = diagnosticLogs.filter((entry) => `${logTitle(entry)} ${entry.event} ${entry.source} ${entry.details}`.toLocaleLowerCase('vi').includes(query.toLocaleLowerCase('vi')));

  async function refreshLogs() {
    try {
      const bridge = window.miaRuntime?.logs;
      if (!bridge) { setLogs([]); return; }
      if (typeof bridge.entries === 'function') {
        setLogs((await bridge.entries()).filter((entry) => entry.level === 'error' || entry.level === 'warn'));
        return;
      }
      const legacy = await bridge.list();
      setLogs(legacy.flatMap((details, index) => {
        const match = details.match(/\b(WARN(?:ING)?|ERROR)\b/i);
        if (!match) return [];
        return [{ id: `legacy-${index}`, timestamp: '', level: match[1].toUpperCase() === 'ERROR' ? 'error' as const : 'warn' as const, source: 'system', event: 'diagnostic_log', details }];
      }));
    }
    catch { setMessage('Không thể đọc nhật ký cục bộ.'); }
  }

  async function clearLogs() {
    try {
      await window.miaRuntime?.logs?.clear();
      setLogs([]);
      setMessage('Đã xóa nhật ký trên máy.');
    } catch { setMessage('Không thể xóa nhật ký. Vui lòng đóng tác vụ đang chạy và thử lại.'); }
  }

  async function copyLog(entry: DiagnosticLogEntry) {
    try {
      await navigator.clipboard.writeText(`[${entry.level.toUpperCase()}] ${entry.event}\n${entry.timestamp}\n${entry.source}\n${entry.details}`);
      setMessage('Đã sao chép chi tiết nhật ký để gửi đội phát triển.');
    } catch { setMessage('Không thể sao chép nhật ký. Vui lòng chọn nội dung chi tiết thủ công.'); }
  }

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

  return <section className={`utility-page${isSettings ? ' utility-page--settings' : ''}`}>
    <h1>{title}</h1>{description ? <p>{description}</p> : null}
    {isSettings ? <div className="utility-panel utility-settings-panel">
      <section className="utility-settings-card">
        <header>
          <div className="utility-settings-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm9 4c0-.5 0-1-.1-1.5l2-1.5-2-3.5-2.5 1a9 9 0 0 0-2.6-1.5L15.5 2h-4l-.4 3a9 9 0 0 0-2.6 1.5L6 5.5 4 9l2 1.5a10 10 0 0 0 0 3L4 15l2 3.5 2.5-1a9 9 0 0 0 2.6 1.5l.4 3h4l.4-3a9 9 0 0 0 2.6-1.5l2.5 1 2-3.5-2-1.5c.1-.5.1-1 .1-1.5Z" /></svg></div>
          <div><h2>Xử lý dữ liệu</h2><p>Cấu hình tốc độ và số lần thử lại cho các tác vụ mới.</p></div>
        </header>
        <div className="utility-settings-grid">
          <label><span>Chế độ xử lý</span><select value={1} disabled aria-label="Chế độ xử lý tuần tự"><option value={1}>Tuần tự (1 tài khoản/lần)</option></select><small>Giúp tác vụ ổn định và tránh xung đột dữ liệu.</small></label>
          <label><span>Số lần thử lại</span><input aria-label="Số lần thử lại" type="number" min="0" max="5" value={retries} onChange={(event) => setRetries(Number(event.target.value))} /><small>Từ 0 đến 5 lần khi kết nối bị gián đoạn.</small></label>
          <label><span>Số PDF xử lý đồng thời</span><input type="number" min="1" max="100" aria-label="Số PDF xử lý đồng thời" value={pdfConcurrency} onChange={(event) => setPdfConcurrency(Number(event.target.value))} /><small>Từ 1 đến 100 tệp, tùy cấu hình máy.</small></label>
        </div>
        <footer><span>Thay đổi áp dụng cho tác vụ bắt đầu sau khi lưu.</span><button type="button" onClick={() => void saveSettings()}>Lưu cài đặt</button></footer>
      </section>
      <LicenseSettingsCard />
      <OfflinePasswordSettingsCard />
    </div> : isLogs ? <div className="utility-panel utility-log-panel">
      <div className="utility-log-toolbar">
        <label className="utility-log-search">Tìm kiếm<input aria-label="Tìm kiếm Nhật ký" placeholder="Tìm chức năng, lỗi hoặc mã tác vụ..." value={query} onChange={(event) => setQuery(event.target.value)} /></label>
        <button type="button" onClick={() => void refreshLogs()}>Làm mới</button>
        <button className="utility-log-clear" type="button" onClick={() => void clearLogs()}>Xóa nhật ký</button>
      </div>
      {visibleLogs.length ? <div className="utility-log-list" role="list">{visibleLogs.map((entry) => <article className="utility-log-row" data-level={entry.level} key={entry.id} role="listitem">
        <span className="utility-log-level">{entry.level === 'error' ? 'Lỗi' : entry.level === 'warn' ? 'Cảnh báo' : 'Thông tin'}</span>
        <div><strong>{logTitle(entry)}</strong><span>{formatTimestamp(entry.timestamp)} · {entry.source}</span>{entry.details ? <pre>{entry.details}</pre> : null}</div>
        <div className="utility-log-meta"><code>{entry.event}</code>{entry.level === 'error' ? <button type="button" onClick={() => void copyLog(entry)}>Sao chép lỗi</button> : null}</div>
      </article>)}</div> : <div className="utility-empty"><strong>Chưa có nhật ký phù hợp</strong><span>Lịch sử đăng nhập, đồng bộ, tải dữ liệu và lỗi kỹ thuật sẽ xuất hiện tại đây.</span></div>}
    </div> : <div className="utility-panel"><div className="utility-empty"><strong>{isComingSoon ? 'Chức năng đang cập nhật' : 'Chưa có dữ liệu'}</strong></div></div>}
    {message ? <NoticeDialog kind={message.startsWith('Đã ') ? 'success' : 'notice'} message={message} onClose={() => setMessage(null)} /> : null}
  </section>;
}
