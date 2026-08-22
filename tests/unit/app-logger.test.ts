import { createRequire } from 'node:module';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { createDiagnosticLogger } = require('../../electron/app-logger.cjs');

describe('desktop diagnostic logger', () => {
  it('redacts credentials and tax identifiers before writing', () => {
    const directory = mkdtempSync(join(tmpdir(), 'mia-log-'));
    try {
      const logger = createDiagnosticLogger(directory, 'electron.log');
      logger.error('test_event', {
        password: 'super-secret',
        tax_code: '0111380276',
        stack: 'request failed token=abc123 authorization=BearerValue',
      });
      const content = readFileSync(join(directory, 'electron.log'), 'utf8');
      expect(content).toContain('test_event');
      expect(content).toContain('[redacted]');
      expect(content).toContain('[redacted-id]');
      expect(content).not.toContain('super-secret');
      expect(content).not.toContain('0111380276');
      expect(content).not.toContain('abc123');
      expect(content).not.toContain('BearerValue');
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
});
