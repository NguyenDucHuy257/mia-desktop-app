import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createJobLifecycleBroker, validateIntent, validateJobId } = require('../../electron/job-lifecycle-broker.cjs');

const intent = {
  connection_id: 'conn_123456', date_from: '2026-01-01', date_to: '2026-01-31',
  directions: ['purchase'], query_types: ['query'], result_scope: 'overview',
};

function memoryStore(initial: unknown = null) {
  let value = initial;
  return { read: () => value, write: (next: unknown) => { value = next; }, clear: () => { value = null; } };
}

describe('job lifecycle IPC broker', () => {
  it('validates the exact backend job options and rejects extra or unsafe input', () => {
    expect(validateIntent(intent)).toMatchObject({ force_refresh: false, result_scope: 'overview' });
    expect(() => validateIntent({ ...intent, detail_limit: 1 })).toThrow();
    expect(() => validateIntent({ ...intent, date_from: '2026-02-01' })).toThrow();
    expect(() => validateIntent({ ...intent, directions: ['purchase', 'purchase'] })).toThrow();
    expect(() => validateIntent({ ...intent, include_xml: true })).toThrow();
    expect(() => validateJobId('../secret')).toThrow();
  });

  it('persists idempotency before create and reuses it after restart', async () => {
    const store = memoryStore();
    const createJob = vi.fn().mockResolvedValue({ job_id: 'job-1', status: 'queued' });
    const client = { createJob };
    const first = createJobLifecycleBroker(() => client, store, () => 'now', () => 'fixed-id');
    const one = await first.start(intent);
    const restarted = createJobLifecycleBroker(() => client, store, () => 'later', () => 'new-id');
    const two = await restarted.start(intent);
    expect(one.data.record.idempotency_key).toBe('desktop-fixed-id');
    expect(two.data.record.job_id).toBe('job-1');
    expect(createJob.mock.calls.map((call) => call[1])).toEqual(['desktop-fixed-id', 'desktop-fixed-id']);
  });

  it('recovers a crash between persisting intent and receiving POST response', async () => {
    const store = memoryStore({ job_id: null, connection_id: intent.connection_id, intent: validateIntent(intent), idempotency_key: 'desktop-crash-key', created_at: 'before', updated_at: 'before' });
    const createJob = vi.fn().mockResolvedValue({ job_id: 'job-resumed', status: 'queued' });
    const broker = createJobLifecycleBroker(() => ({ createJob }), store, () => 'after');
    await expect(broker.resume()).resolves.toMatchObject({ ok: true, data: { job_id: 'job-resumed', idempotency_key: 'desktop-crash-key' } });
    expect(createJob).toHaveBeenCalledWith(expect.any(Object), 'desktop-crash-key');
  });

  it('sanitizes API failures and never returns request input', async () => {
    const broker = createJobLifecycleBroker(() => ({ createJob: vi.fn().mockRejectedValue(new Error('secret body')) }), memoryStore());
    const result = await broker.start(intent);
    expect(result).toEqual({ ok: false, error: { code: 'internal_error', message: 'MIA API request could not be processed.' } });
    expect(JSON.stringify(result)).not.toContain('conn_123456');
  });

  it.each(['queued', 'running', 'cancelling', 'completed'])('passes cancel responses for %s through the allowlist', async (status) => {
    const cancelJob = vi.fn().mockResolvedValue({ job_id: 'job-1', status, stage: null, overall_percent: 0, current_month: null, updated_at: 'now', error: null });
    const broker = createJobLifecycleBroker(() => ({ cancelJob }), memoryStore());
    await expect(broker.cancel('job-1')).resolves.toMatchObject({ ok: true, data: { status } });
  });
});
