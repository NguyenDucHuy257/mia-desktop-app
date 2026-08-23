import { describe, expect, it, vi } from 'vitest';
import { loadArtifactSnapshots } from '../../src/features/artifacts/XmlHtmlPage';

describe('unified artifact account workflow', () => {
  it('chunks local coverage snapshots without starting any invoice job', async () => {
    const snapshot = vi.fn(async (request: { connection_ids: string[] }) => ({
      accounts: request.connection_ids.map((connection_id) => ({
        connection_id, ready: true, missing_ranges: [], total: 0,
        cached: { xml: 0, html: 0, pdf: 0 },
      })),
    }));
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { miaRuntime: { artifacts: { snapshot }, jobs: { start: vi.fn() } } },
    });
    const ids = Array.from({ length: 101 }, (_, index) => `conn_${index}`);

    const result = await loadArtifactSnapshots({
      connection_ids: ids, directions: ['purchase', 'sold'],
      date_from: '2026-01-01', date_to: '2026-08-31',
    });

    expect(snapshot).toHaveBeenCalledTimes(3);
    expect(snapshot.mock.calls.map(([request]) => request.connection_ids.length)).toEqual([50, 50, 1]);
    expect(result).toHaveLength(101);
    expect(window.miaRuntime!.jobs.start).not.toHaveBeenCalled();
  });
});
