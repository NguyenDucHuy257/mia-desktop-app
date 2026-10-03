import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { afterEach, describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const fs = require('node:fs');
const { atomicWrite, createProtectedLicenseStore } = require('../../electron/license/protected-license-store.cjs');

const temporaryDirectories: string[] = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

describe('local license state writes on Windows', () => {
  it('retries a rename rejected with EPERM by antivirus/indexing and names the file on final failure', () => {
    const directory = mkdtempSync(join(tmpdir(), 'mia-atomic-write-'));
    temporaryDirectories.push(directory);
    const target = join(directory, 'license-state.bin');
    const original = fs.renameSync;
    let failures = 2;
    const spy = vi.spyOn(fs, 'renameSync').mockImplementation((...args: unknown[]) => {
      if (failures > 0) { failures -= 1; throw Object.assign(new Error('EPERM: operation not permitted'), { code: 'EPERM' }); }
      return original(args[0] as string, args[1] as string);
    });
    try {
      atomicWrite(target, Buffer.from('ok'));
      expect(readFileSync(target, 'utf8')).toBe('ok');
      expect(spy).toHaveBeenCalledTimes(3);

      spy.mockImplementation(() => { throw Object.assign(new Error('EPERM: operation not permitted'), { code: 'EPERM' }); });
      expect(() => atomicWrite(target, Buffer.from('again'))).toThrowError(expect.objectContaining({ code: 'EPERM', licenseFile: 'license-state.bin' }));
      expect(readFileSync(target, 'utf8')).toBe('ok');
      expect(fs.readdirSync(directory).filter((name: string) => name.endsWith('.tmp'))).toEqual([]);
    } finally {
      spy.mockRestore();
    }
  });
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
