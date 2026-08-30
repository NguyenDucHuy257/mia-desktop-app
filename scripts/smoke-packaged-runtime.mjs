import { access, mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { PythonRuntimeClient } = require('../electron/python-runtime-client.cjs');
const executable = path.resolve(process.env.MIA_PACKAGED_RUNTIME_PATH || 'runtime/dist/mia-runtime/mia-runtime.exe');
const dataDirectory = await mkdtemp(path.join(os.tmpdir(), 'mia-packaged-runtime-'));
const browserDirectory = path.resolve(process.env.MIA_PACKAGED_BROWSER_PATH || 'runtime/browsers');
const packagedTemplateDirectory = path.join(
  path.dirname(executable),
  '_internal',
  'vendor',
  'mia_crawl_service',
  'resources',
  'templates',
);
const requiredResultTemplates = [
  'electronic.xlsx',
  'cash_register.xlsx',
  'cash_register_purchase.xlsx',
  'invoice_detail.xlsx',
];
const client = new PythonRuntimeClient({
  runtimeExecutable: executable,
  defaultTimeoutMs: 10000,
  env: {
    PLAYWRIGHT_BROWSERS_PATH: browserDirectory,
    MIA_SESSION_ENCRYPTION_KEY: randomBytes(32).toString('base64url'),
    MIA_SESSION_ENCRYPTION_KEY_ID: 'packaged-smoke',
  },
});

async function removeGeneratedBrowserLogs(directory) {
  const entries = await readdir(directory, { withFileTypes: true }).catch(() => []);
  await Promise.all(entries.map(async (entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      await removeGeneratedBrowserLogs(target);
    } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.log')) {
      await rm(target, { force: true });
    }
  }));
}

try {
  await Promise.all(requiredResultTemplates.map((name) => access(path.join(packagedTemplateDirectory, name))));
  await removeGeneratedBrowserLogs(browserDirectory);
  await client.start();
  const health = await client.call('system.health');
  const storage = await client.call('storage.initialize', { data_dir: dataDirectory });
  const sourceAccounts = await client.call('source.accounts.list', {}, { timeoutMs: 30000 });
  const pdf = await client.call('artifacts.pdf_health', {}, { timeoutMs: 30000 });
  if (
    health.runtime_version !== '0.5.0'
    || storage.schema_version !== 4
    || storage.integrity !== 'ok'
    || !Array.isArray(sourceAccounts)
    || sourceAccounts.length !== 0
    || pdf.ready !== true
  ) {
    throw new Error('Packaged runtime returned an unexpected response.');
  }
  const account = await client.call('accounts.create', {
    account_id: 'smoke-account', tax_code: '0100000000',
    encrypted_password: 'synthetic-ciphertext', timestamp: '2026-08-18T00:00:00Z',
  });
  const accounts = await client.call('accounts.list');
  if (account.status !== 'unchecked' || accounts.length !== 1 || JSON.stringify(accounts).includes('synthetic-ciphertext')) {
    throw new Error('Packaged account storage smoke failed.');
  }
  const job = await client.call('jobs.start', {
    job_id: 'job_smoke', connection_id: 'smoke-account', idempotency_key: 'desktop-smoke-key',
    intent: { directions: ['purchase'] }, timestamp: '2026-08-18T00:01:00Z',
  });
  const duplicate = await client.call('jobs.start', {
    job_id: 'job_duplicate', connection_id: 'smoke-account', idempotency_key: 'desktop-smoke-key',
    intent: { directions: ['purchase'] }, timestamp: '2026-08-18T00:01:00Z',
  });
  const resumed = await client.call('jobs.resume');
  const cancelled = await client.call('jobs.cancel', { job_id: job.job_id, timestamp: '2026-08-18T00:02:00Z' });
  if (duplicate.job_id !== job.job_id || resumed.job_id !== job.job_id || cancelled.status !== 'cancelled') {
    throw new Error('Packaged job lifecycle smoke failed.');
  }
  await client.call('jobs.clear');
  await client.call('accounts.delete', { account_id: 'smoke-account' });
  process.stdout.write('packaged Python runtime smoke: PASS\n');
} catch (error) {
  const diagnostic = await readFile(path.join(dataDirectory, 'logs', 'runtime.log'), 'utf8').catch(() => 'runtime log unavailable');
  process.stderr.write(`${diagnostic}\n`);
  throw error;
} finally {
  await client.stop();
  await removeGeneratedBrowserLogs(browserDirectory);
  await rm(dataDirectory, { recursive: true, force: true });
}
