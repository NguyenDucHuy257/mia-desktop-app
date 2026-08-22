import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

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
    await expect(first).resolves.toMatchObject({ protocol_version: '1.0', runtime_version: '0.5.0' });
    expect((runtime as any).sessionResetPending).toBe(false);
    await expect(runtime.invoke('storage.status')).resolves.toEqual({ schema_version: 4, integrity: 'ok' });
    expect((runtime as any).sessionResetPending).toBe(false);
  }, PROCESS_TEST_TIMEOUT_MS);

  it('restarts after a crash and preserves the database', async () => {
    const runtime = await manager();
    await runtime.start();
    runtime.terminateForRecoveryTest();
    await new Promise((resolve) => setTimeout(resolve, 50));
    await expect(runtime.invoke('storage.status')).resolves.toEqual({ schema_version: 4, integrity: 'ok' });
  }, PROCESS_TEST_TIMEOUT_MS);

  it('resumes the same idempotent legacy job after a runtime crash', async () => {
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

  it('cancels observed source jobs before shutting down the Python client', async () => {
    const runtime = await manager() as any;
    const order: string[] = [];
    const client = {
      call: vi.fn(async (method: string, params: Record<string, unknown>) => {
        order.push(method === 'source.jobs.cancel' ? `${method}:${params.job_id}` : method);
        if (method === 'source.jobs.resume_all') {
          return [
            { job_id: 'job-running', status: 'running' },
            { job_id: 'job-queued', status: 'queued' },
          ];
        }
        if (method === 'source.jobs.cancel') return { job_id: params.job_id, status: 'cancelling' };
        throw new Error(`unexpected method ${method}`);
      }),
      stop: vi.fn(async () => { order.push('client.stop'); }),
    };
    runtime.client = client;
    runtime.sourceJobsObserved = true;

    await runtime.stop();

    expect(order).toEqual([
      'source.jobs.resume_all',
      'source.jobs.cancel:job-running',
      'source.jobs.cancel:job-queued',
      'client.stop',
    ]);
    expect(client.stop).toHaveBeenCalledOnce();
  });

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
