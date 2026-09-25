import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { afterEach, describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { AUTH_FILE, createOfflineAuthManager } = require('../../electron/offline-auth.cjs');
const directories: string[] = [];

afterEach(() => {
  for (const value of directories.splice(0)) fs.rmSync(value, { recursive: true, force: true });
});

function protector() {
  return {
    encrypt(value: string) { return Buffer.from(`protected:${Buffer.from(value).toString('base64')}`); },
    decrypt(value: Buffer) {
      const text = value.toString();
      if (!text.startsWith('protected:')) throw new Error('decrypt failed');
      return Buffer.from(text.slice(10), 'base64').toString();
    },
  };
}

function setup(now = () => Date.now()) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-offline-auth-'));
  directories.push(directory);
  const logger = { info: vi.fn(), warn: vi.fn() };
  return { directory, logger, manager: createOfflineAuthManager({ directory, protector: protector(), logger, now }) };
}

describe('offline password protection', () => {
  it('requires setup, stores no plaintext, and locks again after restart', async () => {
    const value = setup();
    expect(value.manager.status()).toMatchObject({ state: 'setup_required', configured: false, unlocked: false });
    await expect(value.manager.create('short', 'short')).rejects.toMatchObject({ code: 'offline_password_invalid' });
    await expect(value.manager.create('Mat-khau-rieng-2026', 'khong-khop')).rejects.toMatchObject({ code: 'offline_password_confirmation_mismatch' });
    expect(await value.manager.create('Mat-khau-rieng-2026', 'Mat-khau-rieng-2026')).toMatchObject({ state: 'unlocked', configured: true, unlocked: true });
    expect(fs.readFileSync(path.join(value.directory, AUTH_FILE), 'utf8')).not.toContain('Mat-khau-rieng-2026');

    const restarted = createOfflineAuthManager({ directory: value.directory, protector: protector() });
    expect(restarted.status()).toMatchObject({ state: 'locked', unlocked: false });
    await expect(restarted.unlock('Mat-khau-sai-2026')).rejects.toMatchObject({ code: 'offline_password_incorrect' });
    expect(await restarted.unlock('Mat-khau-rieng-2026')).toMatchObject({ state: 'unlocked', unlocked: true });
  });

  it('requires the current password and changes it without accepting the old one', async () => {
    const value = setup();
    await value.manager.create('Mat-khau-cu-2026', 'Mat-khau-cu-2026');
    await expect(value.manager.change('Mat-khau-sai-2026', 'Mat-khau-moi-2026', 'Mat-khau-moi-2026')).rejects.toMatchObject({ code: 'offline_password_incorrect' });
    await expect(value.manager.change('Mat-khau-cu-2026', 'Mat-khau-moi-2026', 'khong-khop')).rejects.toMatchObject({ code: 'offline_password_confirmation_mismatch' });
    await value.manager.change('Mat-khau-cu-2026', 'Mat-khau-moi-2026', 'Mat-khau-moi-2026');

    const restarted = createOfflineAuthManager({ directory: value.directory, protector: protector() });
    await expect(restarted.unlock('Mat-khau-cu-2026')).rejects.toMatchObject({ code: 'offline_password_incorrect' });
    await expect(restarted.unlock('Mat-khau-moi-2026')).resolves.toMatchObject({ unlocked: true });
  });

  it('rate limits repeated guesses and fails closed on corrupt state', async () => {
    let timestamp = Date.parse('2026-09-10T00:00:00Z');
    const value = setup(() => timestamp);
    await value.manager.create('Mat-khau-dung-2026', 'Mat-khau-dung-2026');
    value.manager.lock();
    for (let index = 0; index < 5; index += 1) {
      await expect(value.manager.unlock('Mat-khau-sai-2026')).rejects.toMatchObject({ code: 'offline_password_incorrect' });
    }
    expect(value.manager.status().retry_after_seconds).toBe(30);
    await expect(value.manager.unlock('Mat-khau-dung-2026')).rejects.toMatchObject({ code: 'offline_auth_rate_limited' });
    timestamp += 31_000;
    await expect(value.manager.unlock('Mat-khau-dung-2026')).resolves.toMatchObject({ unlocked: true });

    fs.writeFileSync(path.join(value.directory, AUTH_FILE), 'corrupt');
    expect(() => value.manager.status()).toThrowError(expect.objectContaining({ code: 'offline_auth_state_corrupt' }));
  });

  it('replaces a configured password only through the recovery entry point', async () => {
    const value = setup();
    await value.manager.create('Mat-khau-cu-2026', 'Mat-khau-cu-2026');
    value.manager.lock();
    await expect(value.manager.recover('Mat-khau-moi-2026', 'khong-khop')).rejects.toMatchObject({ code: 'offline_password_confirmation_mismatch' });
    await expect(value.manager.recover('Mat-khau-moi-2026', 'Mat-khau-moi-2026')).resolves.toMatchObject({ unlocked: true });
    const restarted = createOfflineAuthManager({ directory: value.directory, protector: protector() });
    await expect(restarted.unlock('Mat-khau-cu-2026')).rejects.toMatchObject({ code: 'offline_password_incorrect' });
    await expect(restarted.unlock('Mat-khau-moi-2026')).resolves.toMatchObject({ unlocked: true });
  });
});
