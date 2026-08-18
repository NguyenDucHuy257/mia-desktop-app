import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { createReleaseUpdater, validateChannel } = require('../../electron/release-updater.cjs');

describe('release updater', () => {
  it('is disabled outside packaged builds and validates channels', async () => {
    const updater = createReleaseUpdater({ isPackaged: false });
    expect(await updater.check()).toMatchObject({ phase: 'disabled' });
    expect(() => validateChannel('nightly')).toThrow();
  });

  it('requires explicit download and installs only a fully downloaded update', async () => {
    const autoUpdater = Object.assign(new EventEmitter(), {
      checkForUpdates: vi.fn(async () => undefined), downloadUpdate: vi.fn(async () => undefined), quitAndInstall: vi.fn(),
    });
    const updater = createReleaseUpdater({ isPackaged: true, autoUpdater });
    expect((autoUpdater as typeof autoUpdater & { autoDownload: boolean }).autoDownload).toBe(false);
    await updater.check();
    autoUpdater.emit('update-available', { version: '0.2.0' });
    await updater.download();
    autoUpdater.emit('update-downloaded', { version: '0.2.0' });
    updater.install();
    expect(autoUpdater.quitAndInstall).toHaveBeenCalledWith(false, true);
  });

  it('sanitizes updater errors and clamps progress', () => {
    const autoUpdater = Object.assign(new EventEmitter(), {});
    const updater = createReleaseUpdater({ isPackaged: true, autoUpdater });
    autoUpdater.emit('download-progress', { percent: 120 });
    expect(updater.status().percent).toBe(100);
    autoUpdater.emit('error', new Error('private release URL'));
    expect(updater.status()).toMatchObject({ phase: 'error', error: 'update_failed' });
  });
});
