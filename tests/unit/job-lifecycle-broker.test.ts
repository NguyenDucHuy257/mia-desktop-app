import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createJobLifecycleBroker, idempotencyKey, validateIntent, validateJobId } = require('../../electron/job-lifecycle-broker.cjs');

const intent = {
  connection_id: 'conn_123456', date_from: '2026-01-01', date_to: '2026-01-31',
  directions: ['purchase'], query_types: ['query'], scopes: ['overview'], data_types: ['invoice'],
};

describe('offline job lifecycle IPC broker', () => {
  it('normalizes allowlisted options and rejects empty, extra or unsafe input', () => {
    expect(validateIntent(intent)).toEqual(intent);
    expect(validateIntent({ ...intent, force_refresh: true })).toEqual({ ...intent, force_refresh: true });
    expect(() => validateIntent({ ...intent, force_refresh: 'yes' })).toThrow();
    expect(() => validateIntent({ ...intent, detail_limit: 1 })).toThrow();
    expect(() => validateIntent({ ...intent, date_from: '2026-02-01' })).toThrow();
    expect(() => validateIntent({ ...intent, directions: [] })).toThrow();
    expect(() => validateIntent({ ...intent, data_types: [] })).toThrow();
    expect(() => validateJobId('../secret')).toThrow();
  });

  it('derives the same idempotency key for equivalent normalized intents', () => {
    const reordered = { ...intent, directions: ['sold', 'purchase'], scopes: ['detail', 'overview'] };
    const canonical = validateIntent(reordered);
    expect(idempotencyKey(canonical)).toBe(idempotencyKey(validateIntent({ ...reordered, directions: ['purchase', 'sold'], scopes: ['overview', 'detail'] })));
    expect(idempotencyKey(validateIntent({ ...intent, force_refresh: true }))).not.toBe(idempotencyKey(validateIntent({ ...intent, force_refresh: false })));
  });

  it('sends only validated data to the offline runtime', async () => {
    const invoke = vi.fn(async (method: string) => method === 'accounts.secret'
      ? { username: 'masked', encrypted_password: Buffer.from('cipher').toString('base64') }
      : { job_id: 'job_1', status: 'queued', stage: 'queued' });
    const freshIntent = { ...intent, force_refresh: true };
    const result = await createJobLifecycleBroker(() => ({ invoke }), () => 'now', { decrypt: () => 'memory-only' }).start(freshIntent);
    expect(result).toMatchObject({ ok: true, data: { record: { job_id: 'job_1' }, accepted: { status: 'queued' } } });
    expect(invoke).toHaveBeenCalledWith('source.jobs.start', expect.objectContaining({
        intent: freshIntent, idempotency_key: expect.stringMatching(/^desktop-v3-[a-f0-9]{64}$/),
    }), { timeoutMs: 15000 });
  });

  it('decrypts the account secret only in main and starts the local crawler', async () => {
    const invoke = vi.fn(async (method: string) => {
      if (method === 'source.jobs.start') return { job_id: 'job_1', connection_id: intent.connection_id, intent, status: 'queued', stage: 'queued' };
      if (method === 'accounts.secret') return { username: 'masked-user', encrypted_password: Buffer.from('cipher').toString('base64') };
      return { accepted: true };
    });
    const protector = { decrypt: vi.fn(() => 'plain-in-memory') };
    await createJobLifecycleBroker(() => ({ invoke }), () => 'now', protector).start(intent);
    expect(protector.decrypt).toHaveBeenCalledWith(Buffer.from('cipher'));
    expect(invoke).toHaveBeenCalledWith('source.jobs.start', expect.objectContaining({
      username: 'masked-user', password: 'plain-in-memory', intent,
    }), { timeoutMs: 15000 });
  });

  it('creates a new attempt when the deterministic job is already terminal', async () => {
    const invoke = vi.fn(async (method: string, params: { idempotency_key?: string }) => {
      if (method === 'source.jobs.start' && !params.idempotency_key?.endsWith('-attempt-2')) {
        return { job_id: 'job_old', connection_id: intent.connection_id, intent, status: 'failed', stage: 'failed' };
      }
      if (method === 'source.jobs.start') return { job_id: 'job_new', connection_id: intent.connection_id, intent, status: 'queued', stage: 'queued' };
      if (method === 'accounts.secret') return { username: 'masked', encrypted_password: Buffer.from('cipher').toString('base64') };
      return { accepted: true };
    });
    const broker = createJobLifecycleBroker(
      () => ({ invoke }), () => 'now', { decrypt: () => 'memory-only' }, () => 'attempt-2',
    );
    const result = await broker.start(intent);
    expect(result).toMatchObject({ ok: true, data: { record: { job_id: 'job_new', status: 'queued' } } });
    expect(invoke.mock.calls.filter(([method]) => method === 'source.jobs.start')).toHaveLength(2);
    await broker.start(intent);
    expect(invoke.mock.calls.filter(([method]) => method === 'source.jobs.start')).toHaveLength(3);
    expect(invoke.mock.calls.filter(([method, params]) => method === 'source.jobs.start' && params.idempotency_key?.endsWith('-attempt-2'))).toHaveLength(2);
  });

  it.each(['resume', 'resumeAll', 'latestAll', 'status', 'summary', 'cancel', 'clear'])('routes %s through the runtime allowlist', async (method) => {
    const invoke = vi.fn().mockResolvedValue(null);
    const broker = createJobLifecycleBroker(() => ({ invoke }), () => 'now');
    await broker[method](...(method === 'resume' || method === 'resumeAll' || method === 'latestAll' || method === 'clear' ? [] : ['job_1']));
    if (method === 'clear') expect(invoke).not.toHaveBeenCalled();
    else if (method === 'resume' || method === 'resumeAll') expect(invoke).toHaveBeenCalledWith('source.jobs.resume_all');
    else if (method === 'latestAll') expect(invoke).toHaveBeenCalledWith('source.jobs.latest');
    else expect(invoke).toHaveBeenCalledWith(`source.jobs.${method}`, expect.any(Object));
  });

  it('resumes production jobs without relaunching a second crawler', async () => {
    const records = Array.from({ length: 3 }, (_, index) => ({
      job_id: `job_${index}`, connection_id: `conn_${index}`, intent: { ...intent, connection_id: `conn_${index}` }, status: 'queued',
    }));
    const invoke = vi.fn(async (method: string) => method === 'source.jobs.resume_all' ? records : null);
    const result = await createJobLifecycleBroker(() => ({ invoke }), () => 'now', { decrypt: () => 'memory-only' }).resumeAll();
    expect(result).toMatchObject({ ok: true, data: records });
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it('sanitizes runtime failures without returning intent data', async () => {
    const broker = createJobLifecycleBroker(() => ({ invoke: vi.fn().mockRejectedValue(new Error('secret runtime payload')) }), undefined, { decrypt: () => 'memory-only' });
    const result = await broker.start(intent);
    expect(result).toEqual({ ok: false, error: { code: 'internal_error', message: 'MIA API request could not be processed.' } });
    expect(JSON.stringify(result)).not.toContain('conn_123456');
  });

  it('returns a stable sanitized code for an empty selection', async () => {
    const result = await createJobLifecycleBroker(() => ({ invoke: vi.fn() })).start({ ...intent, scopes: [] });
    expect(result).toEqual({ ok: false, error: { code: 'empty_job_selection', status: 400, message: 'Job request is invalid.' } });
  });
});
