import type { JobStatusResponse } from '../../lib/api/contracts';

export interface JobProgressView {
  status?: string | null;
  stage?: string | null;
  overall_percent?: number | null;
  stage_percent?: number | null;
  message?: string | null;
  current_month?: JobStatusResponse['current_month'];
  error?: JobStatusResponse['error'];
}

const AUTH_MESSAGES: Record<string, string> = {
  session_loaded: 'Đang kiểm tra phiên đăng nhập đã lưu',
  auth_claim_started: 'Đang chuẩn bị phiên đăng nhập',
  auth_claim_acquired: 'Đã giữ quyền sử dụng phiên đăng nhập',
  credentials_ready: 'Đã chuẩn bị thông tin đăng nhập',
  captcha_request_started: 'Đang yêu cầu mã CAPTCHA',
  captcha_fetched: 'Đã nhận CAPTCHA từ Cổng HĐĐT',
  captcha_payload_validated: 'Đã kiểm tra dữ liệu CAPTCHA',
  captcha_solved: 'Đã giải CAPTCHA, chuẩn bị đăng nhập',
  login_request_started: 'Đang gửi yêu cầu đăng nhập Cổng HĐĐT',
  login_request_succeeded: 'Cổng HĐĐT đã nhận yêu cầu đăng nhập',
  login_response_received: 'Đã nhận phản hồi đăng nhập',
  login_response_validated: 'Đã xác minh phản hồi đăng nhập',
  login_token_received: 'Đã nhận token đăng nhập',
  token_persisted: 'Đã lưu phiên đăng nhập an toàn',
};

const FINALIZE_MESSAGES: Record<string, string> = {
  validate_modules: 'Đang kiểm tra các phần dữ liệu đã tải',
  validate_results: 'Đang kiểm tra kết quả hóa đơn đã lưu',
  persist_warnings: 'Đang lưu các cảnh báo của phiên đồng bộ',
  commit_terminal_state: 'Đang ghi nhận trạng thái hoàn tất',
};

const STAGE_MESSAGES: Record<string, string> = {
  auth: 'Đang xác thực tài khoản với Cổng HĐĐT',
  overview: 'Đang chuẩn bị dữ liệu tổng quan hóa đơn',
  detail: 'Đang chuẩn bị tải chi tiết hóa đơn',
  ensure_xml: 'Đang chuẩn bị dữ liệu XML hóa đơn',
  mvt: 'Đang chuẩn bị dữ liệu MVT hóa đơn',
  finalize: 'Đang hoàn tất và kiểm tra dữ liệu',
};

const MONTH_STAGE_LABELS: Record<string, string> = {
  overview: 'Tổng quan',
  detail: 'Chi tiết',
  ensure_xml: 'XML',
  mvt: 'MVT',
};

function formatMonthKey(value?: string | null) {
  const match = String(value ?? '').match(/^(\d{4})-(\d{2})$/);
  return match ? `${match[2]}/${match[1]}` : value || '—';
}

function percentText(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '';
  const rounded = Math.max(0, Math.min(100, Math.round(value)));
  return `${rounded}%`;
}

function withStagePercent(label: string, stagePercent?: number | null) {
  const percent = percentText(stagePercent);
  return percent ? `${label} · ${percent} giai đoạn` : label;
}

function monthProgress(job: JobProgressView) {
  const month = job.current_month;
  const stage = job.stage ?? '';
  if (!month || !MONTH_STAGE_LABELS[stage]) return null;

  const label = MONTH_STAGE_LABELS[stage];
  const monthLabel = formatMonthKey(month.key);

  if (month.planned > 0) {
    return `${label} ${monthLabel} · ${month.processed}/${month.planned} hóa đơn`;
  }

  if (month.processed > 0) {
    return `${label} ${monthLabel} · đã xử lý ${month.processed} hóa đơn`;
  }

  return `Đang xác định dữ liệu ${label.toLocaleLowerCase('vi')} tháng ${monthLabel}`;
}

/**
 * Convert durable source progress tokens into user-facing copy.
 *
 * No progress value is calculated here. Percentages, month counters, stage and
 * raw milestone tokens all come from mia-crawl-service; this module is only a
 * Vietnamese presentation dictionary for the desktop UI.
 */
export function formatSourceJobProgress(job?: JobProgressView | null) {
  if (!job) return 'Chưa đồng bộ';

  if (job.status === 'queued') return 'Đang chờ bộ xử lý bắt đầu';
  if (job.status === 'waiting_account') return 'Đang chờ tài khoản trước hoàn tất';
  if (job.status === 'cancelling') return 'Đang dừng tác vụ đồng bộ';
  if (job.status === 'cancelled') return 'Đã dừng';
  if (job.status === 'completed') return 'Đã tải xong';
  if (job.status === 'completed_with_warning') return 'Đã tải xong, có cảnh báo';
  if (job.status === 'failed') return job.error?.message || 'Job xử lý thất bại';

  const raw = String(job.message ?? '').trim();
  if (raw.startsWith('auth:')) {
    const event = raw.slice('auth:'.length);
    return withStagePercent(
      AUTH_MESSAGES[event] ?? STAGE_MESSAGES.auth,
      job.stage_percent,
    );
  }

  if (raw.startsWith('finalize:')) {
    const operation = raw.slice('finalize:'.length);
    return withStagePercent(
      FINALIZE_MESSAGES[operation] ?? STAGE_MESSAGES.finalize,
      job.stage_percent,
    );
  }

  const month = monthProgress(job);
  if (month) return month;

  if (job.stage && STAGE_MESSAGES[job.stage]) {
    return withStagePercent(STAGE_MESSAGES[job.stage], job.stage_percent);
  }

  // Source may add a future internal token before desktop has a translation.
  // Never leak protocol-like "namespace:event" copy to end users.
  if (raw && !raw.includes(':')) return raw;
  return 'Đang xử lý dữ liệu hóa đơn';
}
