import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const require = (await import('node:module')).createRequire(import.meta.url);
const { readPreferences, readSanitizedLogs, validatePreferences, writePreferences } = require('../../electron/local-preferences.cjs');
const directories: string[] = [];

afterEach(async () => Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true }))));

describe('local preferences and logs', () => {
  it('pins legacy concurrency preferences to one and persists retries atomically', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'mia-preferences-'));
    directories.push(directory);
    await expect(readPreferences(directory)).resolves.toEqual({
      concurrency: 1,
      retries: 5,
      exportFolder: 'C:\\MIACrawl\\Export\\PDF\\T10_2023',
      pdfConcurrency: 5,
    });
    await expect(writePreferences(directory, { concurrency: 4, retries: 1 })).resolves.toEqual({
      concurrency: 1,
      retries: 1,
      exportFolder: 'C:\\MIACrawl\\Export\\PDF\\T10_2023',
      pdfConcurrency: 5,
    });
    await expect(readPreferences(directory)).resolves.toEqual({
      concurrency: 1,
      retries: 1,
      exportFolder: 'C:\\MIACrawl\\Export\\PDF\\T10_2023',
      pdfConcurrency: 5,
    });
    expect(JSON.parse(await readFile(path.join(directory, 'preferences.json'), 'utf8'))).toEqual({
      concurrency: 1,
      retries: 1,
      exportFolder: 'C:\\MIACrawl\\Export\\PDF\\T10_2023',
      pdfConcurrency: 5,
    });
    expect(() => validatePreferences({ concurrency: 0, retries: 9 })).toThrow('invalid_concurrency');
    expect(() => validatePreferences({ concurrency: 1, retries: 5, pdfConcurrency: 0 })).toThrow('invalid_pdf_concurrency');
    expect(validatePreferences({ concurrency: 1, retries: 5, pdfConcurrency: 100 })).toMatchObject({ pdfConcurrency: 100 });
  });

  it('restores the last selected export folder across preference readers', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'mia-preferences-'));
    directories.push(directory);
    const exportFolder = 'C:\\Users\\PC\\Documents\\kq';
    await writePreferences(directory, { concurrency: 1, retries: 5, exportFolder });
    await expect(readPreferences(directory)).resolves.toMatchObject({ exportFolder });
  });

  it('redacts identifiers and credential-like values and preserves the log source', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'mia-logs-'));
    directories.push(directory);
    const logDirectory = path.join(directory, 'offline-runtime', 'logs');
    await mkdir(logDirectory, { recursive: true });
    await writeFile(path.join(logDirectory, 'runtime.log'), 'account 0101234567 token=secret-value\nnormal event\n');
    const lines = await readSanitizedLogs(directory);
    expect(lines).toEqual([
      '[runtime] account [redacted-id] token=[redacted]',
      '[runtime] normal event',
    ]);
    expect(lines.every((line: string) => line.startsWith('[runtime] '))).toBe(true);
    expect(lines.join(' ')).not.toContain('0101234567');
    expect(lines.join(' ')).not.toContain('secret-value');
  });
});
