export type PageToken = number | 'ellipsis';

export function paginationTokens(totalPages: number, currentPage: number): PageToken[] {
  if (totalPages <= 5) return Array.from({ length: totalPages }, (_, index) => index + 1);
  const candidates = new Set([1, totalPages, currentPage - 1, currentPage, currentPage + 1]);
  const pages = [...candidates]
    .filter((value) => value >= 1 && value <= totalPages)
    .sort((left, right) => left - right);
  const tokens: PageToken[] = [];
  pages.forEach((value, index) => {
    if (index > 0 && value - pages[index - 1] > 1) tokens.push('ellipsis');
    tokens.push(value);
  });
  return tokens;
}

export function pageCount(totalItems: number, pageSize: number) {
  return Math.max(1, Math.ceil(Math.max(0, totalItems) / pageSize));
}

export function clampPage(page: number, totalPages: number) {
  return Math.max(1, Math.min(page, Math.max(1, totalPages)));
}

export function pageBounds(totalItems: number, page: number, pageSize: number) {
  const totalPages = pageCount(totalItems, pageSize);
  const currentPage = clampPage(page, totalPages);
  const start = (currentPage - 1) * pageSize;
  return { currentPage, totalPages, start, end: Math.min(start + pageSize, totalItems) };
}
