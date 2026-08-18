'use strict';

const CHANNELS = new Set(['stable', 'beta']);

function validateChannel(value) {
  if (!CHANNELS.has(value)) throw new TypeError('invalid_update_channel');
  return value;
}

function createReleaseUpdater({ isPackaged, autoUpdater }) {
  let state = { phase: isPackaged ? 'idle' : 'disabled', version: null, percent: 0, error: null };
  if (isPackaged && autoUpdater) {
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.allowPrerelease = false;
    autoUpdater.on('checking-for-update', () => { state = { ...state, phase: 'checking', error: null }; });
    autoUpdater.on('update-available', (info) => { state = { ...state, phase: 'available', version: String(info.version) }; });
    autoUpdater.on('update-not-available', () => { state = { ...state, phase: 'current' }; });
    autoUpdater.on('download-progress', (progress) => { state = { ...state, phase: 'downloading', percent: Math.max(0, Math.min(100, Math.floor(progress.percent))) }; });
    autoUpdater.on('update-downloaded', (info) => { state = { ...state, phase: 'ready', version: String(info.version), percent: 100 }; });
    autoUpdater.on('error', () => { state = { ...state, phase: 'error', error: 'update_failed' }; });
  }
  return Object.freeze({
    status: () => ({ ...state }),
    setChannel(value) {
      const channel = validateChannel(value);
      if (!isPackaged || !autoUpdater) return { ...state };
      autoUpdater.channel = channel;
      autoUpdater.allowPrerelease = channel === 'beta';
      return { ...state };
    },
    async check() {
      if (!isPackaged || !autoUpdater) return { ...state };
      await autoUpdater.checkForUpdates();
      return { ...state };
    },
    async download() {
      if (!isPackaged || !autoUpdater || state.phase !== 'available') throw new Error('update_not_available');
      await autoUpdater.downloadUpdate();
      return { ...state };
    },
    install() {
      if (!isPackaged || !autoUpdater || state.phase !== 'ready') throw new Error('update_not_ready');
      autoUpdater.quitAndInstall(false, true);
    },
  });
}

module.exports = { createReleaseUpdater, validateChannel };
