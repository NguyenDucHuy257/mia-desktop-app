import { describe, expect, it } from 'vitest';
import { backoffDelay, initialJobState, jobReducer, TERMINAL_JOB_STATUSES } from '../../src/features/jobs/job-state-machine';
import type { JobStatus } from '../../src/lib/api/contracts';

const statuses: JobStatus[] = ['queued', 'waiting_account', 'running', 'cancelling', 'completed', 'completed_with_warning', 'failed', 'cancelled', 'abandoned'];

describe('job lifecycle state machine', () => {
  it.each(statuses)('maps backend transition %s to the correct phase', (status) => {
    const next = jobReducer(initialJobState, { type: 'status', value: {
      job_id: 'job-1', status, stage: null, overall_percent: 20, current_month: null,
      updated_at: 'now', error: null,
    } });
    expect(next.phase).toBe(TERMINAL_JOB_STATUSES.has(status) ? 'terminal' : 'polling');
  });

  it('clamps both progress levels to 0..100', () => {
    const next = jobReducer(initialJobState, { type: 'status', value: {
      job_id: 'job-1', status: 'running', stage: 'detail', overall_percent: 140,
      current_month: { key: '2026-01', index: 1, total: 1, processed: 2, planned: 1, percent: -4 },
      updated_at: 'now', error: null,
    } });
    expect(next.status?.overall_percent).toBe(100);
    expect(next.status?.current_month?.percent).toBe(0);
  });

  it('uses bounded exponential backoff and recovers to polling', () => {
    expect([1, 2, 3, 9].map((attempt) => backoffDelay(attempt))).toEqual([2000, 4000, 8000, 30000]);
    const retrying = jobReducer(initialJobState, { type: 'retry', count: 2 });
    const recovered = jobReducer(retrying, { type: 'status', value: {
      job_id: 'job-1', status: 'running', stage: 'overview', overall_percent: 10,
      current_month: null, updated_at: 'now', error: null,
    } });
    expect(recovered).toMatchObject({ phase: 'polling', retryCount: 0, message: null });
  });

  it('does not let an older poll response overwrite a newer transition', () => {
    const newer = jobReducer(initialJobState, { type: 'status', value: {
      job_id: 'job-1', status: 'cancelling', stage: 'cancelling', overall_percent: 60,
      current_month: null, event_sequence: 4, updated_at: '2026-08-18T00:04:00Z', error: null,
    } });
    const stale = jobReducer(newer, { type: 'status', value: {
      job_id: 'job-1', status: 'running', stage: 'running', overall_percent: 40,
      current_month: null, event_sequence: 3, updated_at: '2026-08-18T00:03:00Z', error: null,
    } });
    expect(stale).toBe(newer);
  });
});
