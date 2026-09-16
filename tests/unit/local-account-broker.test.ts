import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createLocalAccountBroker } = require('../../electron/local-account-broker.cjs');

describe('local account broker', () => {
  it('delegates account creation to source account-connections without a second JS credential store', async () => {
    const account = {
      connection_id: 'conn_account_1', username: '0101234567', company_name: 'Synthetic Company', status: 'connected',
      token_generation: 0, created_at: 'now', updated_at: 'now', reused: false,
    };
    const invoke = vi.fn().mockResolvedValue(account);
    const broker = createLocalAccountBroker(() => ({ invoke }));
    const result = await broker.create({ username: '0101234567', password: 'portal-password' });
    expect(result).toEqual({ ok: true, data: account });
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(invoke).toHaveBeenCalledWith(
      'source.accounts.create',
      { username: '0101234567', password: 'portal-password' },
      { timeoutMs: 90000 },
    );
    expect(JSON.stringify(result)).not.toContain('portal-password');
  });

  it('preserves a sanitized source authentication code', async () => {
    const error = Object.assign(new Error('invalid_source_credentials'), { code: -32051 });
    const invoke = vi.fn().mockRejectedValue(error);
    const broker = createLocalAccountBroker(() => ({ invoke }));
    const result = await broker.create({ username: '0101234567', password: 'wrong-password' });
    expect(result).toMatchObject({ ok: false, error: { code: 'invalid_source_credentials' } });
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it('purges the source account and its local data instead of only revoking the UI row', async () => {
    const invoke = vi.fn().mockResolvedValue({ deleted: true });
    const broker = createLocalAccountBroker(() => ({ invoke }));
    const result = await broker.revoke('conn_account_1');
    expect(result).toEqual({ ok: true, data: null });
    expect(invoke).toHaveBeenCalledWith(
      'source.accounts.purge',
      { connection_id: 'conn_account_1' },
      { timeoutMs: 120000 },
    );
  });

  it('validates input before runtime access', async () => {
    const invoke = vi.fn();
    const broker = createLocalAccountBroker(() => ({ invoke }));
    const result = await broker.create({ username: '../unsafe', password: '' });
    expect(result).toMatchObject({ ok: false, error: { code: 'invalid_credentials' } });
    expect(invoke).not.toHaveBeenCalled();
  });

  it('refreshes and enforces the licensed MST before contacting the portal', async () => {
    const invoke = vi.fn();
    const authorizeUsername = vi.fn(async (username: string) => {
      if (username !== '0977030925') {
        throw Object.assign(new Error('denied'), { code: 'license_tax_code_denied' });
      }
    });
    const broker = createLocalAccountBroker(() => ({ invoke }), authorizeUsername);
    const denied = await broker.create({ username: '0101234567', password: 'portal-password' });
    expect(denied).toMatchObject({ ok: false, error: { code: 'license_tax_code_denied' } });
    expect(authorizeUsername).toHaveBeenCalledWith('0101234567');
    expect(invoke).not.toHaveBeenCalled();
  });

  it('applies the same licensed-MST check when reconnecting an account', async () => {
    const invoke = vi.fn();
    const authorizeUsername = vi.fn(async () => {
      throw Object.assign(new Error('denied'), { code: 'license_tax_code_denied' });
    });
    const broker = createLocalAccountBroker(() => ({ invoke }), authorizeUsername);
    const denied = await broker.reconnect(
      'conn_account_1',
      { username: '0240590044', password: 'portal-password' },
    );
    expect(denied).toMatchObject({ ok: false, error: { code: 'license_tax_code_denied' } });
    expect(authorizeUsername).toHaveBeenCalledWith('0240590044');
    expect(invoke).not.toHaveBeenCalled();
  });
});
