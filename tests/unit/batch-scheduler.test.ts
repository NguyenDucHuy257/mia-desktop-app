import { describe, expect, it } from 'vitest';
import type { CreateJobRequest, JobStatusResponse } from '../../src/lib/api/contracts';
import { aggregateProgress, normalizeBatch } from '../../src/features/jobs/batch-scheduler';

const intent = (connection_id: string): CreateJobRequest => ({
  connection_id, date_from: '2026-01-01', date_to: '2026-01-31', directions: ['purchase'], query_types: ['query'], scopes: ['overview'], data_types: ['invoice'],
});

describe('multi-account batch scheduler', () => {
  it.each([0, 1, 2, 50])('accepts %i distinct accounts', (count) => {
    expect(normalizeBatch(Array.from({ length: count }, (_, index) => intent(`conn_${index}`)))).toHaveLength(count);
  });

  it('deduplicates accounts and rejects more than 50', () => {
    expect(normalizeBatch([intent('conn_1'), intent('conn_1')])).toHaveLength(1);
    expect(() => normalizeBatch(Array.from({ length: 51 }, (_, index) => intent(`conn_${index}`)))).toThrow('batch_too_large');
  });

  it('clamps aggregate progress and never counts a missing child as complete', () => {
    const status = (overall_percent: number) => ({ overall_percent } as JobStatusResponse);
    expect(aggregateProgress([])).toBe(0);
    expect(aggregateProgress([status(20), undefined, status(100)])).toBe(40);
    expect(aggregateProgress([status(200)])).toBe(100);
  });
});
