import type { ArtifactAccountSnapshot, VatReturnDirectionCoverage, VatReturnMissingRange } from '../../lib/runtime-bridge';

export type ArtifactCoverageState = 'loading' | 'ready' | 'error';
type CoverageSnapshot = ArtifactAccountSnapshot | VatReturnDirectionCoverage;

export function formatDate(value: string) {
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}/${month}/${year}` : value;
}

function vatMissingRanges(snapshot: VatReturnDirectionCoverage): VatReturnMissingRange[] {
  if (snapshot.missing?.length) return snapshot.missing;
  return [
    ...(snapshot.missing_overview_ranges ?? []).map((range) => ({ scope: 'overview' as const, ...range })),
    ...(snapshot.missing_detail_ranges ?? []).map((range) => ({ scope: 'details' as const, ...range })),
  ];
}

function isVatCoverage(snapshot: CoverageSnapshot | undefined): snapshot is VatReturnDirectionCoverage {
  return Boolean(snapshot && ('direction' in snapshot || 'overview_ready' in snapshot || 'missing_overview_ranges' in snapshot));
}

function groupedVatMissing(snapshot: VatReturnDirectionCoverage) {
  const groups = new Map<string, { date_from: string; date_to: string; scopes: Set<string> }>();
  for (const range of vatMissingRanges(snapshot)) {
    const key = `${range.date_from}|${range.date_to}`;
    const group = groups.get(key) ?? { date_from: range.date_from, date_to: range.date_to, scopes: new Set<string>() };
    group.scopes.add(range.scope === 'overview' ? 'Tổng quan' : 'Chi tiết');
    groups.set(key, group);
  }
  return [...groups.values()].map((group) => ({
    ...group,
    scopeLabel: group.scopes.has('Tổng quan') && group.scopes.has('Chi tiết') ? 'Tổng quan + Chi tiết'
      : group.scopes.has('Tổng quan') ? 'Tổng quan' : 'Chi tiết',
  }));
}

export function missingCoverageText(snapshot: CoverageSnapshot | undefined, dateFrom: string, dateTo: string) {
  if (isVatCoverage(snapshot)) {
    const groups = groupedVatMissing(snapshot);
    if (!groups.length) return `${formatDate(dateFrom)} - ${formatDate(dateTo)}`;
    const first = groups[0];
    const text = `Thiếu ${first.scopeLabel}: ${formatDate(first.date_from)} - ${formatDate(first.date_to)}`;
    return groups.length === 1 ? text : `${text} (+${groups.length - 1} khoảng)`;
  }
  const ranges = snapshot?.missing_ranges ?? [];
  if (!ranges.length) return `${formatDate(dateFrom)} - ${formatDate(dateTo)}`;
  const first = `${formatDate(ranges[0].date_from)} - ${formatDate(ranges[0].date_to)}`;
  return ranges.length === 1 ? first : `${first} (+${ranges.length - 1} khoảng)`;
}

export function coverageTitle(snapshot: CoverageSnapshot | undefined, dateFrom: string, dateTo: string) {
  if (isVatCoverage(snapshot)) {
    const groups = groupedVatMissing(snapshot);
    if (!groups.length) return `${formatDate(dateFrom)} - ${formatDate(dateTo)}`;
    return groups.map((group) => `Thiếu ${group.scopeLabel}: ${formatDate(group.date_from)} - ${formatDate(group.date_to)}`).join('; ');
  }
  const ranges = snapshot?.missing_ranges ?? [];
  if (!ranges.length) return `${formatDate(dateFrom)} - ${formatDate(dateTo)}`;
  return ranges.map((range) => `${formatDate(range.date_from)} - ${formatDate(range.date_to)}`).join(', ');
}

export function CoverageBadge({ snapshot, state, dateFrom, dateTo }: {
  snapshot?: CoverageSnapshot;
  state: ArtifactCoverageState;
  dateFrom: string;
  dateTo: string;
}) {
  const status = state === 'loading' ? 'checking' : state === 'error' ? 'error' : snapshot?.ready ? 'ready' : 'not_ready';
  const label = status === 'checking' ? 'Đang kiểm tra' : status === 'error' ? 'Không thể kiểm tra' : status === 'ready' ? 'Đã đồng bộ' : 'Chưa đồng bộ';
  const detail = status === 'ready'
    ? `${formatDate(dateFrom)} - ${formatDate(dateTo)}`
    : status === 'not_ready' ? missingCoverageText(snapshot, dateFrom, dateTo) : '';
  return <span className="artifact-coverage-badge" data-status={status} title={coverageTitle(snapshot, dateFrom, dateTo)}>
    <span className="artifact-coverage-heading"><i aria-hidden="true">{status === 'ready' ? '✓' : status === 'not_ready' ? '!' : '…'}</i><strong>{label}</strong></span>
    {detail ? <small>{detail}</small> : null}
  </span>;
}
