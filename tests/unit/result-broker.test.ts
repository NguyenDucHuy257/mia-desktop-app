import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { MAX_RESULT_CURSOR_LENGTH, createResultBroker, validateQuery } = require('../../electron/result-broker.cjs');

describe('result IPC broker', () => {
  it('normalizes safe cursor queries and enforces 50 rows per page', () => {
    expect(validateQuery({ connection_id: 'account-1' })).toEqual({
      connection_id: 'account-1', cursor: null, limit: 50, search: '', direction: null,
      query_type: null, date_from: null, date_to: null,
      column_filters: {}, exclusion: { keys: [], rules: [] },
    });
    expect(validateQuery({ connection_id: 'account-1', limit: 50 }).limit).toBe(50);
    expect(() => validateQuery({ connection_id: 'account-1', limit: 51 })).toThrow();
    expect(() => validateQuery({ connection_id: 'account-1', offset: 10 })).toThrow();
    expect(() => validateQuery({ connection_id: '../bad' })).toThrow();
  });

  it('round-trips long opaque source cursors used by page 2+', () => {
    const cursor = 'eyJ3cmFwcGVkIjoi' + 'x'.repeat(512);
    expect(validateQuery({ connection_id: 'account-1', cursor }).cursor).toBe(cursor);
    expect(() => validateQuery({
      connection_id: 'account-1', cursor: 'x'.repeat(MAX_RESULT_CURSOR_LENGTH + 1),
    })).toThrow();
  });

  it('accepts an explicit result range and exact source invoice type', () => {
    expect(validateQuery({
      connection_id: 'account-1', date_from: '2026-08-01', date_to: '2026-08-31',
      direction: 'sold', query_type: 'sco-query', search: 'abc',
    })).toMatchObject({
      connection_id: 'account-1', date_from: '2026-08-01', date_to: '2026-08-31',
      direction: 'sold', query_type: 'sco-query', search: 'abc',
    });
    expect(() => validateQuery({ connection_id: 'account-1', query_type: 'bad' })).toThrow();
    expect(() => validateQuery({ connection_id: 'account-1', date_from: '2026-08-01' })).toThrow();
    expect(() => validateQuery({ connection_id: 'account-1', date_from: '2026-09-01', date_to: '2026-08-31' })).toThrow();
  });

  it.each(['overview', 'details'])('routes %s only to its allowlisted runtime method', async (method) => {
    const invoke = vi.fn().mockResolvedValue({ items: [], pagination: { limit: 50, has_more: false, next_cursor: null } });
    const result = await createResultBroker(() => ({ invoke }))[method]({ connection_id: 'account-1' });
    expect(result.ok).toBe(true);
    expect(invoke).toHaveBeenCalledWith(`results.${method}`, expect.objectContaining({ connection_id: 'account-1', limit: 50 }));
  });

  it('validates column filters, symbolic exclusions and facet requests', async () => {
    const invoke = vi.fn().mockResolvedValue({ values: ['A'], truncated: false, column_type: 'text' });
    const broker = createResultBroker(() => ({ invoke }));
    await expect(broker.facets({
      connection_id: 'account-1', kind: 'details', column: 'ten',
      column_filters: { ten: { values: ['Dịch vụ'], search: 'dịch' } },
    })).resolves.toMatchObject({ ok: true });
    expect(invoke).toHaveBeenCalledWith('results.facets', expect.objectContaining({ kind: 'details', column: 'ten' }));
    expect(() => validateQuery({ connection_id: 'account-1', column_filters: { ten: { operator: 'drop table' } } })).toThrow();
  });
});
