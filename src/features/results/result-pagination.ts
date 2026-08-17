import type { ResultPage } from '../../lib/api/contracts';

export class ResultPaginationError extends Error {
  constructor(public readonly code: 'repeated_cursor' | 'invalid_page') {
    super(code === 'repeated_cursor' ? 'Backend returned a repeated cursor.' : 'Backend returned an invalid result page.');
    this.name = 'ResultPaginationError';
  }
}

export function resultIdentity(item: Record<string, unknown>) {
  if (item.id !== undefined) return `id:${String(item.id)}`;
  return ['direction', 'query_type', 'nbmst', 'khmshdon', 'khhdon', 'shdon', 'nlap', 'stt']
    .map((key) => `${key}:${String(item[key] ?? '')}`).join('|');
}

export async function collectAllResults(
  fetchPage: (cursor?: string) => Promise<ResultPage<Record<string, unknown>>>,
) {
  const cursors = new Set<string>();
  const identities = new Set<string>();
  const items: Record<string, unknown>[] = [];
  let cursor: string | undefined;
  let totalCount: number | undefined;
  let invoiceCount: number | undefined;
  for (;;) {
    const page = await fetchPage(cursor);
    if (!page || !Array.isArray(page.items) || !page.pagination) throw new ResultPaginationError('invalid_page');
    totalCount ??= page.total_count;
    invoiceCount ??= page.invoice_count;
    for (const item of page.items) {
      const identity = resultIdentity(item);
      if (!identities.has(identity)) { identities.add(identity); items.push(item); }
    }
    if (!page.pagination.has_more) break;
    const next = page.pagination.next_cursor;
    if (!next || cursors.has(next)) throw new ResultPaginationError('repeated_cursor');
    cursors.add(next);
    cursor = next;
  }
  return { items, totalCount, invoiceCount };
}

export function matchesResult(item: Record<string, unknown>, query: string, direction: string) {
  if (direction && String(item.direction ?? '') !== direction) return false;
  const normalized = query.trim().toLocaleLowerCase('vi');
  if (!normalized) return true;
  return Object.values(item).some((value) => (
    typeof value === 'string' || typeof value === 'number'
  ) && String(value).toLocaleLowerCase('vi').includes(normalized));
}
