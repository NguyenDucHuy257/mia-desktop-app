import { useEffect, useState } from 'react';
import { NoticeDialog } from '../../components/NoticeDialog';
import type { DiagnosticLogEntry } from '../../lib/runtime-bridge';
import { LicenseSettingsCard } from '../licensing/LicenseSettingsCard';

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
  const isLogs = title === 'Nhật ký' || title === 'Lịch sử tải xuống';
  const isGuide = title === 'Hướng dẫn sử dụng';
  const isComingSoon = isGuide || title === 'Tra cứu MVT';

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

  return <section className="utility-page">
    <h1>{title}</h1>{description ? <p>{description}</p> : null}
    {isSettings ? <div className="utility-panel">
      <label>Chế độ xử lý<select value={1} disabled aria-label="Chế độ xử lý tuần tự"><option value={1}>Tuần tự (1 tài khoản/lần)</option></select></label>
      <label>Số lần thử lại<input type="number" min="0" max="5" value={retries} onChange={(event) => setRetries(Number(event.target.value))} /></label>
      <label>Số PDF xử lý đồng thời<input type="number" min="1" max="100" aria-label="Số PDF xử lý đồng thời" value={pdfConcurrency} onChange={(event) => setPdfConcurrency(Number(event.target.value))} /></label>
      <button type="button" onClick={() => void saveSettings()}>Lưu cài đặt</button>
      <LicenseSettingsCard />
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
