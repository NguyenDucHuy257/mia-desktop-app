import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';

const distDirectory = path.resolve('dist');
const forbiddenMarkers = [
  'X-MIA-API-Key',
  'MIA_API_ACCESS_TOKEN',
  'VITE_MIA_DEV_API_KEY',
];

async function filesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const absolute = path.join(directory, entry.name);
    return entry.isDirectory() ? filesUnder(absolute) : [absolute];
  }));
  return nested.flat();
}

const candidateFiles = (await filesUnder(distDirectory)).filter((file) => (
  /\.(?:html|js|map|css)$/i.test(file)
));
const violations = [];

for (const file of candidateFiles) {
  const content = await readFile(file, 'utf8');
  for (const marker of forbiddenMarkers) {
    if (content.includes(marker)) violations.push(`${path.relative(distDirectory, file)}: ${marker}`);
  }
}

if (violations.length > 0) {
  throw new Error(`Renderer bundle contains forbidden API markers:\n${violations.join('\n')}`);
}

process.stdout.write(`renderer secret scan: PASS (${candidateFiles.length} files)\n`);
