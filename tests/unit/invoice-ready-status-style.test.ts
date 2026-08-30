import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('invoice ready status presentation', () => {
  it('uses the existing success-green badge without changing other variants', () => {
    const css = readFileSync('src/styles/global.css', 'utf8');
    expect(css).toContain('.status-badge[data-status="ready"]');
    expect(css).toContain('background: #dcfce7');
    expect(css).toContain('.status-badge[data-status="failed"]');
    expect(css).toContain('.status-badge[data-status="processing"]');
  });
});
