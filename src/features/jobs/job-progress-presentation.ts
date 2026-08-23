import type { JobStatusResponse } from '../../lib/api/contracts';

export interface JobProgressView {
  status?: string | null;
  stage?: string | null;
  overall_percent?: number | null;
  stage_percent?: number | null;
  current_direction?: JobStatusResponse['current_direction'];
  current_query_type?: JobStatusResponse['current_query_type'];
  message?: string | null;
  current_month?: JobStatusResponse['current_month'];
  scope_progress?: JobStatusResponse['scope_progress'];
  progress_totals?: JobStatusResponse['progress_totals'];
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

const SCOPE_LABELS: Record<string, string> = {
  overview: 'Tổng quan',
  detail: 'Chi tiết',
  ensure_xml: 'XML',
  mvt: 'MVT',
};

function percentText(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(value)) return '';
  const rounded = Math.max(0, Math.min(100, Math.round(value)));
  return `${rounded}%`;
}

function withStagePercent(label: string, stagePercent?: number | null) {
  const percent = percentText(stagePercent);
  return percent ? `${label} · ${percent} giai đoạn` : label;
}

function directionLabel(direction?: string | null) {
  if (direction === 'purchase') return 'Mua vào';
  if (direction === 'sold') return 'Bán ra';
  return '';
}

function scopeProgress(job: JobProgressView) {
  const progress = job.scope_progress;
  if (!progress || !SCOPE_LABELS[progress.scope]) return null;
  const label = SCOPE_LABELS[progress.scope];
  const activeDirection = directionLabel(job.current_direction);
  const prefix = activeDirection ? `${label} - ${activeDirection}` : label;
  return progress.total > 0
    ? `${prefix} - ${progress.processed}/${progress.total} hóa đơn`
    : `${prefix} - Đang xác định tổng số hóa đơn`;
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
  if (job.status === 'completed' || job.status === 'completed_with_warning') {
    const total = job.progress_totals?.overview;
    const suffix = total ? ` - ${total.total}/${total.total} hóa đơn` : '';
    return `${job.status === 'completed' ? 'Đã tải xong' : 'Đã tải xong, có cảnh báo'}${suffix}`;
  }
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

  const aggregate = scopeProgress(job);
  if (aggregate) return aggregate;

  if (job.stage && STAGE_MESSAGES[job.stage]) {
    const activeDirection = directionLabel(job.current_direction);
    const label = activeDirection
      ? `${activeDirection} · ${STAGE_MESSAGES[job.stage]}`
      : STAGE_MESSAGES[job.stage];
    return withStagePercent(label, job.stage_percent);
  }

  // Source may add a future internal token before desktop has a translation.
  // Never leak protocol-like "namespace:event" copy to end users.
  if (raw && !raw.includes(':')) return raw;
  return 'Đang xử lý dữ liệu hóa đơn';
}
