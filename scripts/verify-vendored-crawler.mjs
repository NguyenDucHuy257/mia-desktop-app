import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve('runtime/python/vendor/mia_crawl_service');
const manifestPath = path.join(root, 'VENDOR-MANIFEST.json');
const transportPath = path.join(root, 'VENDOR-TRANSPORT.json');
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
const transport = JSON.parse(await readFile(transportPath, 'utf8'));

function canonicalBytes(buffer) {
  // Git may materialize text files as CRLF on Windows and LF on CI. The
  // integrity manifest protects source content, not the checkout convention.
  return buffer.includes(0)
    ? buffer
    : Buffer.from(buffer.toString('utf8').replaceAll('\r\n', '\n'));
}

function gitBlobSha(buffer) {
  return createHash('sha1')
    .update(Buffer.from(`blob ${buffer.length}\0`, 'utf8'))
    .update(buffer)
    .digest('hex');
}

function sourceBlobMatches(buffer, sourceBlob) {
  if (buffer.includes(0)) return gitBlobSha(buffer) === sourceBlob;
  const canonical = canonicalBytes(buffer);
  if (gitBlobSha(canonical) === sourceBlob) return true;
  // Some files in the pinned upstream tree are committed with CRLF while
  // checkout may normalize them to LF (and vice versa on Windows). Rebuild the
  // CRLF byte form before declaring a source divergence.
  const crlf = Buffer.from(canonical.toString('utf8').replaceAll('\n', '\r\n'));
  return gitBlobSha(crlf) === sourceBlob;
}

async function filesBelow(directory) {
  const result = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (
      entry.name === '__pycache__'
      || entry.name === 'VENDOR-MANIFEST.json'
      || entry.name === 'VENDOR-TRANSPORT.json'
    ) continue;
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) result.push(...await filesBelow(absolute));
    else if (entry.isFile()) result.push(absolute);
  }
  return result;
}

const actual = {};
const actualBuffers = {};
for (const file of (await filesBelow(root)).sort()) {
  const relative = path.relative(root, file).replaceAll('\\', '/');
  const raw = await readFile(file);
  const canonical = canonicalBytes(raw);
  actualBuffers[relative] = raw;
  actual[relative] = createHash('sha256').update(canonical).digest('hex');
}

const pinned = '63acf111c64b47ac964608141b2c83bbb6e2f688';
if (manifest.source_commit !== pinned || transport.source_commit !== pinned) {
  throw new Error('Vendored crawler source commit is not pinned.');
}

// The external API package is now copied byte-for-byte from the pinned source
// tree.  No desktop business-logic override is allowed here.
for (const [relative, sourceBlob] of Object.entries(transport.source_files ?? {})) {
  const buffer = actualBuffers[relative];
  if (!buffer) throw new Error(`Vendored source API file is missing: ${relative}`);
  if (!sourceBlobMatches(buffer, sourceBlob)) {
    throw new Error(`Vendored source API file diverged from source: ${relative}`);
  }
}

for (const [relative, descriptor] of Object.entries(transport.transport_overrides ?? {})) {
  const buffer = actualBuffers[relative];
  if (!buffer) throw new Error(`Vendored transport override is missing: ${relative}`);
  if (!sourceBlobMatches(buffer, descriptor.desktop_git_blob)) {
    throw new Error(`Vendored transport override changed unexpectedly: ${relative}`);
  }
}

const legacyActual = { ...actual };
const legacyExpected = { ...manifest.files };
for (const relative of Object.keys(transport.source_files ?? {})) {
  delete legacyActual[relative];
  delete legacyExpected[relative];
}
for (const relative of Object.keys(transport.transport_overrides ?? {})) {
  delete legacyActual[relative];
  delete legacyExpected[relative];
}

if (JSON.stringify(legacyActual) !== JSON.stringify(legacyExpected)) {
  throw new Error('Vendored crawler hash manifest does not match the packaged files.');
}
console.log(
  `vendored crawler integrity: PASS (${Object.keys(actual).length} files; exact source external API verified)`,
);
