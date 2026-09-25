import { describe, expect, it } from 'vitest';
import { formatSourceJobProgress } from '../../src/features/jobs/job-progress-presentation';

describe('source job progress presentation', () => {
  it('does not leak running:overview and reports the active source direction', () => {
    expect(formatSourceJobProgress({
      status: 'running',
      stage: 'overview',
      current_direction: 'purchase',
      message: 'running:overview',
      scope_progress: { scope: 'overview', processed: 145, total: 300 },
    })).toBe('Tổng quan - Mua vào - 145/300 hóa đơn');
  });

  it('changes the presentation when source advances to sold data', () => {
    expect(formatSourceJobProgress({
      status: 'running',
      stage: 'detail',
      current_direction: 'sold',
      message: 'running:detail',
      scope_progress: { scope: 'detail', processed: 7843, total: 9492 },
    })).toBe('Chi tiết - Bán ra - 7843/9492 hóa đơn');
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

  it('keeps finalize visible even when the last invoice count is still present', () => {
    expect(formatSourceJobProgress({
      status: 'running',
      stage: 'finalize',
      message: 'running:finalize',
      stage_percent: 25,
      scope_progress: { scope: 'detail', processed: 45, total: 45 },
    })).toBe('Đang hoàn tất và kiểm tra dữ liệu · 25% giai đoạn');
  });

  it('explains the background taxable-total enrichment after overview completes', () => {
    expect(formatSourceJobProgress({
      status: 'running',
      stage: 'overview',
      message: 'overview:taxable_total_enrichment_progress',
      scope_progress: { scope: 'overview', processed: 45, total: 45 },
    })).toBe('Đang tải ngầm chi tiết để bổ sung Tổng tiền chưa thuế');
  });

  it('keeps future protocol tokens out of the end-user UI', () => {
    expect(formatSourceJobProgress({
      status: 'running',
      stage: null,
      message: 'future_internal:step',
    })).toBe('Đang xử lý dữ liệu hóa đơn');
  });

  it('shows final cumulative overview and detail invoice totals', () => {
    expect(formatSourceJobProgress({
      status: 'completed',
      progress_totals: {
        overview: { processed: 600, total: 600 },
        detail: { processed: 600, total: 600 },
      },
    })).toBe('Đã tải xong - Tổng quan 600/600 · Chi tiết 600/600 hóa đơn');
  });
});
