import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { JOB_POLICY_NAMESPACE, createJobLifecycleBroker, idempotencyKey, validateIntent, validateJobId } = require('../../electron/job-lifecycle-broker.cjs');

const intent = {
  connection_id: 'conn_123456', date_from: '2026-01-01', date_to: '2026-01-31',
  directions: ['purchase'], query_types: ['query'], scopes: ['overview'], data_types: ['invoice'],
};

describe('local source job lifecycle IPC broker', () => {
  it('normalizes allowlisted source options and rejects empty, extra or unsafe input', () => {
    expect(validateIntent(intent)).toEqual(intent);
    expect(validateIntent({ ...intent, force_refresh: true })).toEqual({ ...intent, force_refresh: true });
    expect(validateIntent({ ...intent, refresh_latest_month: true })).toEqual({ ...intent, refresh_latest_month: true });
    expect(validateIntent({ ...intent, sync_mode: 'supplement' })).toEqual({ ...intent, sync_mode: 'supplement' });
    expect(() => validateIntent({ ...intent, sync_mode: 'replace' })).toThrow();
    expect(() => validateIntent({ ...intent, force_refresh: 'yes' })).toThrow();
    expect(() => validateIntent({ ...intent, detail_limit: 1 })).toThrow();
    expect(() => validateIntent({ ...intent, date_from: '2026-02-01' })).toThrow();
    expect(() => validateIntent({ ...intent, directions: [] })).toThrow();
    expect(() => validateIntent({ ...intent, data_types: [] })).toThrow();
    expect(() => validateIntent({ ...intent, connection_id: 'legacy-account-id' })).toThrow();
    expect(() => validateJobId('../secret')).toThrow();
  });

  it('validates direction-specific sync state reads before crossing IPC', async () => {
    const invoke = vi.fn().mockResolvedValue([]);
    const broker = createJobLifecycleBroker(() => ({ invoke }));
    await expect(broker.syncStates(['conn_123456'], 'purchase', '2026-01-01', '2026-01-31')).resolves.toMatchObject({ ok: true, data: [] });
    expect(invoke).toHaveBeenCalledWith('source.sync.states', { connection_ids: ['conn_123456'], direction: 'purchase', date_from: '2026-01-01', date_to: '2026-01-31' });
    await expect(broker.syncStates(['../bad'], 'purchase')).resolves.toMatchObject({ ok: false, error: { code: 'invalid_connection_id' } });
    await expect(broker.syncStates(['conn_123456'], 'both')).resolves.toMatchObject({ ok: false, error: { code: 'invalid_job_input' } });
  });

  it('derives the same idempotency key for equivalent normalized intents', () => {
    const reordered = { ...intent, directions: ['sold', 'purchase'], scopes: ['detail', 'overview'] };
    const canonical = validateIntent(reordered);
    expect(JOB_POLICY_NAMESPACE).toBe('desktop-source-v1');
    expect(idempotencyKey(canonical)).toBe(idempotencyKey(validateIntent({ ...reordered, directions: ['purchase', 'sold'], scopes: ['overview', 'detail'] })));
    expect(idempotencyKey(validateIntent({ ...intent, force_refresh: true }))).not.toBe(idempotencyKey(validateIntent({ ...intent, force_refresh: false })));
  });

  it('sends only source connection intent and idempotency key to the runtime', async () => {
    const invoke = vi.fn(async () => ({ job_id: 'job_1', connection_id: intent.connection_id, status: 'queued', stage: null }));
    const freshIntent = { ...intent, force_refresh: true, refresh_latest_month: true };
    const result = await createJobLifecycleBroker(() => ({ invoke })).start(freshIntent);
    expect(result).toMatchObject({ ok: true, data: { record: { job_id: 'job_1' }, accepted: { status: 'queued' } } });
    expect(invoke).toHaveBeenCalledTimes(1);
    expect(invoke).toHaveBeenCalledWith('source.jobs.start', {
      intent: freshIntent,
      idempotency_key: expect.stringMatching(/^desktop-source-v1-[a-f0-9]{64}$/),
    }, { timeoutMs: 90000 });
    expect(JSON.stringify(invoke.mock.calls)).not.toContain('password');
    expect(JSON.stringify(invoke.mock.calls)).not.toContain('accounts.secret');
  });

  it('does not decrypt account credentials in Electron when starting a job', async () => {
    const invoke = vi.fn(async () => ({ job_id: 'job_1', connection_id: intent.connection_id, status: 'queued', stage: null }));
    const protector = { decrypt: vi.fn(() => 'must-not-be-used') };
    await createJobLifecycleBroker(() => ({ invoke }), () => 'now', protector).start(intent);
    expect(protector.decrypt).not.toHaveBeenCalled();
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it('creates a new attempt when the deterministic source job is already terminal', async () => {
    const invoke = vi.fn(async (method: string, params: { idempotency_key?: string }) => {
      if (method === 'source.jobs.start' && !params.idempotency_key?.endsWith('-attempt-2')) {
        return { job_id: 'job_old', connection_id: intent.connection_id, status: 'failed', stage: null };
      }
      return { job_id: 'job_new', connection_id: intent.connection_id, status: 'queued', stage: null };
    });
    const broker = createJobLifecycleBroker(
      () => ({ invoke }), () => 'now', undefined, () => 'attempt-2',
    );
    const result = await broker.start(intent);
    expect(result).toMatchObject({ ok: true, data: { record: { job_id: 'job_new', status: 'queued' } } });
    expect(invoke.mock.calls.filter(([method]) => method === 'source.jobs.start')).toHaveLength(2);
    await broker.start(intent);
    expect(invoke.mock.calls.filter(([method]) => method === 'source.jobs.start')).toHaveLength(3);
    expect(invoke.mock.calls.filter(([method, params]) => method === 'source.jobs.start' && params.idempotency_key?.endsWith('-attempt-2'))).toHaveLength(2);
  });

  it.each(['resume', 'resumeAll', 'latestAll', 'status', 'summary', 'cancel', 'clear'])('routes %s through the local source runtime allowlist', async (method) => {
    const invoke = vi.fn().mockResolvedValue(null);
    const broker = createJobLifecycleBroker(() => ({ invoke }));
    await broker[method](...(method === 'resume' || method === 'resumeAll' || method === 'latestAll' || method === 'clear' ? [] : ['job_1']));
    if (method === 'clear') expect(invoke).not.toHaveBeenCalled();
    else if (method === 'resume' || method === 'resumeAll') expect(invoke).toHaveBeenCalledWith('source.jobs.resume_all');
    else if (method === 'latestAll') expect(invoke).toHaveBeenCalledWith('source.jobs.latest');
    else expect(invoke).toHaveBeenCalledWith(`source.jobs.${method}`, expect.any(Object));
  });

  it('resumes source jobs without relaunching a second crawler in Electron', async () => {
    const records = Array.from({ length: 3 }, (_, index) => ({
      job_id: `job_${index}`, connection_id: `conn_${index}`, intent: { ...intent, connection_id: `conn_${index}` }, status: 'queued',
    }));
    const invoke = vi.fn(async (method: string) => method === 'source.jobs.resume_all' ? records : null);
    const result = await createJobLifecycleBroker(() => ({ invoke })).resumeAll();
    expect(result).toMatchObject({ ok: true, data: records });
    expect(invoke).toHaveBeenCalledTimes(1);
  });

  it('sanitizes runtime failures without returning intent data', async () => {
    const broker = createJobLifecycleBroker(() => ({ invoke: vi.fn().mockRejectedValue(new Error('secret runtime payload')) }));
    const result = await broker.start(intent);
    expect(result).toEqual({ ok: false, error: { code: 'internal_error', message: 'Local runtime request could not be processed.' } });
    expect(JSON.stringify(result)).not.toContain('conn_123456');
  });

  it('returns a stable sanitized code for an empty selection', async () => {
    const result = await createJobLifecycleBroker(() => ({ invoke: vi.fn() })).start({ ...intent, scopes: [] });
    expect(result).toEqual({ ok: false, error: { code: 'empty_job_selection', status: 400, message: 'Job request is invalid.' } });
  });
});
