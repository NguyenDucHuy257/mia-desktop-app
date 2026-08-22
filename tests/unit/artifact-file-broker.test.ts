import { createRequire } from 'node:module';
import { mkdtemp, readFile, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { atomicWrite, createArtifactBroker, resolveInside, validateArtifactName, validateExportRequest, validateListRequest } = require('../../electron/artifact-file-broker.cjs');

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

  it('validates filtered result workbook export separately from normal artifacts', () => {
    const destination = path.resolve(tmpdir(), 'MIA-results');
    expect(validateExportRequest({
      destination,
      connection_ids: ['conn_1'],
      kinds: ['excel'],
      result_scopes: ['overview', 'details'],
      date_from: '2026-08-01',
      date_to: '2026-08-31',
      direction: 'purchase',
      query_type: 'query',
      search: '000123',
    })).toMatchObject({
      destination,
      connection_ids: ['conn_1'],
      kinds: ['excel'],
      result_scopes: ['overview', 'details'],
      date_from: '2026-08-01',
      date_to: '2026-08-31',
      direction: 'purchase',
      query_type: 'query',
      search: '000123',
    });
    expect(() => validateExportRequest({ destination, connection_ids: ['conn_1'], kinds: ['excel'], result_scopes: ['overview'], date_from: '2026-08-01', date_to: '2026-08-31', query_type: 'bad' })).toThrow();
    expect(() => validateExportRequest({ destination, connection_ids: ['conn_1'], kinds: ['excel'], result_scopes: [], date_from: '2026-08-01', date_to: '2026-08-31' })).toThrow();
    expect(() => validateExportRequest({ destination, connection_ids: ['conn_1'], kinds: ['excel'], result_scopes: ['overview'], date_from: '2026-09-01', date_to: '2026-08-31' })).toThrow();
  });

  it('preserves allowlisted XML/HTML invoice filters', () => {
    const destination = path.resolve(tmpdir(), 'MIA-packages');
    expect(validateExportRequest({
      destination, connection_ids: ['conn_1'], kinds: ['xml', 'html'],
      date_from: '2026-08-01', date_to: '2026-08-31', direction: 'sold',
      query_type: 'sco-query', search: '000123',
    })).toMatchObject({
      destination, connection_ids: ['conn_1'], kinds: ['xml', 'html'],
      direction: 'sold', query_type: 'sco-query', search: '000123',
    });
    expect(() => validateExportRequest({ destination, connection_ids: ['conn_1'], kinds: ['xml'], query_type: 'bad' })).toThrow('invalid_artifact_query_type');
  });

  it('returns the broker envelope expected by preload for successful exports', async () => {
    const destination = path.resolve(tmpdir(), 'MIA-results');
    const runtime = {
      invoke: vi.fn().mockResolvedValue({ count: 1, files: [path.join(destination, 'result.xlsx')] }),
    };
    const broker = createArtifactBroker(() => runtime);

    await expect(broker.export({
      destination,
      connection_ids: ['conn_1'],
      kinds: ['excel'],
      result_scopes: ['overview'],
      date_from: '2026-08-01',
      date_to: '2026-08-31',
    })).resolves.toMatchObject({
      ok: true,
      data: { count: 1 },
    });
  });

  it('preserves a safe no-data reason for the renderer', async () => {
    const destination = path.resolve(tmpdir(), 'MIA-results');
    const runtime = {
      invoke: vi.fn().mockResolvedValue({ count: 0, files: [], error_code: 'result_export_empty' }),
    };
    const broker = createArtifactBroker(() => runtime);

    await expect(broker.export({
      destination,
      connection_ids: ['conn_1'],
      kinds: ['excel'],
      result_scopes: ['overview'],
      date_from: '2026-08-01',
      date_to: '2026-08-31',
    })).resolves.toEqual({
      ok: false,
      error: {
        code: 'result_export_empty',
        message: 'Không có dữ liệu phù hợp để tạo Excel.',
      },
    });
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
