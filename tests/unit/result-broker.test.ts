import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createResultBroker, validateQuery } = require('../../electron/result-broker.cjs');

describe('result IPC broker', () => {
  it('normalizes safe cursor queries and rejects extra fields', () => {
    expect(validateQuery({ connection_id: 'account-1' })).toEqual({ connection_id: 'account-1', cursor: null, limit: 50, search: '', direction: null });
    expect(() => validateQuery({ connection_id: 'account-1', limit: 201 })).toThrow();
    expect(() => validateQuery({ connection_id: 'account-1', offset: 10 })).toThrow();
    expect(() => validateQuery({ connection_id: '../bad' })).toThrow();
  });

  it.each(['overview', 'details'])('routes %s only to its allowlisted runtime method', async (method) => {
    const invoke = vi.fn().mockResolvedValue({ items: [], pagination: { limit: 50, has_more: false, next_cursor: null } });
    const result = await createResultBroker(() => ({ invoke }))[method]({ connection_id: 'account-1' });
    expect(result.ok).toBe(true);
    expect(invoke).toHaveBeenCalledWith(`results.${method}`, expect.objectContaining({ connection_id: 'account-1' }));
  });
});
