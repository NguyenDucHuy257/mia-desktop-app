import { readFile, readdir, stat } from 'node:fs/promises';
import path from 'node:path';

const packageRoot = process.env.MIA_PACKAGE_SCAN_ROOT || 'release';
const roots = [path.join(packageRoot, 'win-unpacked/resources/app.asar'), 'runtime/dist/mia-runtime'];
const forbidden = [
  { label: 'GitHub token', pattern: /gh[opsu]_[A-Za-z0-9]{20,}/ },
  { label: 'private key', pattern: /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/ },
  { label: 'embedded API key assignment', pattern: /X-MIA-API-Key["']?\s*[:=]\s*["'][^"']{12,}/i },
];

async function filesBelow(candidate) {
  try {
    const info = await stat(candidate);
    if (info.isFile()) return [candidate];
    const result = [];
    for (const entry of await readdir(candidate, { withFileTypes: true })) {
      const child = path.join(candidate, entry.name);
      if (entry.isDirectory()) result.push(...await filesBelow(child));
      else if (entry.isFile()) result.push(child);
    }
    return result;
  } catch { return []; }
}

const files = (await Promise.all(roots.map(filesBelow))).flat();
if (!files.length) throw new Error('No packaged files found for secret scan.');
for (const file of files) {
  const extension = path.extname(file).toLowerCase();
  if (path.basename(file) !== 'app.asar' && !['.py', '.pyc', '.json', '.txt', '.yml', '.yaml', '.ini', '.cfg'].includes(extension)) continue;
  const buffer = await readFile(file);
  if (buffer.length > 100 * 1024 * 1024) continue;
  const content = buffer.toString('latin1');
  for (const rule of forbidden) if (rule.pattern.test(content)) throw new Error(`${rule.label} detected in ${file}`);
}
console.log(`packaged secret scan: PASS (${files.length} files)`);
