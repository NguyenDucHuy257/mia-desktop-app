import { createRequire } from 'node:module';
import { mkdtemp, readFile, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { atomicWrite, resolveInside, validateArtifactName, validateExportRequest } = require('../../electron/artifact-file-broker.cjs');

describe('artifact filesystem boundary', () => {
  it.each(['../escape.xml', 'C:\\escape.xml', 'CON.pdf', 'name.exe', 'a/b.html'])('rejects unsafe name %s', (name) => {
    expect(() => validateArtifactName(name)).toThrow();
  });

  it('requires an absolute selected directory', () => {
    expect(() => resolveInside('relative', 'safe.xml')).toThrow('invalid_artifact_directory');
  });

  it('sanitizes the export DTO and rejects traversal-like account ids', () => {
    expect(validateExportRequest({ destination: 'D:\\MIA', connection_ids: ['conn_1'], kinds: ['xml', 'excel'] })).toEqual({ destination: 'D:\\MIA', connection_ids: ['conn_1'], kinds: ['xml', 'excel'] });
    expect(() => validateExportRequest({ destination: 'D:\\MIA', connection_ids: ['../account'], kinds: ['xml'] })).toThrow();
    expect(() => validateExportRequest({ destination: 'D:\\MIA', connection_ids: ['conn_1'], kinds: ['exe'] })).toThrow();
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
