import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const require = (await import('node:module')).createRequire(import.meta.url);
const {
  PythonRuntimeClient,
  runtimeEnvironment,
} = require('../../electron/python-runtime-client.cjs');

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const fixture = (name: string) => path.join(testDirectory, '..', 'fixtures', 'python', name);

describe('PythonRuntimeClient', () => {
  it('starts, exchanges JSON-RPC messages and shuts down cleanly', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    const health = await client.call('system.health');
    expect(health).toMatchObject({ protocol_version: '1.0', runtime_version: '0.4.1' });
    expect(await client.call('system.echo', { value: 'xin chào' })).toEqual({ value: 'xin chào' });
    await client.stop();
    await expect(client.call('system.health')).rejects.toMatchObject({ code: 'runtime_not_running' });
  });

  it('times out one request without corrupting later responses', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    await expect(client.call('system.sleep', { milliseconds: 100 }, { timeoutMs: 20 }))
      .rejects.toMatchObject({ code: 'runtime_timeout' });
    await new Promise((resolve) => setTimeout(resolve, 120));
    await expect(client.call('system.health')).resolves.toMatchObject({ protocol_version: '1.0' });
    await client.stop();
  });

  it('returns sanitized JSON-RPC errors', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    await expect(client.call('unknown.method')).rejects.toMatchObject({
      code: -32601,
      message: 'method_not_found',
    });
    await client.stop();
  });

  it('rejects pending work when the child process crashes', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    const pending = client.call('system.sleep', { milliseconds: 1000 });
    client.terminate();
    await expect(pending).rejects.toMatchObject({ code: 'runtime_exited' });
    await client.stop();
  });

  it('rejects malformed and oversized runtime output', async () => {
    for (const script of ['malformed_runtime.py', 'oversized_runtime.py']) {
      const client = new PythonRuntimeClient({ runtimeScript: fixture(script) });
      await client.start();
      await expect(client.call('system.health')).rejects.toMatchObject({
        code: script.startsWith('malformed') ? 'invalid_response' : 'response_too_large',
      });
      await client.stop();
    }
  });

  it('passes only allowlisted environment variables to Python', () => {
    const env = runtimeEnvironment({
      PATH: 'safe-path',
      MIA_API_ACCESS_TOKEN: 'must-not-cross-boundary',
      PORTAL_PASSWORD: 'must-not-cross-boundary',
    }, {
      MIA_RUNTIME_DATA_DIR: 'safe-data-directory',
      PLAYWRIGHT_BROWSERS_PATH: 'safe-browser-directory',
      ARBITRARY_SECRET: 'must-not-cross-boundary',
    });
    expect(env.PATH).toBe('safe-path');
    expect(env.MIA_RUNTIME_DATA_DIR).toBe('safe-data-directory');
    expect(env.PLAYWRIGHT_BROWSERS_PATH).toBe('safe-browser-directory');
    expect(env).not.toHaveProperty('MIA_API_ACCESS_TOKEN');
    expect(env).not.toHaveProperty('PORTAL_PASSWORD');
    expect(env).not.toHaveProperty('ARBITRARY_SECRET');
  });

  it('rejects requests larger than one MiB before writing to the process', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    await expect(client.call('system.echo', { value: 'x'.repeat(1024 * 1024) }))
      .rejects.toMatchObject({ code: 'request_too_large' });
    await client.stop();
  });
});
