import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { createMiaApiClientFromEnvironment } = require('../electron/mia-api-client.cjs');

const required = [
  'MIA_API_BASE_URL',
  'MIA_API_ACCESS_TOKEN',
  'MIA_SMOKE_TAX_CODE',
  'MIA_SMOKE_PORTAL_PASSWORD',
];
const missing = required.filter((name) => !process.env[name]);

if (missing.length > 0) {
  throw new Error(`Missing required smoke-test variables: ${missing.join(', ')}`);
}
if (process.env.MIA_SMOKE_ALLOW_REVOKE !== 'dedicated-test-account') {
  throw new Error('Set MIA_SMOKE_ALLOW_REVOKE=dedicated-test-account only for an isolated staging account.');
}

const client = createMiaApiClientFromEnvironment();
const credentials = {
  username: process.env.MIA_SMOKE_TAX_CODE,
  password: process.env.MIA_SMOKE_PORTAL_PASSWORD,
};
let connectionId;
let revoked = false;

try {
  const created = await client.createConnection(credentials);
  connectionId = created.connection_id;
  process.stdout.write('create: PASS\n');

  await client.getConnection(connectionId);
  process.stdout.write('get: PASS\n');

  await client.reconnectConnection(connectionId, credentials);
  process.stdout.write('reconnect: PASS\n');

  await client.revokeConnection(connectionId);
  revoked = true;
  process.stdout.write('revoke: PASS\n');
} catch (error) {
  const code = typeof error?.code === 'string' ? error.code : 'unknown_error';
  const status = Number.isInteger(error?.status) ? String(error.status) : 'n/a';
  const requestId = typeof error?.requestId === 'string' ? error.requestId : 'n/a';
  process.stderr.write(`account-connections smoke failed (code=${code}, status=${status}, request_id=${requestId})\n`);
  process.exitCode = 1;
} finally {
  if (connectionId && !revoked) {
    await client.revokeConnection(connectionId).catch(() => undefined);
  }
}
