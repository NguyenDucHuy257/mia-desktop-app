import { describe, expect, it } from 'vitest';
import {
  AccountGatewayError,
  accountErrorMessage,
  createAccountConnectionsInBatches,
  InMemoryAccountConnectionGateway,
} from '../../src/features/accounts/account-gateway';

describe('account connection gateway', () => {
  it('recovers distinct login failures after Electron serializes Errors', () => {
    expect(accountErrorMessage(new Error('[invalid_source_credentials] hidden'))).toBe('Tên đăng nhập hoặc mật khẩu không đúng.');
    expect(accountErrorMessage(new Error('[source_account_locked] hidden'))).toContain('bị khóa');
  });
  it('supports create, get, reconnect, reuse, and revoke in the browser demo adapter', async () => {
    let time = '2026-08-17T00:00:00.000Z';
    const gateway = new InMemoryAccountConnectionGateway(() => time, () => 'demo-connection-1');
    const created = await gateway.create({ username: '0101234567', password: 'first' });
    expect(created).toMatchObject({ connection_id: 'demo-connection-1', reused: false, token_generation: 1 });

    const reused = await gateway.create({ username: '0101234567', password: 'second' });
    expect(reused.reused).toBe(true);

    time = '2026-08-17T00:01:00.000Z';
    const reconnected = await gateway.reconnect('demo-connection-1', {
      username: '0101234567',
      password: 'third',
    });
    expect(reconnected).toMatchObject({ token_generation: 2, updated_at: time });
    expect(await gateway.get('demo-connection-1')).toEqual(reconnected);

    await gateway.revoke('demo-connection-1');
    await expect(gateway.get('demo-connection-1')).rejects.toBeInstanceOf(AccountGatewayError);
  });

  it('creates bulk local accounts strictly one at a time', async () => {
    let active = 0;
    let peak = 0;
    const gateway = {
      create: async ({ username }: { username: string }) => {
        active += 1;
        peak = Math.max(peak, active);
        await new Promise((resolve) => setTimeout(resolve, 1));
        active -= 1;
        return {
          connection_id: username,
          username,
          status: 'active',
          token_generation: 1,
          created_at: 'now',
          updated_at: 'now',
          reused: false,
        };
      },
      list: async () => [],
      get: async () => { throw new Error('not used'); },
      reconnect: async () => { throw new Error('not used'); },
      revoke: async () => undefined,
    };
    const credentials = Array.from({ length: 8 }, (_, index) => ({
      username: String(1_000_000_000 + index),
      password: 'password',
    }));

    const results = await createAccountConnectionsInBatches(gateway, credentials, 3);
    expect(results).toHaveLength(8);
    expect(peak).toBe(1);
  });
});
