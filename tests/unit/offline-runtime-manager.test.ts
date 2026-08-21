import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const require = (await import('node:module')).createRequire(import.meta.url);
const { OfflineRuntimeManager } = require('../../electron/offline-runtime-manager.cjs');

const managers: Array<{ stop(): Promise<void> }> = [];
const directories: string[] = [];
const PROCESS_TEST_TIMEOUT_MS = 15_000;

async function manager(maxRestarts = 2) {
  const dataDirectory = await mkdtemp(path.join(os.tmpdir(), 'mia-runtime-manager-'));
  directories.push(dataDirectory);
  const value = new OfflineRuntimeManager({ dataDirectory, isPackaged: false, maxRestarts });
  managers.push(value);
  return value;
}

afterEach(async () => {
  await Promise.all(managers.splice(0).map((value) => value.stop()));
  await Promise.all(directories.splice(0).map((value) => rm(value, { recursive: true, force: true })));
});

describe('OfflineRuntimeManager', () => {
  it('deduplicates concurrent starts and initializes SQLite', async () => {
    const runtime = await manager();
    const first = runtime.start();
    const second = runtime.start();
    expect(first).toBe(second);
    await expect(first).resolves.toMatchObject({ protocol_version: '1.0', runtime_version: '0.4.1' });
    await expect(runtime.invoke('storage.status')).resolves.toEqual({ schema_version: 4, integrity: 'ok' });
  }, PROCESS_TEST_TIMEOUT_MS);

  it('restarts after a crash and preserves the database', async () => {
    const runtime = await manager();
    await runtime.start();
    runtime.terminateForRecoveryTest();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await expect(runtime.invoke('storage.status')).resolves.toEqual({ schema_version: 4, integrity: 'ok' });
  }, PROCESS_TEST_TIMEOUT_MS);

  it('resumes the same idempotent job after a runtime crash', async () => {
    const runtime = await manager();
    await runtime.start();
    await runtime.invoke('accounts.create', {
      account_id: 'account-1', tax_code: '0101234567', encrypted_password: 'ciphertext', timestamp: 'now',
    });
    const request = {
      job_id: 'job_1', connection_id: 'account-1', idempotency_key: 'desktop-fixed',
      intent: { directions: ['purchase'] }, timestamp: '2026-08-18T00:00:00Z',
    };
    const first = await runtime.invoke('jobs.start', request);
    runtime.terminateForRecoveryTest();
    await new Promise((resolve) => setTimeout(resolve, 50));
    const resumed = await runtime.invoke('jobs.resume');
    const duplicate = await runtime.invoke('jobs.start', { ...request, job_id: 'job_2' });
    expect(resumed.job_id).toBe(first.job_id);
    expect(duplicate).toMatchObject({ job_id: first.job_id, reused: true });
  }, PROCESS_TEST_TIMEOUT_MS);

  it('enforces the bounded restart limit', async () => {
    const runtime = await manager(1);
    await runtime.start();
    runtime.terminateForRecoveryTest();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await runtime.invoke('storage.status');
    runtime.terminateForRecoveryTest();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await expect(runtime.invoke('storage.status')).rejects.toMatchObject({ code: 'runtime_restart_exhausted' });
  }, PROCESS_TEST_TIMEOUT_MS);
});
