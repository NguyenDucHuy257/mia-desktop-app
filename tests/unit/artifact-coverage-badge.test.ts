import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CoverageBadge } from '../../src/features/artifacts/ArtifactCoverageBadge';
import type { VatReturnDirectionCoverage } from '../../src/lib/runtime-bridge';

function coverage(missing: VatReturnDirectionCoverage['missing']): VatReturnDirectionCoverage {
  return {
    direction: 'purchase', overview_ready: !missing.some((item) => item.scope === 'overview'),
    detail_ready: !missing.some((item) => item.scope === 'details'), ready: missing.length === 0,
    missing_overview_ranges: missing.filter((item) => item.scope === 'overview'),
    missing_detail_ranges: missing.filter((item) => item.scope === 'details'), missing,
  };
}

function render(snapshot: VatReturnDirectionCoverage) {
  return renderToStaticMarkup(createElement(CoverageBadge, {
    snapshot, state: 'ready', dateFrom: '2023-10-01', dateTo: '2023-10-31',
  }));
}

describe('shared artifact coverage badge', () => {
  it('renders the original ready icon and selected range', () => {
    const html = render(coverage([]));
    expect(html).toContain('artifact-coverage-heading');
    expect(html).toContain('✓');
    expect(html).toContain('Đã đồng bộ');
    expect(html).toContain('01/10/2023 - 31/10/2023');
  });

  it.each([
    ['overview', 'Thiếu Tổng quan: 01/10/2023 - 31/10/2023'],
    ['details', 'Thiếu Chi tiết: 01/10/2023 - 31/10/2023'],
  ] as const)('renders a missing %s scope with the warning icon', (scope, expected) => {
    const html = render(coverage([{ scope, date_from: '2023-10-01', date_to: '2023-10-31' }]));
    expect(html).toContain('>!<');
    expect(html).toContain(expected);
  });

  it('groups both scopes and retains every missing range in the tooltip', () => {
    const html = render(coverage([
      { scope: 'overview', date_from: '2023-10-01', date_to: '2023-10-31' },
      { scope: 'details', date_from: '2023-10-01', date_to: '2023-10-31' },
      { scope: 'details', date_from: '2023-11-10', date_to: '2023-11-20' },
    ]));
    expect(html).toContain('Thiếu Tổng quan + Chi tiết: 01/10/2023 - 31/10/2023');
    expect(html).toContain('(+1 khoảng)');
    expect(html).toContain('Thiếu Chi tiết: 10/11/2023 - 20/11/2023');
  });

  it('uses the same checking and error markup as XML/HTML/PDF', () => {
    const checking = renderToStaticMarkup(createElement(CoverageBadge, { state: 'loading', dateFrom: '2023-10-01', dateTo: '2023-10-31' }));
    const error = renderToStaticMarkup(createElement(CoverageBadge, { state: 'error', dateFrom: '2023-10-01', dateTo: '2023-10-31' }));
    expect(checking).toContain('data-status="checking"');
    expect(checking).toContain('Đang kiểm tra');
    expect(error).toContain('data-status="error"');
    expect(error).toContain('Không thể kiểm tra');
  });
});
