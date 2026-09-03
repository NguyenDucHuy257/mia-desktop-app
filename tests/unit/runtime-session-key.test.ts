import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

const require = (await import('node:module')).createRequire(import.meta.url);
const {
  RUNTIME_KEY_FILE,
  isValidRuntimeSessionKey,
  loadOrCreateRuntimeSessionKey,
} = require('../../electron/runtime-session-key.cjs');

const directories: string[] = [];
const firstKey = Buffer.alloc(32, 1).toString('base64url');
const replacementKey = Buffer.alloc(32, 2).toString('base64url');

async function temporaryDirectory() {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'mia-runtime-key-'));
  directories.push(directory);
  return directory;
}

afterEach(async () => {
  await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe('runtime session key storage', () => {
  it('reuses a valid protected key', async () => {
    const directory = await temporaryDirectory();
    const protector = {
      encrypt: vi.fn((value: string) => Buffer.from(`protected:${value}`)),
      decrypt: vi.fn((value: Buffer) => value.toString().replace('protected:', '')),
    };

    expect(loadOrCreateRuntimeSessionKey({ directory, protector, generateKey: () => firstKey })).toBe(firstKey);
    expect(loadOrCreateRuntimeSessionKey({ directory, protector, generateKey: () => replacementKey })).toBe(firstKey);
    expect(protector.encrypt).toHaveBeenCalledTimes(1);
  });

  it('atomically replaces only an undecryptable key and preserves neighboring data', async () => {
    const directory = await temporaryDirectory();
    const filename = path.join(directory, RUNTIME_KEY_FILE);
    const database = path.join(directory, 'source-control.sqlite3');
    await writeFile(filename, Buffer.from('broken-dpapi-ciphertext'));
    await writeFile(database, Buffer.from('sqlite-data-must-remain'));
    const logger = { warn: vi.fn() };
    const protector = {
      encrypt: vi.fn((value: string) => Buffer.from(`reprotected:${value}`)),
      decrypt: vi.fn(() => { throw new Error('DPAPI decrypt failed'); }),
    };

    expect(loadOrCreateRuntimeSessionKey({ directory, protector, logger, generateKey: () => replacementKey })).toBe(replacementKey);
    expect((await readFile(filename)).toString()).toBe(`reprotected:${replacementKey}`);
    expect((await readFile(database)).toString()).toBe('sqlite-data-must-remain');
    expect(logger.warn).toHaveBeenCalledWith('runtime_session_key_recovered', { error_type: 'Error' });
    expect((await import('node:fs/promises')).readdir(directory).then((files) => files.filter((file) => file.endsWith('.tmp')))).resolves.toEqual([]);
  });

  it('rejects malformed plaintext even when decryption succeeds', async () => {
    const directory = await temporaryDirectory();
    await writeFile(path.join(directory, RUNTIME_KEY_FILE), Buffer.from('protected-invalid'));
    const protector = {
      encrypt: (value: string) => Buffer.from(`protected:${value}`),
      decrypt: () => 'not-a-32-byte-key',
    };

    expect(loadOrCreateRuntimeSessionKey({ directory, protector, generateKey: () => replacementKey })).toBe(replacementKey);
    expect(isValidRuntimeSessionKey(replacementKey)).toBe(true);
  });
});
