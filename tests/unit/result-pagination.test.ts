import { describe, expect, it } from 'vitest';
import { collectAllResults, matchesResult } from '../../src/features/results/result-pagination';

function page(items: Record<string, unknown>[], hasMore: boolean, nextCursor: string | null) {
  return { items, total_count: 421, row_count: items.length, invoice_count: 421, pagination: { limit: 200, has_more: hasMore, next_cursor: nextCursor } };
}

describe('result cursor pagination', () => {
  it('collects exactly 421 records without duplicates or missing rows', async () => {
    const all = Array.from({ length: 421 }, (_, index) => ({ id: index + 1, shdon: String(index + 1) }));
    const pages = [page(all.slice(0, 200), true, 'cursor-200'), page(all.slice(200, 400), true, 'cursor-400'), page(all.slice(400), false, null)];
    let index = 0;
    const result = await collectAllResults(async () => pages[index++]!);
    expect(result.items).toHaveLength(421);
    expect(new Set(result.items.map((item) => item.id)).size).toBe(421);
  });

  it('deduplicates overlapping records but stops on a repeated cursor', async () => {
    const pages = [page([{ id: 1 }, { id: 2 }], true, 'same'), page([{ id: 2 }, { id: 3 }], true, 'same')];
    let index = 0;
    await expect(collectAllResults(async () => pages[index++]!)).rejects.toEqual(expect.objectContaining({ code: 'repeated_cursor' }));
  });

  it('rejects empty continuation cursors and supports search/filter', async () => {
    await expect(collectAllResults(async () => page([{ id: 1 }], true, null))).rejects.toEqual(expect.objectContaining({ code: 'repeated_cursor' }));
    expect(matchesResult({ direction: 'purchase', shdon: 'HD-421', nbten: 'Công ty A' }, '421', 'purchase')).toBe(true);
    expect(matchesResult({ direction: 'sold', shdon: 'HD-421' }, '421', 'purchase')).toBe(false);
  });
});
