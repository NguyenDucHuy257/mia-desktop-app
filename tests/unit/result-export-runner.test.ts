import { describe, expect, it, vi } from 'vitest';
import type { ArtifactExportRequest, RuntimeExportProgress } from '../../src/lib/runtime-bridge';
import {
  aggregateBulkPercent,
  runResultExports,
  type ExcelExportProgress,
  type ExportLane,
} from '../../src/features/results/result-export-runner';

const request = (id: string): ArtifactExportRequest => ({
  destination: 'C:\\MIA',
  connection_ids: [id],
  kinds: ['excel'],
  result_scopes: ['details'],
  date_from: '2026-08-01',
  date_to: '2026-08-31',
});

function gateway(steps: Array<RuntimeExportProgress[] | Error>) {
  let listener: ((progress: RuntimeExportProgress) => void) | undefined;
  let call = 0;
  return {
    onExportProgress(callback: (progress: RuntimeExportProgress) => void) {
      listener = callback;
      return () => { listener = undefined; };
    },
    export: vi.fn(async () => {
      const step = steps[call++];
      if (step instanceof Error) throw step;
      for (const progress of step) listener?.(progress);
      return { count: 1, files: [`result-${call}.xlsx`] };
    }),
  };
}

const event = (percent: number, phase: RuntimeExportProgress['phase'] = 'write_rows'): RuntimeExportProgress => ({
  status: percent === 100 ? 'completed' : 'running',
  scope: 'details',
  phase,
  processed: percent,
  total: 100,
  percent,
});

describe('real result export progress runner', () => {
  it('starts at zero, stays monotonic and ends at 100 for one export', async () => {
    const updates: ExcelExportProgress[] = [];
    const lane: ExportLane = { owner: null };
    const summary = await runResultExports(
      'results', [request('conn_1')], gateway([[event(0, 'query'), event(37), event(91, 'save'), event(100, 'completed')]]), lane,
      (progress) => updates.push(progress),
    );

    expect(updates[0].percent).toBe(0);
    expect(updates.at(-1)).toMatchObject({ active: false, percent: 100, phase: 'completed' });
    expect(updates.every((value) => value.percent >= 0 && value.percent <= 100)).toBe(true);
    expect(updates.every((value, index) => index === 0 || value.percent >= updates[index - 1].percent)).toBe(true);
    expect(summary).toMatchObject({ count: 1, completed: 1, failures: [] });
    expect(lane.owner).toBeNull();
  });

  it('aggregates current account work without resetting between bulk accounts', async () => {
    const updates: ExcelExportProgress[] = [];
    await runResultExports(
      'bulk', [request('conn_1'), request('conn_2')],
      gateway([[event(50), event(100, 'completed')], [event(50), event(100, 'completed')]]),
      { owner: null }, (progress) => updates.push(progress),
    );
    const accountTwo = updates.filter((value) => value.accountIndex === 2 && value.active);
    expect(accountTwo[0].percent).toBe(50);
    expect(accountTwo.some((value) => value.percent === 75)).toBe(true);
    expect(updates.at(-1)?.percent).toBe(100);
    expect(updates.every((value, index) => index === 0 || value.percent >= updates[index - 1].percent)).toBe(true);
  });

  it('continues after a failed bulk account and preserves the failure summary', async () => {
    const failure = Object.assign(new Error('empty'), { code: 'result_export_empty' });
    const exporter = gateway([failure, [event(40), event(100, 'completed')]]);
    const updates: ExcelExportProgress[] = [];
    const summary = await runResultExports(
      'bulk', [request('conn_1'), request('conn_2')], exporter, { owner: null },
      (progress) => updates.push(progress),
    );
    expect(exporter.export).toHaveBeenCalledTimes(2);
    expect(summary).toMatchObject({ completed: 2, count: 1 });
    expect(summary.failures).toHaveLength(1);
    expect(updates.at(-1)).toMatchObject({ active: false, percent: 100 });
  });

  it('keeps the global lane mutually exclusive and releases it after failure', async () => {
    let release!: () => void;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    const firstGateway = {
      onExportProgress: () => () => undefined,
      export: vi.fn(async () => { await pending; return { count: 1, files: ['one.xlsx'] }; }),
    };
    const lane: ExportLane = { owner: null };
    const first = runResultExports('results', [request('conn_1')], firstGateway, lane, () => undefined);
    await expect(runResultExports('bulk', [request('conn_2')], gateway([[]]), lane, () => undefined))
      .rejects.toMatchObject({ code: 'result_export_busy_results' });
    release();
    await first;

    const failedUpdates: ExcelExportProgress[] = [];
    await runResultExports('results', [request('conn_3')], gateway([new Error('failed')]), lane, (value) => failedUpdates.push(value));
    expect(lane.owner).toBeNull();
    expect(failedUpdates.at(-1)).toMatchObject({ active: false, percent: 100 });
  });

  it('clamps account fractions to the valid percentage range', () => {
    expect(aggregateBulkPercent(0, 2, -10)).toBe(0);
    expect(aggregateBulkPercent(1, 2, 50)).toBe(75);
    expect(aggregateBulkPercent(2, 2, 200)).toBe(100);
  });
});
