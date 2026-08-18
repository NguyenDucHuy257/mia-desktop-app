import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';

const root = path.resolve('runtime/python/vendor/mia_crawl_service');
const manifestPath = path.join(root, 'VENDOR-MANIFEST.json');
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));

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
  actual[relative] = createHash('sha256').update(await readFile(file)).digest('hex');
}

if (manifest.source_commit !== '64ebb6ec0a35c784e194e8ce116c4bb4cf1b19d3') {
  throw new Error('Vendored crawler source commit is not pinned.');
}
if (JSON.stringify(actual) !== JSON.stringify(manifest.files)) {
  throw new Error('Vendored crawler hash manifest does not match the packaged files.');
}
console.log(`vendored crawler integrity: PASS (${Object.keys(actual).length} files)`);
