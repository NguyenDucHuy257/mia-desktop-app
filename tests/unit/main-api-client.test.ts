import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const {
  MiaMainApiClient,
  assertSafeApiBaseUrl,
} = require('../../electron/mia-api-client.cjs');

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

describe('Electron main-process MIA API client', () => {
  it('allows HTTPS and loopback HTTP but rejects remote cleartext API URLs', () => {
    expect(assertSafeApiBaseUrl('https://crawl.example.com/')).toBe('https://crawl.example.com');
    expect(assertSafeApiBaseUrl('http://127.0.0.1:8080/')).toBe('http://127.0.0.1:8080');
    expect(() => assertSafeApiBaseUrl('http://crawl.example.com')).toThrow(/HTTPS/);
  });

  it('matches all four account-connections routes and keeps the token in main', async () => {
    const connection = {
      connection_id: 'conn_123456',
      username: '0101234567',
      status: 'active',
      token_generation: 1,
      created_at: 'now',
      updated_at: 'now',
      reused: false,
    };
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(connection, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse(connection))
      .mockResolvedValueOnce(jsonResponse({ ...connection, token_generation: 2 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const client = new MiaMainApiClient({
      baseUrl: 'https://crawl.example.com',
      getAccessToken: () => 'main-process-token',
      fetchImpl,
    });

    await client.createConnection({ username: '0101234567', password: 'password' });
    await client.getConnection('conn_123456');
    await client.reconnectConnection('conn_123456', { username: '0101234567', password: 'password-2' });
    await client.revokeConnection('conn_123456');

    expect(fetchImpl.mock.calls.map(([url, init]) => [url, init?.method ?? 'GET'])).toEqual([
      ['https://crawl.example.com/v1/account-connections', 'POST'],
      ['https://crawl.example.com/v1/account-connections/conn_123456', 'GET'],
      ['https://crawl.example.com/v1/account-connections/conn_123456/reconnect', 'POST'],
      ['https://crawl.example.com/v1/account-connections/conn_123456', 'DELETE'],
    ]);
    for (const [, init] of fetchImpl.mock.calls) {
      expect(new Headers(init?.headers).get('X-MIA-API-Key')).toBe('main-process-token');
    }
  });

  it('sanitizes transport failures without including credentials', async () => {
    const client = new MiaMainApiClient({
      baseUrl: 'https://crawl.example.com',
      getAccessToken: () => 'main-process-token',
      fetchImpl: vi.fn<typeof fetch>().mockRejectedValue(new Error('password=super-secret')),
    });

    await expect(client.createConnection({ username: '0101234567', password: 'super-secret' }))
      .rejects.toEqual(expect.objectContaining({ code: 'network_error' }));
    await client.createConnection({ username: '0101234567', password: 'super-secret' }).catch((error: Error) => {
      expect(error.message).not.toContain('super-secret');
    });
  });
});
