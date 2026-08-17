import { describe, expect, it, vi } from 'vitest';
import { MiaApiClient } from '../../src/lib/api/client';

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
}

describe('MiaApiClient', () => {
  it('does not send a service token to public health endpoints', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status: 'ok' }));
    const client = new MiaApiClient({ baseUrl: 'https://crawl.example.com/', getAccessToken: () => 'secret-token', fetchImpl });
    await client.live();
    const request = fetchImpl.mock.calls[0]?.[1];
    expect(new Headers(request?.headers).has('X-MIA-API-Key')).toBe(false);
  });

  it('matches the production connection and job contract', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ connection_id: 'conn_123456', username: '0101234567', status: 'active', token_generation: 1, created_at: 'now', updated_at: 'now', reused: false }, { status: 201 }))
      .mockResolvedValueOnce(jsonResponse({ job_id: 'job-1', status: 'queued', current_stage: null, worker_slot_id: null }, { status: 202 }));
    const client = new MiaApiClient({ baseUrl: 'https://crawl.example.com', getAccessToken: () => 'service-token-at-least-32-characters', fetchImpl });
    await client.createConnection('0101234567', 'password');
    await client.createJob({ connection_id: 'conn_123456', date_from: '2026-01-01', date_to: '2026-01-31', directions: ['purchase'], query_types: ['query'], result_scope: 'overview' }, 'desktop-job-0001');
    const [, init] = fetchImpl.mock.calls[1]!;
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('desktop-job-0001');
    expect(JSON.parse(String(init?.body))).toMatchObject({ connection_id: 'conn_123456', result_scope: 'overview' });
  });

  it('maps sanitized API errors without exposing request bodies', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ error: { code: 'account_busy', message: 'account already has an active job', request_id: 'req-1' } }, { status: 409 }));
    const client = new MiaApiClient({ baseUrl: 'https://crawl.example.com', getAccessToken: () => 'token', fetchImpl });
    await expect(client.getJob('job-1')).rejects.toEqual(expect.objectContaining({
      status: 409,
      code: 'account_busy',
      requestId: 'req-1',
    }));
  });

  it('uses the production summary route', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      job_id: 'job-1',
      status: 'running',
      warning_count: 0,
      stages: [],
      coverage_plan: {},
      work: {},
      post_processing: {},
    }));
    const client = new MiaApiClient({
      baseUrl: 'https://crawl.example.com',
      getAccessToken: () => 'token',
      fetchImpl,
    });

    await client.getJobSummary('job-1');

    expect(fetchImpl.mock.calls[0]?.[0]).toBe('https://crawl.example.com/v1/jobs/job-1/summary');
  });
});
