import { describe, expect, it } from 'vitest';
import { resultExportErrorMessage } from '../../src/features/results/result-export-errors';

describe('result export error messages', () => {
  it('explains that another writer is active', () => {
    expect(resultExportErrorMessage({ code: 'artifact_task_active' })).toBe(
      'Đang có một tiến trình tải hoặc xuất file khác. Vui lòng chờ tiến trình hiện tại hoàn tất.',
    );
  });

  it('maps an empty date range to the specific invoice notice', () => {
    expect(resultExportErrorMessage(
      { code: 'result_export_empty' },
      '2026-01-01',
      '2026-01-31',
      ['overview', 'details'],
    )).toBe('Không tồn tại hóa đơn trong thời gian này.');
  });

  it('distinguishes an empty search/filter selection', () => {
    expect(resultExportErrorMessage(
      { code: 'result_export_empty' },
      '2026-01-01',
      '2026-01-31',
      ['details'],
      'không-có',
    )).toBe('Không tồn tại hóa đơn phù hợp với lựa chọn hiện tại.');
  });
});
