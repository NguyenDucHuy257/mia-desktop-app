import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createAccountConnectionBroker } = require('../../electron/account-connection-broker.cjs');

describe('account connection IPC broker', () => {
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
});