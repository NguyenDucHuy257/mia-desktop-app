import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const require = (await import('node:module')).createRequire(import.meta.url);
const { clearDiagnosticLogs, readPreferences, readSanitizedLogEntries, readSanitizedLogs, validatePreferences, writePreferences } = require('../../electron/local-preferences.cjs');
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

  it('keeps only warnings and errors while redacting identifiers and credentials', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'mia-logs-'));
    directories.push(directory);
    const logDirectory = path.join(directory, 'offline-runtime', 'logs');
    await mkdir(logDirectory, { recursive: true });
    await writeFile(path.join(logDirectory, 'runtime.log'), [
      '2026-08-24 22:15:48,000 INFO runtime package_cache_hit',
      '2026-08-24 22:15:48,100 WARNING runtime package_request_failed attempt=1 retryable=true',
      '2026-08-24 22:15:48,200 WARNING app.services.portal_session Invoice package is unavailable on the tax portal; not retrying a permanent HTTP 500 response',
      '2026-08-24 22:15:49,000 WARNING runtime source_retry_exhausted format=XML mst=0101234567 token=secret-value status=500',
    ].join('\n'));
    const lines = await readSanitizedLogs(directory);
    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain('source_retry_exhausted');
    expect(lines[0]).toContain('status=500');
    expect(lines.every((line: string) => line.startsWith('[runtime] '))).toBe(true);
    expect(lines.join(' ')).not.toContain('0101234567');
    expect(lines.join(' ')).not.toContain('secret-value');
  });

  it('returns structured newest-first activity rows and clears every diagnostic file', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'mia-logs-'));
    directories.push(directory);
    const rendererDirectory = path.join(directory, 'logs');
    await mkdir(rendererDirectory, { recursive: true });
    const filename = path.join(rendererDirectory, 'renderer.log');
    await writeFile(filename, [
      '2026-08-24T01:00:00.000Z INFO account_login_succeeded {"connection_id":"conn_1"}',
      '2026-08-24T01:01:00.000Z ERROR job_start_failed {"code":"invalid_params"}',
    ].join('\n'));
    const entries = await readSanitizedLogEntries(directory);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ level: 'error', source: 'renderer', event: 'job_start_failed' });
    expect(entries[0].details).toContain('invalid_params');
    await expect(clearDiagnosticLogs(directory)).resolves.toBe(true);
    await expect(readSanitizedLogs(directory)).resolves.toEqual([]);
  });

  it('preserves compact diagnostic categories for unavailable and PDF failures', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'mia-logs-'));
    directories.push(directory);
    const logDirectory = path.join(directory, 'offline-runtime', 'logs');
    await mkdir(logDirectory, { recursive: true });
    await writeFile(path.join(logDirectory, 'runtime.log'), [
      '2026-08-24 22:16:00,000 WARNING runtime source_confirmed_unavailable format=HTML invoice_ref=42 status=404',
      '2026-08-24 22:16:03,000 ERROR runtime pdf_failed format=PDF invoice_ref=42 error_type=FileNotFoundError message=sign-check.jpg_missing',
    ].join('\n'));
    const entries = await readSanitizedLogEntries(directory);
    expect(entries).toHaveLength(2);
    expect(entries.some((entry: { event: string }) => entry.event === 'source_confirmed_unavailable')).toBe(true);
    expect(entries.some((entry: { event: string; details: string }) => entry.event === 'pdf_failed' && entry.details.includes('FileNotFoundError'))).toBe(true);
  });
});
