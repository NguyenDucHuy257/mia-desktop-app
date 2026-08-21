import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createLocalAccountBroker } = require('../../electron/local-account-broker.cjs');

describe('local account broker', () => {
  it('encrypts before crossing into Python and sanitizes the returned DTO', async () => {
    const account = {
      connection_id: 'account-1', username: '0101234567', company_name: 'Synthetic Company', status: 'connected',
      token_generation: 0, created_at: 'now', updated_at: 'now', reused: false,
    };
    const invoke = vi.fn(async (method: string) => method === 'crawler.verify_account' ? { company_name: 'Synthetic Company' } : account);
    const encrypt = vi.fn().mockReturnValue(Buffer.from('dpapi-ciphertext'));
    const broker = createLocalAccountBroker(() => ({ invoke }), { encrypt }, () => 'now', () => 'account-1');
    const result = await broker.create({ username: '0101234567', password: 'portal-password' });
    expect(result.ok).toBe(true);
    expect(encrypt).toHaveBeenCalledWith('portal-password');
    expect(invoke).toHaveBeenNthCalledWith(1, 'crawler.verify_account', {
      username: '0101234567', password: 'portal-password',
    }, { timeoutMs: 90000 });
    expect(invoke).toHaveBeenCalledWith('accounts.create', expect.objectContaining({
      encrypted_password: Buffer.from('dpapi-ciphertext').toString('base64'),
    }));
    expect(invoke).toHaveBeenLastCalledWith('accounts.update_company', {
      account_id: 'account-1', company_name: 'Synthetic Company', timestamp: 'now',
    });
    expect(JSON.stringify(result)).not.toContain('portal-password');
    expect(JSON.stringify(result)).not.toContain('dpapi-ciphertext');
  });

  it('does not persist an account when portal verification fails', async () => {
    const error = Object.assign(new Error('authentication_failed'), { code: -32051 });
    const invoke = vi.fn().mockRejectedValue(error);
    const broker = createLocalAccountBroker(() => ({ invoke }), { encrypt: vi.fn() });
    const result = await broker.create({ username: '0101234567', password: 'wrong-password' });
    expect(result).toMatchObject({ ok: false, error: { code: 'authentication_failed' } });
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(invoke).not.toHaveBeenCalledWith('accounts.create', expect.anything());
  });

  it('purges the account instead of only revoking the UI row', async () => {
    const invoke = vi.fn().mockResolvedValue({ deleted: true });
    const broker = createLocalAccountBroker(() => ({ invoke }), { encrypt: vi.fn() });
    const result = await broker.revoke('account-1');
    expect(result).toEqual({ ok: true, data: null });
    expect(invoke).toHaveBeenCalledWith(
      'accounts.purge',
      { account_id: 'account-1' },
      { timeoutMs: 30000 },
    );
  });

  it('validates input before encryption or runtime access', async () => {
    const invoke = vi.fn();
    const encrypt = vi.fn();
    const broker = createLocalAccountBroker(() => ({ invoke }), { encrypt });
    const result = await broker.create({ username: '../unsafe', password: '' });
    expect(result).toMatchObject({ ok: false, error: { code: 'invalid_credentials' } });
    expect(encrypt).not.toHaveBeenCalled();
    expect(invoke).not.toHaveBeenCalled();
  });
});
