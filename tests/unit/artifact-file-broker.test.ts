import { createRequire } from 'node:module';
import { mkdtemp, readFile, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { atomicWrite, resolveInside, validateArtifactName, validateExportRequest, validateListRequest } = require('../../electron/artifact-file-broker.cjs');

describe('artifact filesystem boundary', () => {
  it.each(['../escape.xml', 'C:\\escape.xml', 'CON.pdf', 'name.exe', 'a/b.html'])('rejects unsafe name %s', (name) => {
    expect(() => validateArtifactName(name)).toThrow();
  });

  it('requires an absolute selected directory', () => {
    expect(() => resolveInside('relative', 'safe.xml')).toThrow('invalid_artifact_directory');
  });

  it('sanitizes the export DTO and rejects traversal-like account ids', () => {
    const destination = path.resolve(tmpdir(), 'MIA');
    expect(validateExportRequest({ destination, connection_ids: ['conn_1'], kinds: ['xml', 'excel'] })).toEqual({ destination, connection_ids: ['conn_1'], kinds: ['xml', 'excel'] });
    expect(() => validateExportRequest({ destination, connection_ids: ['../account'], kinds: ['xml'] })).toThrow();
    expect(() => validateExportRequest({ destination, connection_ids: ['conn_1'], kinds: ['exe'] })).toThrow();
  });

  it('validates filtered result workbook export and download-only exclusions', () => {
    const destination = path.resolve(tmpdir(), 'MIA-results');
    expect(validateExportRequest({
      destination,
      connection_ids: ['conn_1'],
      kinds: ['excel'],
      result_scopes: ['overview', 'details'],
      date_from: '2026-08-01',
      date_to: '2026-08-31',
      direction: 'purchase',
      search: '000123',
      column_filters: { nmten: 'Công ty A', dgia: '1.000' },
      exclude_business_keys: ['010|AA|1|1', '010|AA|1|1'],
      filter_scope: 'details',
    })).toMatchObject({
      destination,
      connection_ids: ['conn_1'],
      kinds: ['excel'],
      result_scopes: ['overview', 'details'],
      date_from: '2026-08-01',
      date_to: '2026-08-31',
      direction: 'purchase',
      search: '000123',
      column_filters: { nmten: 'Công ty A', dgia: '1.000' },
      exclude_business_keys: ['010|AA|1|1'],
      filter_scope: 'details',
    });
    expect(() => validateExportRequest({ destination, connection_ids: ['conn_1'], kinds: ['excel'], result_scopes: [], date_from: '2026-08-01', date_to: '2026-08-31' })).toThrow();
    expect(() => validateExportRequest({ destination, connection_ids: ['conn_1'], kinds: ['excel'], result_scopes: ['overview'], date_from: '2026-09-01', date_to: '2026-08-31' })).toThrow();
    expect(() => validateExportRequest({ destination, connection_ids: ['conn_1'], kinds: ['excel'], result_scopes: ['overview'], date_from: '2026-08-01', date_to: '2026-08-31', filter_scope: 'invalid' })).toThrow();
  });

  it('does not accept result-only exclusions on normal artifact copy requests', () => {
    const destination = path.resolve(tmpdir(), 'MIA-results');
    expect(() => validateExportRequest({
      destination, connection_ids: ['conn_1'], kinds: ['xml'], exclude_business_keys: ['invoice-1'],
    })).toThrow();
  });

  it('sanitizes artifact list filters and date bounds', () => {
    const query = validateListRequest({ connection_ids: ['conn_1'], kind: 'xml', direction: 'purchase', date_from: '2026-01-01', date_to: '2026-01-31', limit: 50 });
    expect(query).toMatchObject({ connection_ids: ['conn_1'], kind: 'xml', direction: 'purchase', date_from: '2026-01-01', date_to: '2026-01-31' });
    expect(() => validateListRequest({ connection_ids: ['conn_1'], kind: 'xml', date_from: '2026-02-01', date_to: '2026-01-01' })).toThrow();
    expect(() => validateListRequest({ connection_ids: ['conn_1'], kind: 'exe' })).toThrow();
  });

  it('writes atomically and preserves duplicates with a suffix', async () => {
    const directory = await mkdtemp(path.join(tmpdir(), 'mia-artifact-'));
    const first = await atomicWrite(directory, 'hóa-đơn.xml', Buffer.from('<xml/>'));
    const second = await atomicWrite(directory, 'hóa-đơn.xml', Buffer.from('<xml>2</xml>'));
    expect(path.basename(first)).toBe('hóa-đơn.xml');
    expect(path.basename(second)).toBe('hóa-đơn (1).xml');
    expect(await readFile(second, 'utf8')).toBe('<xml>2</xml>');
    expect((await readdir(directory)).some((name) => name.endsWith('.tmp'))).toBe(false);
  });
});
