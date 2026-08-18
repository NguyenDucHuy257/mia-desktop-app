import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { PythonRuntimeClient } = require('../electron/python-runtime-client.cjs');
const executable = path.resolve(process.env.MIA_PACKAGED_RUNTIME_PATH || 'runtime/dist/mia-runtime/mia-runtime.exe');
const dataDirectory = await mkdtemp(path.join(os.tmpdir(), 'mia-packaged-runtime-'));
const client = new PythonRuntimeClient({ runtimeExecutable: executable, defaultTimeoutMs: 10000 });

try {
  await client.start();
  const health = await client.call('system.health');
  const storage = await client.call('storage.initialize', { data_dir: dataDirectory });
  if (health.runtime_version !== '0.2.0' || storage.schema_version !== 1 || storage.integrity !== 'ok') {
    throw new Error('Packaged runtime returned an unexpected response.');
  }
  process.stdout.write('packaged Python runtime smoke: PASS\n');
} finally {
  await client.stop();
  await rm(dataDirectory, { recursive: true, force: true });
}
