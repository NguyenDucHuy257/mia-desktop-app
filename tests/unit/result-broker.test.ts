import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createResultBroker, validateQuery } = require('../../electron/result-broker.cjs');

describe('result IPC broker', () => {
  it('normalizes safe cursor queries and rejects extra fields', () => {
    expect(validateQuery({ connection_id: 'account-1' })).toEqual({
      connection_id: 'account-1', cursor: null, limit: 50, search: '', direction: null,
      date_from: null, date_to: null, column_filters: {},
      exclude_business_keys: [], include_meta: false,
    });
    expect(() => validateQuery({ connection_id: 'account-1', limit: 201 })).toThrow();
    expect(() => validateQuery({ connection_id: 'account-1', offset: 10 })).toThrow();
    expect(() => validateQuery({ connection_id: '../bad' })).toThrow();
  });

  it('accepts an explicit result range, column filters and download-only exclusions', () => {
    expect(validateQuery({
      connection_id: 'account-1', date_from: '2026-08-01', date_to: '2026-08-31',
      direction: 'sold', search: 'abc', column_filters: { nmten: 'Công ty A', dgia: '1.000' },
      exclude_business_keys: ['010|AA|1|1', '010|AA|1|1'], include_meta: true,
    })).toMatchObject({
      connection_id: 'account-1', date_from: '2026-08-01', date_to: '2026-08-31',
      direction: 'sold', search: 'abc',
      column_filters: { nmten: 'Công ty A', dgia: '1.000' },
      exclude_business_keys: ['010|AA|1|1'], include_meta: true,
    });
    expect(() => validateQuery({ connection_id: 'account-1', date_from: '2026-08-01' })).toThrow();
    expect(() => validateQuery({ connection_id: 'account-1', date_from: '2026-09-01', date_to: '2026-08-31' })).toThrow();
    expect(() => validateQuery({ connection_id: 'account-1', column_filters: { nmten: 123 } })).toThrow();
    expect(() => validateQuery({ connection_id: 'account-1', exclude_business_keys: ['ok', 1] })).toThrow();
  });

  it.each(['overview', 'details'])('routes %s only to its allowlisted runtime method', async (method) => {
    const invoke = vi.fn().mockResolvedValue({ items: [], pagination: { limit: 50, has_more: false, next_cursor: null } });
    const result = await createResultBroker(() => ({ invoke }))[method]({ connection_id: 'account-1' });
    expect(result.ok).toBe(true);
    expect(invoke).toHaveBeenCalledWith(`results.${method}`, expect.objectContaining({ connection_id: 'account-1' }));
  });
});
