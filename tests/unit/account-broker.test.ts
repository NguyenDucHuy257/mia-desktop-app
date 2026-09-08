import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createAccountConnectionBroker, runBrokerCommand } = require('../../electron/account-connection-broker.cjs');

describe('account connection IPC broker', () => {
  it('passes a 12-digit household username unchanged to the runtime', async () => {
    const createConnection = vi.fn().mockResolvedValue({ connection_id: 'conn_household', username: '001234567890' });
    const broker = createAccountConnectionBroker(() => ({ createConnection }));
    const result = await broker.create({ username: '001234567890', password: 'test-password' });
    expect(result.ok).toBe(true);
    expect(createConnection).toHaveBeenCalled();
    expect(result.data.username).toBe('001234567890');
  });
  it('rejects invalid renderer input before calling the API client', async () => {
    const createConnection = vi.fn();
    const broker = createAccountConnectionBroker(() => ({ createConnection }));
    const result = await broker.create({ username: '../unsafe', password: '' });
    expect(result).toMatchObject({ ok: false, error: { code: 'invalid_credentials', status: 400 } });
    expect(createConnection).not.toHaveBeenCalled();
  });

  it('returns only a sanitized DTO to preload', async () => {
    const broker = createAccountConnectionBroker(() => ({
      createConnection: vi.fn().mockResolvedValue({
        connection_id: 'conn_1',
        username: '0101234567',
        status: 'active',
        token_generation: 1,
        created_at: 'now',
        updated_at: 'now',
        reused: false,
      }),
    }));
    const result = await broker.create({ username: '0101234567', password: 'not-returned' });
    expect(result.ok).toBe(true);
    expect(JSON.stringify(result)).not.toContain('not-returned');
  });

  it('returns a specific message for rejected portal credentials', async () => {
    const broker = createAccountConnectionBroker(() => ({
      createConnection: vi.fn().mockRejectedValue(new Error('invalid_source_credentials')),
    }));
    const result = await broker.create({ username: '0101234567', password: 'wrong-password' });
    expect(result).toMatchObject({
      ok: false,
      error: {
        code: 'invalid_source_credentials',
        message: 'Tên đăng nhập hoặc mật khẩu không đúng.',
      },
    });
  });

  it('returns an actionable VAT destination message instead of a generic runtime error', async () => {
    await expect(runBrokerCommand(async () => {
      throw new Error('vat_return_destination_not_writable');
    })).resolves.toEqual({
      ok: false,
      error: {
        code: 'vat_return_destination_not_writable',
        message: 'Không thể lưu tờ khai vào thư mục đã chọn. Vui lòng kiểm tra quyền ghi hoặc chọn thư mục khác.',
      },
    });
  });

  it('preserves a safe runtime code when the exception message is descriptive', async () => {
    const result = await runBrokerCommand(async () => {
      throw Object.assign(new Error('Runtime request timed out.'), { code: 'runtime_timeout' });
    });
    expect(result).toEqual({
      ok: false,
      error: { code: 'runtime_timeout', message: 'runtime_timeout' },
    });
  });

  it('distinguishes an open VAT workbook from a directory permission failure', async () => {
    const code = 'vat_return_destination_file_locked:{"filename":"To_khai_thue_GTGT_0109591907_01-10-2023_31-10-2023.xlsx","path":"D:\\\\KQ\\\\To_khai_thue_GTGT_0109591907_01-10-2023_31-10-2023.xlsx"}';
    const result = await runBrokerCommand(async () => { throw new Error(code); });
    expect(result).toEqual({
      ok: false,
      error: {
        code,
        message: 'Không thể ghi đè tờ khai thuế GTGT vì file đang được mở. Vui lòng đóng file:\nTo_khai_thue_GTGT_0109591907_01-10-2023_31-10-2023.xlsx\nSau đó thử xuất lại.\nĐường dẫn:\nD:\\KQ\\To_khai_thue_GTGT_0109591907_01-10-2023_31-10-2023.xlsx',
      },
    });
  });

  it('returns actionable masked purchase overview validation details', async () => {
    const code = 'vat_return_purchase_invalid:{"direction":"purchase","count":1,"examples":[{"identity":"abc123","invoice_number_masked":"***512","missing":["tgtthue"]}]}';
    const result = await runBrokerCommand(async () => { throw new Error(code); });
    expect(result).toEqual({
      ok: false,
      error: {
        code,
        message: 'Không thể xuất tờ khai vì còn 1 hóa đơn Mua vào thiếu dữ liệu Tổng quan bắt buộc. Ví dụ: ***512 (thiếu tgtthue). Hãy chạy Đồng bộ bổ sung Tổng quan.',
      },
    });
  });

  it('returns actionable masked purchase 8% line validation details', async () => {
    const code = 'vat_return_purchase_reduction_invalid:{"direction":"purchase","count":1,"examples":[{"line_identity":"abc123:2","missing":["tthue"]}]}';
    const result = await runBrokerCommand(async () => { throw new Error(code); });
    expect(result).toEqual({
      ok: false,
      error: {
        code,
        message: 'Không thể xuất tờ khai vì còn 1 dòng Chi tiết Mua vào 8% thiếu dữ liệu bắt buộc. Ví dụ: abc123:2 (thiếu tthue). Hãy chạy Đồng bộ bổ sung Chi tiết.',
      },
    });
  });
});
