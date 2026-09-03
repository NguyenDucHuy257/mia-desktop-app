import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { NoticeDialog } from '../../src/components/NoticeDialog';
import { vatReturnExportErrorFeedback } from '../../src/features/artifacts/VatReturnExportPage';

describe('VAT return popup semantics', () => {
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
