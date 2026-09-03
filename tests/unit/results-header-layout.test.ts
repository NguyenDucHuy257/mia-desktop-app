import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('results header layout contract', () => {
  it('uses the bundled back asset and compact normal-flow spacing', () => {
    const component = readFileSync('src/features/results/ResultsPage.tsx', 'utf8');
    const tokens = readFileSync('src/styles/tokens.css', 'utf8');
    const styles = readFileSync('src/styles/results-enhancements.css', 'utf8');
    expect(component).toContain("import backIcon from '../../assets/figma/back.png'");
    expect(component).toContain('<img src={backIcon} alt="" />');
    expect(component).not.toContain('http://127.0.0.1:5173/src/assets/figma/back.png');
    expect(tokens).toContain('--mia-sidebar-width: 232px;');
    expect(styles).toContain('padding: var(--results-page-inset-top) 24px 8px;');
    expect(styles).toContain('--results-back-title-gap: 8px;');
    expect(styles).toContain('display: grid;');
    expect(styles).toContain('gap: 3px;');
    const headerLayoutRules = styles.slice(0, styles.indexOf('.results-export {'));
    expect(headerLayoutRules).not.toContain('padding: calc(');
    expect(headerLayoutRules).not.toContain('margin-top:');
    expect(headerLayoutRules).not.toContain('transform:');
    expect(headerLayoutRules).not.toContain('position: absolute');
    expect(headerLayoutRules).not.toMatch(/margin:\s*-\d/);
    expect(styles).toMatch(/\.results-page--figma \.results-export-popover > button:hover\s*\{[^}]*color:\s*#fff;/s);
    expect(styles).toMatch(/\.results-page--figma \.results-export-popover > button:active\s*\{[^}]*color:\s*#fff;/s);
  });
});
