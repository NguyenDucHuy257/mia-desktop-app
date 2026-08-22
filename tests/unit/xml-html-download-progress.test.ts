import { describe, expect, it } from 'vitest';
import { phaseWeightedPercent } from '../../src/features/artifacts/use-xml-html-download-lifecycle';
import { artifactCounterText, resolveArtifactRowStatus, XML_HTML_GRID } from '../../src/features/artifacts/XmlHtmlPage';

describe('XML/HTML batch progress', () => {
  it('uses real source and copy phase fractions without resetting', () => {
    const values = [
      phaseWeightedPercent(0, 0, 2),
      phaseWeightedPercent(50, 0, 2),
      phaseWeightedPercent(100, 0, 2),
      phaseWeightedPercent(100, 25, 2),
      phaseWeightedPercent(100, 75, 2),
      phaseWeightedPercent(100, 100, 2),
    ];
    expect(values[0]).toBe(0);
    expect(values.at(-1)).toBe(100);
    expect(values.every((value) => value >= 0 && value <= 100)).toBe(true);
    expect(values).toEqual([...values].sort((left, right) => left - right));
  });

  it('weights one selected artifact without inventing timer progress', () => {
    expect(phaseWeightedPercent(100, 0, 1)).toBe(50);
    expect(phaseWeightedPercent(100, 50, 1)).toBe(75);
    expect(phaseWeightedPercent(100, 100, 1)).toBe(100);
  });

  it('reports real per-kind success counters', () => {
    const completed = { xml: new Set(['a', 'b']), html: new Set(['a']) };
    expect(artifactCounterText(['xml'], 3, completed)).toBe('XML 2/3');
    expect(artifactCounterText(['html'], 3, completed)).toBe('HTML 1/3');
    expect(artifactCounterText(['xml', 'html'], 3, completed)).toBe('XML 2/3 · HTML 1/3');
  });

  it('keeps stable identity status across pages and requires every selected kind', () => {
    const completed = { xml: new Set(['invoice-a']), html: new Set<string>() };
    const common = {
      key: 'invoice-a', selectedKinds: ['xml', 'html'] as Array<'xml' | 'html'>,
      persistedComplete: false, completedKeys: completed,
      batchRelevant: true, terminal: false, downloadActive: false,
      activeKeys: new Set<string>(),
    };
    expect(resolveArtifactRowStatus(common)).toBe('Chưa xử lý');
    expect(resolveArtifactRowStatus({ ...common, downloadActive: true, activeKeys: new Set(['invoice-a']) })).toBe('Đang tải');
    completed.html.add('invoice-a');
    expect(resolveArtifactRowStatus(common)).toBe('Hoàn tất');
    expect(resolveArtifactRowStatus({ ...common, key: 'invoice-b', terminal: true })).toBe('Lỗi');
    expect(resolveArtifactRowStatus({ ...common, key: 'invoice-a' })).toBe('Hoàn tất');
  });

  it('uses one fixed nine-column grid for headers and rows', () => {
    expect(XML_HTML_GRID).toBe('55px 130px 130px 130px 160px minmax(300px, 1fr) 150px 140px 150px');
  });
});
