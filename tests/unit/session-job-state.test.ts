import { describe, expect, it } from 'vitest';
import type { PersistedJob } from '../../src/lib/runtime-bridge';
import { currentSessionJobRecords } from '../../src/features/jobs/use-batch-job-lifecycle';

function record(status: string): PersistedJob {
  return {
    job_id: `job_${status}`,
    connection_id: `conn_${status}`,
    intent: {} as PersistedJob['intent'],
    idempotency_key: 'test',
    created_at: '2026-08-22T00:00:00Z',
    updated_at: '2026-08-22T00:00:00Z',
    status,
    overall_percent: status === 'completed' ? 100 : 0,
  };
}

describe('fresh desktop session job presentation', () => {
  it('does not hydrate historical completed, cancelled or failed progress', () => {
    const visible = currentSessionJobRecords([
      record('completed'),
      record('completed_with_warning'),
      record('cancelled'),
      record('failed'),
      record('queued'),
    ]);
    expect(visible.map((item) => item.status)).toEqual(['queued']);
    expect(visible.some((item) => item.overall_percent === 100)).toBe(false);
  });
});
