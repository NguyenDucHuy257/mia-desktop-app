import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { afterEach, describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { createProtectedLicenseStore } = require('../../electron/license/protected-license-store.cjs');

const temporaryDirectories: string[] = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

describe('local license first-use date', () => {
  it('writes a plain local text marker once and never overwrites it', () => {
    const directory = mkdtempSync(join(tmpdir(), 'mia-first-use-'));
    temporaryDirectories.push(directory);
    const store = createProtectedLicenseStore(directory, {
      encrypt: (value: string) => Buffer.from(value, 'utf8'),
      decrypt: (value: Buffer) => value.toString('utf8'),
    });

    expect(store.loadFirstUseDate()).toBeNull();
    expect(store.saveFirstUseDate('2026-09-13')).toBe('2026-09-13');
    expect(store.saveFirstUseDate('2026-10-01')).toBe('2026-09-13');
    expect(store.loadFirstUseDate()).toBe('2026-09-13');
    expect(readFileSync(join(directory, 'first-use.txt'), 'utf8')).toBe('2026-09-13\n');
  });
});
