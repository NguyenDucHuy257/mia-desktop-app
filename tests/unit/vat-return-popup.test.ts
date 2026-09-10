import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NoticeDialog } from '../../src/components/NoticeDialog';
import { loadVatReturnCoverage, vatReturnExportErrorFeedback } from '../../src/features/artifacts/VatReturnExportPage';

describe('VAT return popup semantics', () => {
  it('shows a readable confirmable warning for serialized reduction errors', () => {
    const feedback = vatReturnExportErrorFeedback(new Error('[vat_return_purchase_reduction_invalid:{"count":72,"examples":[]}] failure'));
    expect(feedback.kind).toBe('warning');
    expect(feedback.canContinue).toBe(true);
    expect(feedback.message).toContain('72 dòng');
    expect(feedback.message).not.toContain('examples');
    expect(feedback.message).not.toContain('vat_return_');
  });
  afterEach(() => vi.unstubAllGlobals());

  it('retries a cold transient coverage timeout without exposing a false error', async () => {
    const coverage = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error('timed out'), { code: 'runtime_timeout' }))
      .mockResolvedValueOnce({ accounts: [{ connection_id: 'conn_1' }] });
    vi.stubGlobal('window', {
      miaRuntime: { artifacts: { vatReturnCoverage: coverage } },
      setTimeout: globalThis.setTimeout,
    });

    await expect(loadVatReturnCoverage(['conn_1'], {
      dateFrom: '2026-01-01', dateTo: '2026-09-03',
    })).resolves.toEqual([{ connection_id: 'conn_1' }]);
    expect(coverage).toHaveBeenCalledTimes(2);
  });

  it('maps the broker writer lock to a warning instead of a database failure', () => {
    expect(vatReturnExportErrorFeedback({ code: 'artifact_task_active' })).toEqual({
      kind: 'warning',
      message: 'Đang có một tiến trình tải hoặc xuất file khác. Vui lòng chờ tiến trình hiện tại hoàn tất.',
    });
  });

  it.each([
    ['success', '✓'],
    ['warning', '!'],
    ['error', '×'],
    ['info', 'i'],
  ] as const)('renders the shared %s icon explicitly', (kind, icon) => {
    const html = renderToStaticMarkup(createElement(NoticeDialog, {
      kind, message: 'Nội dung', onClose: () => undefined,
    }));
    expect(html).toContain(`data-kind="${kind}"`);
    expect(html).toContain(`>${icon}</span>`);
  });

  it('renders a returned canonical path in a dedicated wrapping region', () => {
    const path = 'D:\\Kết quả có nhiều dấu cách\\Tên thư mục rất dài\\To_khai_thue_GTGT_0109591907_01-10-2023_31-10-2023.xlsx';
    const html = renderToStaticMarkup(createElement(NoticeDialog, {
      kind: 'success', message: 'Đã tạo tờ khai thuế GTGT thành công.', path,
      onClose: () => undefined,
    }));
    expect(html).toContain('class="notice-path-block"');
    expect(html).toContain('class="popup-path"');
    expect(html).toContain('Đường dẫn:');
    expect(html).toContain('01-10-2023_31-10-2023.xlsx');
  });

  it('classifies a locked workbook as an error and preserves its final path', () => {
    const path = 'D:\\KQ\\To_khai_thue_GTGT_0109591907_01-10-2023_31-10-2023.xlsx';
    const error = Object.assign(new Error('sanitized broker message'), {
      code: `vat_return_destination_file_locked:${JSON.stringify({ filename: path.split('\\').at(-1), path })}`,
    });
    expect(vatReturnExportErrorFeedback(error)).toEqual({
      kind: 'error',
      message: 'Không thể ghi đè tờ khai thuế GTGT vì file đang được mở.\nVui lòng đóng file:\nTo_khai_thue_GTGT_0109591907_01-10-2023_31-10-2023.xlsx\nSau đó thử xuất lại.',
      path,
    });
  });
});
