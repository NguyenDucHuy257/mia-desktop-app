import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createJobLifecycleBroker } = require('../../electron/job-lifecycle-broker.cjs');

describe('latest production job state', () => {
  it('routes latestAll to the production runtime without credentials', async () => {
    const records = [{ job_id: 'job_done', connection_id: 'conn_1', status: 'completed' }];
    const invoke = vi.fn(async (method: string) => method === 'source.jobs.latest' ? records : null);
    const result = await createJobLifecycleBroker(() => ({ invoke }), () => 'now').latestAll();
    expect(result).toMatchObject({ ok: true, data: records });
    expect(invoke).toHaveBeenCalledOnce();
    expect(invoke).toHaveBeenCalledWith('source.jobs.latest');
  });
});
