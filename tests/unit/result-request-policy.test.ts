import { describe, expect, it, vi } from 'vitest';
import { coalesceResultRequest, isTransientResultError, requestResultWithRetry } from '../../src/features/results/result-request-policy';

describe('result request retry policy', () => {
  it('keeps loading through one transient timeout and returns the second result', async () => {
    const timeout = Object.assign(new Error('timed out'), { code: 'runtime_timeout' });
    const request = vi.fn()
      .mockRejectedValueOnce(timeout)
      .mockResolvedValueOnce({ items: [1] });
    const attempts: string[] = [];
    await expect(requestResultWithRetry(
      request,
      event => attempts.push(event.outcome),
      { delayMs: 0 },
    )).resolves.toEqual({ items: [1] });
    expect(request).toHaveBeenCalledTimes(2);
    expect(attempts).toEqual(['retry', 'ok']);
  });

  it('does not retry validation or authorization failures', async () => {
    const invalid = Object.assign(new Error('invalid'), { code: 'invalid_result_query', status: 400 });
    const request = vi.fn().mockRejectedValue(invalid);
    await expect(requestResultWithRetry(request, () => undefined, { delayMs: 0 })).rejects.toBe(invalid);
    expect(request).toHaveBeenCalledTimes(1);
    expect(isTransientResultError(Object.assign(new Error(), { status: 401 }))).toBe(false);
  });

  it('coalesces StrictMode/effect duplicates into one backend request', async () => {
    const inFlight = new Map<string, Promise<{ items: number[] }>>();
    let resolve!: (value: { items: number[] }) => void;
    const backend = vi.fn(() => new Promise<{ items: number[] }>(done => { resolve = done; }));
    const first = coalesceResultRequest(inFlight, 'overview|range', backend);
    const second = coalesceResultRequest(inFlight, 'overview|range', backend);
    expect(first).toBe(second);
    expect(backend).toHaveBeenCalledTimes(1);
    resolve({ items: [1] });
    await expect(first).resolves.toEqual({ items: [1] });
    expect(inFlight.size).toBe(0);
  });
});
