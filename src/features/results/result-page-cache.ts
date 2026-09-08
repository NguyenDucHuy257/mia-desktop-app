// Session-only, bounded cache: never persist invoice contents to browser storage.
const pages = new Map<string, { value: unknown; saved: number }>();
const pending = new Map<string, Promise<unknown>>();
const revisions = new Map<string, string>();
const MAX_PAGES = 80;
const MAX_AGE = 5 * 60_000;

export function setResultRevision(account: string, revision: string) {
  revisions.set(account, revision);
}

export async function cachedResultPage<T>(account: string, key: string, request: () => Promise<T>): Promise<T> {
  const revision = revisions.get(account) ?? '';
  const cacheKey = JSON.stringify([account, revision, key]);
  const cached = pages.get(cacheKey);
  if (cached && Date.now() - cached.saved < MAX_AGE) {
    pages.delete(cacheKey);
    pages.set(cacheKey, cached);
    return cached.value as T;
  }
  const existing = pending.get(cacheKey);
  if (existing) return existing as Promise<T>;
  const promise = request().then(value => {
    if ((revisions.get(account) ?? '') === revision) {
      pages.set(cacheKey, { value, saved: Date.now() });
      while (pages.size > MAX_PAGES) pages.delete(pages.keys().next().value!);
    }
    return value;
  }).finally(() => { pending.delete(cacheKey); });
  pending.set(cacheKey, promise);
  return promise;
}
