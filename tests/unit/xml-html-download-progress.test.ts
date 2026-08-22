import { describe, expect, it } from 'vitest';
import { actualArtifactPercent, completedArtifactKeys, settleRunningArtifactStates } from '../../src/features/artifacts/use-xml-html-download-lifecycle';
import { artifactCounterText, resolveArtifactRowStatus, XML_HTML_GRID } from '../../src/features/artifacts/XmlHtmlPage';

describe('XML/HTML batch progress', () => {
  it('uses actual terminal source and copied work without resetting', () => {
    const values = [
      actualArtifactPercent(0, 0, 3, 2),
      actualArtifactPercent(1, 0, 3, 2),
      actualArtifactPercent(2, 0, 3, 2),
      actualArtifactPercent(3, 0, 3, 2),
      actualArtifactPercent(3, 3, 3, 2),
      actualArtifactPercent(3, 6, 3, 2),
    ];
    expect(values[0]).toBe(0);
    expect(values.at(-1)).toBe(99.9);
    expect(values.every((value) => value >= 0 && value <= 100)).toBe(true);
    expect(values).toEqual([...values].sort((left, right) => left - right));
  });

  it('does not infer completed work from an overall source percentage', () => {
    expect(actualArtifactPercent(0, 0, 3, 1)).toBe(0);
    expect(actualArtifactPercent(3, 0, 3, 1)).toBe(50);
    expect(actualArtifactPercent(3, 3, 3, 1)).toBe(99.9);
  });

  it('reports real per-kind success counters', () => {
    const completed = { xml: new Set(['a', 'b']), html: new Set(['a']) };
    expect(artifactCounterText(['xml'], 3, completed)).toBe('XML 2/3');
    expect(artifactCounterText(['html'], 3, completed)).toBe('HTML 1/3');
    expect(artifactCounterText(['xml', 'html'], 3, completed)).toBe('XML 2/3 · HTML 1/3');
    expect(artifactCounterText(['xml', 'html'], 120, completed)).toBe('XML 2/120 · HTML 1/120');
  });

  it('advances XML and HTML counters only for successful target substates', () => {
    const targets = new Set(['a', 'b', 'c']);
    const initial = completedArtifactKeys({
      a: { xml: 'pending', html: 'pending' }, b: { xml: 'pending', html: 'pending' }, c: { xml: 'pending', html: 'pending' },
    }, targets);
    expect(artifactCounterText(['xml', 'html'], 3, initial)).toBe('XML 0/3 · HTML 0/3');

    const xmlOne = completedArtifactKeys({
      a: { xml: 'completed', html: 'running' }, b: { xml: 'pending', html: 'pending' }, c: { xml: 'pending', html: 'pending' },
    }, targets);
    expect(artifactCounterText(['xml', 'html'], 3, xmlOne)).toBe('XML 1/3 · HTML 0/3');

    const htmlOneAndFailure = completedArtifactKeys({
      a: { xml: 'completed', html: 'completed' }, b: { xml: 'failed', html: 'pending' }, c: { xml: 'pending', html: 'pending' },
    }, targets);
    expect(artifactCounterText(['xml', 'html'], 3, htmlOneAndFailure)).toBe('XML 1/3 · HTML 1/3');
  });

  it('keeps stable identity status across pages and requires every selected kind', () => {
    const common = {
      selectedKinds: ['xml', 'html'] as Array<'xml' | 'html'>,
      batchRelevant: true,
      states: { xml: 'pending', html: 'pending' } as const,
    };
    expect(resolveArtifactRowStatus(common)).toBe('Chưa xử lý');
    expect(resolveArtifactRowStatus({ ...common, states: { xml: 'running', html: 'pending' } })).toBe('Đang tải');
    expect(resolveArtifactRowStatus({ ...common, states: { xml: 'completed', html: 'pending' } })).not.toBe('Hoàn tất');
    expect(resolveArtifactRowStatus({ ...common, states: { xml: 'completed', html: 'completed' } })).toBe('Hoàn tất');
    expect(resolveArtifactRowStatus({ ...common, states: { xml: 'failed', html: 'completed' } })).toBe('Lỗi');
    expect(resolveArtifactRowStatus({ ...common, batchRelevant: false })).toBe('Chưa xử lý');
  });

  it('does not leave a row running after stop or terminal failure', () => {
    const states = { a: { xml: 'running', html: 'pending' } } as const;
    expect(settleRunningArtifactStates(states, true).a).toEqual({ xml: 'pending', html: 'pending' });
    expect(settleRunningArtifactStates(states, false).a).toEqual({ xml: 'failed', html: 'pending' });
  });

  it('uses one fixed nine-column grid for headers and rows', () => {
    expect(XML_HTML_GRID).toBe('55px 130px 130px 130px 160px minmax(300px, 1fr) 150px 140px 150px');
  });
});
