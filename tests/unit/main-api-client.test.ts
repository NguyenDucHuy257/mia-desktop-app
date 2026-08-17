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

  it('uses the production job lifecycle routes and preserves idempotency', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ job_id: 'job-1', status: 'queued' }, { status: 202 }))
      .mockResolvedValueOnce(jsonResponse({ job_id: 'job-1', status: 'running' }))
      .mockResolvedValueOnce(jsonResponse({ job_id: 'job-1', status: 'running', stages: [] }))
      .mockResolvedValueOnce(jsonResponse({ job_id: 'job-1', status: 'cancelling' }));
    const client = new MiaMainApiClient({ baseUrl: 'https://crawl.example.com', getAccessToken: () => 'main-process-token', fetchImpl });
    await client.createJob({ connection_id: 'conn_123456' }, 'desktop-key-1');
    await client.getJob('job-1');
    await client.getJobSummary('job-1');
    await client.cancelJob('job-1');
    expect(fetchImpl.mock.calls.map(([url, init]) => [url, init?.method ?? 'GET'])).toEqual([
      ['https://crawl.example.com/v1/jobs', 'POST'],
      ['https://crawl.example.com/v1/jobs/job-1', 'GET'],
      ['https://crawl.example.com/v1/jobs/job-1/summary', 'GET'],
      ['https://crawl.example.com/v1/jobs/job-1/cancel', 'POST'],
    ]);
    expect(new Headers(fetchImpl.mock.calls[0]?.[1]?.headers).get('Idempotency-Key')).toBe('desktop-key-1');
  });

  it('keeps opaque result cursors on the production overview/detail routes', async () => {
    const response = { items: [], pagination: { limit: 200, has_more: false, next_cursor: null } };
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(response));
    const client = new MiaMainApiClient({ baseUrl: 'https://crawl.example.com', getAccessToken: () => 'main-process-token', fetchImpl });
    await client.getOverviewResults('job-1', 200, 'opaque+/=cursor');
    await client.getDetailResults('job-1', 50);
    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      'https://crawl.example.com/v1/jobs/job-1/results/overview?limit=200&cursor=opaque%2B%2F%3Dcursor',
      'https://crawl.example.com/v1/jobs/job-1/results/details?limit=50',
    ]);
  });
});
