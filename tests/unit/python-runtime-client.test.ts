import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';

const require = (await import('node:module')).createRequire(import.meta.url);
const {
  PythonRuntimeClient,
  runtimeEnvironment,
  validateRuntimeNotification,
} = require('../../electron/python-runtime-client.cjs');

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const fixture = (name: string) => path.join(testDirectory, '..', 'fixtures', 'python', name);
const PROCESS_TEST_TIMEOUT_MS = 15_000;

describe('PythonRuntimeClient', () => {
  it('starts, exchanges JSON-RPC messages and shuts down cleanly', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    const health = await client.call('system.health');
    expect(health).toMatchObject({ protocol_version: '1.0', runtime_version: '0.5.0' });
    expect(await client.call('system.echo', { value: 'xin chào' })).toEqual({ value: 'xin chào' });
    await client.stop();
    await expect(client.call('system.health')).rejects.toMatchObject({ code: 'runtime_not_running' });
  }, PROCESS_TEST_TIMEOUT_MS);

  it('times out one request without corrupting later responses', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    await expect(client.call('system.sleep', { milliseconds: 100 }, { timeoutMs: 20 }))
      .rejects.toMatchObject({ code: 'runtime_timeout' });
    await new Promise((resolve) => setTimeout(resolve, 120));
    await expect(client.call('system.health')).resolves.toMatchObject({ protocol_version: '1.0' });
    await client.stop();
  }, PROCESS_TEST_TIMEOUT_MS);

  it('returns sanitized JSON-RPC errors', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    await expect(client.call('unknown.method')).rejects.toMatchObject({
      code: -32601,
      message: 'method_not_found',
    });
    await client.stop();
  }, PROCESS_TEST_TIMEOUT_MS);

  it('accepts allowlisted export progress notifications without completing the pending RPC', async () => {
    const onNotification = vi.fn();
    const client = new PythonRuntimeClient({
      runtimeScript: fixture('notification_runtime.py'),
      onNotification,
    });
    await client.start();
    await expect(client.call('system.health')).resolves.toEqual({ ok: true });
    expect(onNotification).toHaveBeenCalledWith('export.progress', {
      status: 'running', scope: 'details', phase: 'write_rows',
      processed: 2, total: 4, percent: 50,
    });
    client.terminate();
  }, PROCESS_TEST_TIMEOUT_MS);

  it('rejects pending work when the child process crashes', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    const pending = client.call('system.sleep', { milliseconds: 1000 });
    client.terminate();
    await expect(pending).rejects.toMatchObject({ code: 'runtime_exited' });
    await client.stop();
  }, PROCESS_TEST_TIMEOUT_MS);

  it('rejects malformed and oversized runtime output', async () => {
    for (const script of ['malformed_runtime.py', 'oversized_runtime.py']) {
      const client = new PythonRuntimeClient({ runtimeScript: fixture(script) });
      await client.start();
      await expect(client.call('system.health')).rejects.toMatchObject({
        code: script.startsWith('malformed') ? 'invalid_response' : 'response_too_large',
      });
      await client.stop();
    }
  }, PROCESS_TEST_TIMEOUT_MS);

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

  it('accepts only bounded path-free invoice artifact progress', () => {
    expect(validateRuntimeNotification({ jsonrpc: '2.0', method: 'artifact.progress', params: {
      status: 'running', processed: 3, total: 8, percent: 37.5,
      artifact_key: 'purchase|query|0101|AA/26E|12|1',
      kind: 'html',
    }})).toMatchObject({ processed: 3, total: 8, percent: 37.5 });
    expect(validateRuntimeNotification({ jsonrpc: '2.0', method: 'artifact.progress', params: {
      status: 'running', processed: 3, total: 8, percent: 37.5,
      filesystem_path: 'C:\\secret\\invoice.xml',
    }})).toBeNull();
  });

  it('rejects requests larger than one MiB before writing to the process', async () => {
    const client = new PythonRuntimeClient();
    await client.start();
    await expect(client.call('system.echo', { value: 'x'.repeat(1024 * 1024) }))
      .rejects.toMatchObject({ code: 'request_too_large' });
    await client.stop();
  }, PROCESS_TEST_TIMEOUT_MS);
});
