import { describe, expect, it } from 'vitest';
import { formatSourceJobProgress } from '../../src/features/jobs/job-progress-presentation';

describe('source job progress presentation', () => {
  it('does not leak running:overview and reports concise monthly source counters', () => {
    expect(formatSourceJobProgress({
      status: 'running',
      stage: 'overview',
      message: 'running:overview',
      current_month: {
        key: '2025-05', index: 1, total: 3,
        processed: 45, planned: 120, percent: 37.5,
      },
    })).toBe('Tổng quan 05/2025 · 45/120 hóa đơn');
  });

  it('translates granular source authentication milestones', () => {
    expect(formatSourceJobProgress({
      status: 'running',
      stage: 'auth',
      message: 'auth:captcha_solved',
      stage_percent: 55,
    })).toBe('Đã giải CAPTCHA, chuẩn bị đăng nhập · 55% giai đoạn');
  });

  it('translates source finalize operations', () => {
    expect(formatSourceJobProgress({
      status: 'running',
      stage: 'finalize',
      message: 'finalize:validate_results',
      stage_percent: 55,
    })).toBe('Đang kiểm tra kết quả hóa đơn đã lưu · 55% giai đoạn');
  });

  it('keeps future protocol tokens out of the end-user UI', () => {
    expect(formatSourceJobProgress({
      status: 'running',
      stage: null,
      message: 'future_internal:step',
    })).toBe('Đang xử lý dữ liệu hóa đơn');
  });
});
