import { createRequire } from 'node:module';
import { describe, it, expect, vi } from 'vitest';
const require = createRequire(import.meta.url);
const { createLicenseRequestGuard, validateEntitlements } = require('../../electron/license/entitlements.cjs');
const { runBrokerCommand } = require('../../electron/account-connection-broker.cjs');
const trial = (ids = ['0123456789'], plan = 'TEST1') => ({ version: 1, plan, trial: true,
  max_tax_codes: Number(plan.replace('TEST', '') || 1), allowed_tax_codes: ids,
  date_from: '2026-08-01', date_to: '2026-08-31' });
const guard = (policy = trial()) => createLicenseRequestGuard(() => ({ active: true, reason: 'ok', entitlements: policy }));
const call = vi.fn(async (_: string, query: any) => ({ username: query.connection_id === 'conn_other' ? '0987654321' : '0123456789' }));
describe('license data boundary', () => {
  it('rejects absent policy and trial configurations that silently broaden scope', () => {
    expect(() => validateEntitlements(null)).toThrow();
    expect(() => validateEntitlements(trial([]))).toThrow();
    expect(() => validateEntitlements(trial(['0123456789', '0987654321']))).toThrow();
    expect(() => validateEntitlements({ ...trial(), date_to: '2026-09-01' })).toThrow();
  });
  it('TEST1 cannot import a second MST or its branch, TEST2 allows its two exact MSTs', async () => {
    for (const username of ['0987654321', '0123456789-001']) {
      await expect(guard()('source.accounts.create', { username }, call)).rejects.toMatchObject({ code: 'license_tax_code_denied' });
    }
    await expect(guard(trial(['0123456789', '0987654321'], 'TEST2'))('source.accounts.create', { username: '0987654321' }, call)).resolves.toBeDefined();
  });
  it.each(['artifacts.export', 'artifacts.export.start', 'artifacts.batch.start', 'results.details', 'results.materialStart'])('checks saved accounts and explicit ranges for %s', async (method) => {
    await expect(guard()(method, { connection_id: 'conn_other', date_from: '2026-08-01', date_to: '2026-08-31' }, call)).rejects.toMatchObject({ code: 'license_tax_code_denied' });
    await expect(guard()(method, { connection_id: 'conn_ok', date_from: '2026-01-01', date_to: '2026-08-31' }, call)).rejects.toMatchObject({ code: 'license_date_denied' });
    expect(await guard()(method, { connection_id: 'conn_ok' }, call)).toMatchObject({ date_from: '2026-08-01', date_to: '2026-08-31' });
  });
  it('checks every account in a cached batch export', async () => {
    await expect(guard()('artifacts.export.start', { connection_ids: ['conn_ok', 'conn_other'] }, call)).rejects.toMatchObject({ code: 'license_tax_code_denied' });
  });
  it('checks nested sync intent and preserves options', async () => {
    const request = { intent: { connection_id: 'conn_ok', date_from: '2026-08-01', date_to: '2026-08-31', scopes: ['detail'] }, idempotency_key: 'synthetic' };
    expect(await guard()('source.jobs.start', request, call)).toEqual(request);
    await expect(guard()('source.jobs.start', { ...request, intent: { ...request.intent, date_to: '2026-09-01' } }, call)).rejects.toMatchObject({ code: 'license_date_denied' });
  });
  it('does not restrict dates for paid keys, and never blocks cancellation', async () => {
    const paid = { version: 1, plan: 'V', trial: false, max_tax_codes: null, allowed_tax_codes: [], date_from: null, date_to: null };
    const request = { connection_id: 'conn_other', date_from: '2020-01-01', date_to: '2026-09-10' };
    expect(await createLicenseRequestGuard(() => ({ active: true, reason: 'ok', entitlements: paid }))('results.details', request, call)).toEqual(request);
    expect(await guard()('source.jobs.cancel', { job_id: 'old' }, call)).toEqual({ job_id: 'old' });
  });
  it('preserves existing numbered VIP policies without applying the TEST month', () => {
    expect(validateEntitlements({ version: 1, plan: 'VIP2', trial: false, max_tax_codes: 2, allowed_tax_codes: ['0123456789'], date_from: null, date_to: null })).toMatchObject({ plan: 'VIP2', trial: false });
  });
  it('returns a useful Vietnamese broker error instead of internal_error', async () => {
    const result = await runBrokerCommand(() => guard()('source.accounts.create', { username: '0987654321' }, call));
    expect(result.error.code).toBe('license_tax_code_denied');
    expect(result.error.message).toContain('MST');
  });
});
