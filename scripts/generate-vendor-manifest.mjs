import { createHash } from 'node:crypto';
import { readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve('runtime/python/vendor/mia_crawl_service');
const manifestPath = path.join(root, 'VENDOR-MANIFEST.json');

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

const files = {};
for (const filename of (await filesBelow(root)).sort()) {
  const bytes = await readFile(filename);
  const canonical = bytes.includes(0) ? bytes : Buffer.from(bytes.toString('utf8').replaceAll('\r\n', '\n'));
  files[path.relative(root, filename).replaceAll('\\', '/')] = createHash('sha256').update(canonical).digest('hex');
}

await writeFile(manifestPath, `${JSON.stringify({
  source_repository: 'https://github.com/hvsoftware26/mia-crawl-service',
  source_commit: '63acf111c64b47ac964608141b2c83bbb6e2f688',
  selection: ['production pipeline dependency closure under app/**', 'resources/**'],
  excluded: ['HTTP server entrypoints', 'deployment scripts', 'environment files', 'credentials', 'runtime data'],
  files,
}, null, 2)}\n`, 'utf8');
