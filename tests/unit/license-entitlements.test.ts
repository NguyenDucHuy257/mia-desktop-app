import { createRequire } from 'node:module';
import { describe, it, expect, vi } from 'vitest';
const require = createRequire(import.meta.url);
const { createLicenseRequestGuard, isLicenseDataRequest, validateEntitlements, localStateErrorCode } = require('../../electron/license/entitlements.cjs');
const { runBrokerCommand } = require('../../electron/account-connection-broker.cjs');
const trial = (ids = ['0123456789'], plan = 'TEST1') => ({ version: 1, plan, trial: true,
  max_tax_codes: Number(plan.replace('TEST', '') || 1), allowed_tax_codes: ids,
  date_from: '2026-08-01', date_to: '2026-08-31' });
const guard = (policy: any = trial()) => createLicenseRequestGuard(() => ({ active: true, reason: 'ok', entitlements: policy }));
const call = vi.fn(async (_: string, query: any) => ({ username: query.connection_id === 'conn_other' ? '0987654321' : '0123456789' }));
describe('license data boundary', () => {
  it('classifies every protected account/data entry point without blocking cleanup controls', () => {
    for (const method of [
      'source.accounts.create', 'source.accounts.reconnect', 'source.jobs.start',
      'results.overview', 'results.details', 'results.materialStart',
      'artifacts.export.start', 'artifacts.batch.start', 'artifacts.vat_return.export',
    ]) expect(isLicenseDataRequest(method)).toBe(true);
    for (const method of [
      'source.accounts.list', 'source.accounts.purge', 'source.jobs.cancel',
      'artifacts.export.status', 'artifacts.export.cancel', 'artifacts.export.failures',
    ]) expect(isLicenseDataRequest(method)).toBe(false);
  });
  it('rejects absent policy and trial configurations that silently broaden scope', () => {
    expect(() => validateEntitlements(null)).toThrow();
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
  it('accepts a limited plan whose dynamic quota is not filled yet and keeps it scoped', async () => {
    expect(validateEntitlements(trial([])).allowed_tax_codes).toEqual([]);
    const vip20 = { version: 1, plan: 'VIP20', trial: false, max_tax_codes: 20, allowed_tax_codes: [], date_from: null, date_to: null };
    expect(validateEntitlements(vip20)).toMatchObject({ max_tax_codes: 20, allowed_tax_codes: [] });
    // Nothing bound yet: every MST is still denied locally. The server binds
    // the MST when the account is created, then it appears in the list.
    await expect(guard(vip20)('source.accounts.create', { username: '0123456789' }, call)).rejects.toMatchObject({ code: 'license_tax_code_denied' });
    await expect(guard(trial([]))('results.details', { connection_id: 'conn_ok' }, call)).rejects.toMatchObject({ code: 'license_tax_code_denied' });
    await expect(guard({ ...vip20, allowed_tax_codes: ['0123456789'] })('source.accounts.create', { username: '0123456789' }, call)).resolves.toBeDefined();
    expect(() => validateEntitlements({ ...vip20, plan: 'VIP2', max_tax_codes: 2, allowed_tax_codes: ['1', '2', '3'] })).toThrow();
  });
  it('preserves existing numbered VIP policies without applying the TEST month', () => {
    expect(validateEntitlements({ version: 1, plan: 'VIP2', trial: false, max_tax_codes: 2, allowed_tax_codes: ['0123456789'], date_from: null, date_to: null })).toMatchObject({ plan: 'VIP2', trial: false });
  });
  it('VIP1 permits only its exact fifth-field MST entitlement', async () => {
    const vip1 = { version: 1, plan: 'VIP1', trial: false, max_tax_codes: 1, allowed_tax_codes: ['0240590043'], date_from: null, date_to: null };
    const vipGuard = guard(vip1);
    await expect(vipGuard('source.accounts.create', { username: '0240590043' }, call)).resolves.toBeDefined();
    await expect(vipGuard('source.accounts.create', { username: '0240590044' }, call)).rejects.toMatchObject({ code: 'license_tax_code_denied' });
  });
  it('accepts portal login formats beyond plain tax codes in allowed_tax_codes', async () => {
    // Error report 2026-10-01: VIP1 scoped to a delegated user such as 0303761733-U001.
    for (const id of ['0303761733-U001', '0104998537-u002', '0100109106-001', '0303761733.KT@x', '123456789012']) {
      const vip1 = { version: 1, plan: 'VIP1', trial: false, max_tax_codes: 1, allowed_tax_codes: [id], date_from: null, date_to: null };
      expect(validateEntitlements(vip1)).toMatchObject({ allowed_tax_codes: [id] });
      const vipGuard = guard(vip1);
      await expect(vipGuard('source.accounts.create', { username: id }, call)).resolves.toBeDefined();
      await expect(vipGuard('source.accounts.create', { username: '0303761733' }, call)).rejects.toMatchObject({ code: 'license_tax_code_denied' });
    }
    for (const id of ['', '0303761733 U001', '-0303761733', 'a'.repeat(65)]) {
      expect(() => validateEntitlements({ version: 1, plan: 'VIP1', trial: false, max_tax_codes: 1, allowed_tax_codes: [id], date_from: null, date_to: null }))
        .toThrowError(expect.objectContaining({ code: 'license_policy_invalid' }));
    }
  });
  it('names a local state write failure instead of blaming the key server', async () => {
    expect(localStateErrorCode({ reason: 'EPERM' })).toBe('license_local_state_failed');
    expect(localStateErrorCode({ reason: 'license_network_error' })).toBeNull();
    const local = createLicenseRequestGuard(() => ({ active: false, state: 'error', reason: 'EPERM' }));
    await expect(local('results.materialStatus', { connection_id: 'conn_ok' }, call)).rejects.toMatchObject({ code: 'license_local_state_failed' });
    const remote = createLicenseRequestGuard(() => ({ active: false, state: 'error', reason: 'license_network_error' }));
    await expect(remote('results.materialStatus', { connection_id: 'conn_ok' }, call)).rejects.toMatchObject({ code: 'license_policy_missing' });
  });
  it('returns a useful Vietnamese broker error instead of internal_error', async () => {
    const result = await runBrokerCommand(() => guard()('source.accounts.create', { username: '0987654321' }, call));
    expect(result.error.code).toBe('license_tax_code_denied');
    expect(result.error.message).toContain('MST');
  });
});
