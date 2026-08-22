import { describe, expect, it } from 'vitest';
import { pageBounds, pageCount, paginationTokens } from '../../src/components/pagination-utils';

describe('account pagination helpers', () => {
  it.each([
    [0, 1],
    [1, 1],
    [20, 1],
    [21, 2],
    [40, 2],
    [41, 3],
  ])('uses twenty rows per page for %i accounts', (accounts, expectedPages) => {
    expect(pageCount(accounts, 20)).toBe(expectedPages);
  });

  it('returns the correct second-page window and clamps after deletion/filtering', () => {
    expect(pageBounds(27, 2, 20)).toEqual({ currentPage: 2, totalPages: 2, start: 20, end: 27 });
    expect(pageBounds(12, 4, 20)).toEqual({ currentPage: 1, totalPages: 1, start: 0, end: 12 });
  });

  it('does not duplicate the final page in compact tokens', () => {
    expect(paginationTokens(3, 1)).toEqual([1, 2, 3]);
    expect(paginationTokens(8, 4)).toEqual([1, 'ellipsis', 3, 4, 5, 'ellipsis', 8]);
  });
});
