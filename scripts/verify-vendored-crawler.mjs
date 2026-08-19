import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve('runtime/python/vendor/mia_crawl_service');
const manifestPath = path.join(root, 'VENDOR-MANIFEST.json');
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));

function canonicalBytes(buffer) {
  // Git may materialize text files as CRLF on Windows and LF on CI. The
  // integrity manifest protects source content, not the checkout convention.
  return buffer.includes(0) ? buffer : Buffer.from(buffer.toString('utf8').replaceAll('\r\n', '\n'));
}

async function filesBelow(directory) {
  const result = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.name === '__pycache__' || entry.name === 'VENDOR-MANIFEST.json') continue;
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) result.push(...await filesBelow(absolute));
    else if (entry.isFile()) result.push(absolute);
  }
  return result;
}

const actual = {};
for (const file of (await filesBelow(root)).sort()) {
  const relative = path.relative(root, file).replaceAll('\\', '/');
  actual[relative] = createHash('sha256').update(canonicalBytes(await readFile(file))).digest('hex');
}

if (manifest.source_commit !== '63acf111c64b47ac964608141b2c83bbb6e2f688') {
  throw new Error('Vendored crawler source commit is not pinned.');
}
if (JSON.stringify(actual) !== JSON.stringify(manifest.files)) {
  throw new Error('Vendored crawler hash manifest does not match the packaged files.');
}
console.log(`vendored crawler integrity: PASS (${Object.keys(actual).length} files)`);
