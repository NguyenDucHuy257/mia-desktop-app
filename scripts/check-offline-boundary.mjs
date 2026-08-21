import { access, readFile, readdir } from 'node:fs/promises';
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
  // Vendored source bytes are verified separately by verify-vendored-crawler.
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

// MIA Desktop is a local process, not an HTTP API host. Keep HTTP server/client
// frameworks and the source HTTP DTO validation dependency out of the packaged
// environment. The local transport uses small dataclass DTOs instead.
const requirements = await readFile(path.resolve('runtime/requirements-crawler.txt'), 'utf8');
for (const packageName of ['fastapi', 'uvicorn', 'httpx', 'pydantic']) {
  if (new RegExp(`^${packageName}(?:[=<>~!]|$)`, 'im').test(requirements)) {
    failures.push(`runtime/requirements-crawler.txt: HTTP/API-only dependency ${packageName}`);
  }
}

// These were remote-control clients from the web/server architecture. Their
// reappearance would silently reintroduce a second production execution path.
for (const relative of [
  'electron/mia-api-client.cjs',
  'src/lib/api/client.ts',
  'scripts/account-connections-smoke.mjs',
]) {
  try {
    await access(path.resolve(relative));
    failures.push(`${relative}: remote API client/entrypoint is forbidden in local desktop`);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
}

if (failures.length) {
  process.stderr.write(`${failures.join('\n')}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write('offline runtime boundary scan: PASS\n');
}
