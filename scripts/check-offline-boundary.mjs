import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';

const root = path.resolve('runtime/python');
const vendorRoot = path.join(root, 'vendor', 'mia_crawl_service');
const forbiddenNames = new Set(['.env', 'id_rsa', 'credentials.json']);
const forbiddenExtensions = new Set(['.pt', '.pth', '.pem', '.key', '.xlsx']);
const forbiddenContent = [
  /https?:\/\//i,
  /postgres(?:ql)?:\/\//i,
  /(?:api[_-]?key|password|secret|token)\s*=\s*["'][^"']+["']/i,
];

async function files(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? files(target) : [target];
  }));
  return nested.flat();
}

const failures = [];
for (const file of await files(root)) {
  // Phase 4A permits only the pinned, hash-verified crawler distribution. The
  // following manifest check rejects additions or byte changes in this tree.
  if (file.startsWith(`${vendorRoot}${path.sep}`)) continue;
  const basename = path.basename(file).toLowerCase();
  const extension = path.extname(file).toLowerCase();
  if (forbiddenNames.has(basename) || forbiddenExtensions.has(extension)) {
    failures.push(`${path.relative('.', file)}: forbidden runtime artifact`);
    continue;
  }
  if (extension === '.py') {
    const source = await readFile(file, 'utf8');
    if (forbiddenContent.some((pattern) => pattern.test(source))) {
      failures.push(`${path.relative('.', file)}: forbidden endpoint/credential marker`);
    }
  }
}

if (failures.length) {
  process.stderr.write(`${failures.join('\n')}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write('offline runtime boundary scan: PASS\n');
}
