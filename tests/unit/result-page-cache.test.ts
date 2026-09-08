import { expect, it, vi } from 'vitest';
import { cachedResultPage, setResultRevision } from '../../src/features/results/result-page-cache';

it('reuses pages across visits and invalidates on sync revision', async () => {
  const read = vi.fn().mockResolvedValue({ items: [1] });
  await Promise.all([cachedResultPage('a', 'query', read), cachedResultPage('a', 'query', read)]);
  await cachedResultPage('a', 'query', read);
  expect(read).toHaveBeenCalledTimes(1);
  setResultRevision('a', 'new-sync');
  await cachedResultPage('a', 'query', read);
  expect(read).toHaveBeenCalledTimes(2);
  await cachedResultPage('b', 'query', read);
  expect(read).toHaveBeenCalledTimes(3);
});

it('does not cache failed reads', async () => {
  const read = vi.fn().mockRejectedValueOnce(new Error('timeout')).mockResolvedValue(1);
  await expect(cachedResultPage('c', 'query', read)).rejects.toThrow('timeout');
  await expect(cachedResultPage('c', 'query', read)).resolves.toBe(1);
});
