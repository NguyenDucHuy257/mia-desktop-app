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
  return buffer.includes(0) ? buffer : Buffer.from(buffer.toString('utf8').replaceAll('\r\n', '\n'));
}

function gitBlobSha(buffer) {
  const value = canonicalBytes(buffer);
  return createHash('sha1')
    .update(Buffer.from(`blob ${value.length}\0`, 'utf8'))
    .update(value)
    .digest('hex');
}

async function filesBelow(directory) {
  const result = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name === 'VENDOR-MANIFEST.json' || entry.name === 'VENDOR-TRANSPORT.json') continue;
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
  const buffer = canonicalBytes(await readFile(file));
  actualBuffers[relative] = buffer;
  actual[relative] = createHash('sha256').update(buffer).digest('hex');
}

const pinned = '63acf111c64b47ac964608141b2c83bbb6e2f688';
if (manifest.source_commit !== pinned || transport.source_commit !== pinned) {
  throw new Error('Vendored crawler source commit is not pinned.');
}

// Desktop reuses only source DTO/service modules needed in-process. It must not
// package the upstream HTTP listener/auth/audit host or CLI server entrypoints.
const forbiddenServerFiles = [
  'app/external_api/__main__.py',
  'app/external_api/app.py',
  'app/external_api/cli.py',
  'app/external_api/config.py',
  'app/external_api/factory.py',
  'app/external_api/repository.py',
  'app/external_api/security.py',
];
for (const relative of forbiddenServerFiles) {
  if (actualBuffers[relative]) {
    throw new Error(`Server-only external API file must not be packaged: ${relative}`);
  }
}

for (const [relative, sourceBlob] of Object.entries(transport.source_files ?? {})) {
  const buffer = actualBuffers[relative];
  if (!buffer) throw new Error(`Vendored source service file is missing: ${relative}`);
  if (gitBlobSha(buffer) !== sourceBlob) {
    throw new Error(`Vendored source service file diverged from source: ${relative}`);
  }
}

// The package initializer is the one intentional transport override: importing
// in-process source service/results must not import a missing/server-only app.
for (const [relative, descriptor] of Object.entries(transport.transport_overrides ?? {})) {
  const buffer = actualBuffers[relative];
  if (!buffer) throw new Error(`Vendored transport override is missing: ${relative}`);
  if (gitBlobSha(buffer) !== descriptor.desktop_git_blob) {
    throw new Error(`Vendored transport override changed unexpectedly: ${relative}`);
  }
}

const legacyActual = { ...actual };
const legacyExpected = { ...manifest.files };
for (const relative of Object.keys(transport.source_files ?? {})) delete legacyActual[relative];
for (const relative of Object.keys(transport.transport_overrides ?? {})) {
  delete legacyActual[relative];
  delete legacyExpected[relative];
}

if (JSON.stringify(legacyActual) !== JSON.stringify(legacyExpected)) {
  throw new Error('Vendored crawler hash manifest does not match the packaged files.');
}
console.log(`vendored crawler integrity: PASS (${Object.keys(actual).length} files; local-only source transport verified)`);
