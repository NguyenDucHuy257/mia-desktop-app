const { app, BrowserWindow, dialog, ipcMain, safeStorage } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { ensureDeviceIdentity, signChallenge } = require('./device-identity.cjs');
const { isTrustedAppUrl } = require('./security-policy.cjs');
const { createJobLifecycleBroker } = require('./job-lifecycle-broker.cjs');
const { OfflineRuntimeManager } = require('./offline-runtime-manager.cjs');
const { createLocalAccountBroker } = require('./local-account-broker.cjs');

const LICENSE_FILE = 'license-token.bin';
let jobLifecycleBroker;
let offlineRuntime;
let localAccountBroker;
let runtimeShutdownStarted = false;

function jobs() {
  if (!jobLifecycleBroker) {
    jobLifecycleBroker = createJobLifecycleBroker(() => offlineRuntime);
  }
  return jobLifecycleBroker;
}

function secureProtector() {
  return {
    encrypt(value) {
      if (!safeStorage.isEncryptionAvailable()) {
        throw new Error('OS secure storage is not available');
      }
      return safeStorage.encryptString(value);
    },
    decrypt(value) {
      if (!safeStorage.isEncryptionAvailable()) {
        throw new Error('OS secure storage is not available');
      }
      return safeStorage.decryptString(value);
    },
  };
}

function securityDirectory() {
  return path.join(app.getPath('userData'), 'security');
}

function productionEntryUrl() {
  return pathToFileURL(path.join(__dirname, '..', 'dist', 'index.html')).toString();
}

function assertTrustedSender(event) {
  const senderUrl = event.senderFrame?.url ?? event.sender.getURL();
  if (!isTrustedAppUrl(senderUrl, {
    devServerUrl: process.env.VITE_DEV_SERVER_URL,
    productionEntryUrl: productionEntryUrl(),
  })) {
    throw new Error('untrusted IPC sender');
  }
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1500,
    height: 1024,
    minWidth: 1024,
    minHeight: 720,
    useContentSize: true,
    show: false,
    backgroundColor: '#ffffff',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  window.once('ready-to-show', () => window.show());
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', (event, url) => {
    if (!isTrustedAppUrl(url, {
      devServerUrl: process.env.VITE_DEV_SERVER_URL,
      productionEntryUrl: productionEntryUrl(),
    })) event.preventDefault();
  });

  if (process.env.VITE_DEV_SERVER_URL) {
    void window.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    void window.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }
}

ipcMain.handle('mia:device-identity', (event) => {
  assertTrustedSender(event);
  return ensureDeviceIdentity(securityDirectory(), secureProtector());
});
ipcMain.handle('mia:sign-device-challenge', (event, challenge) => {
  assertTrustedSender(event);
  return signChallenge(securityDirectory(), secureProtector(), challenge);
});
ipcMain.handle('mia:license-store', (event, token) => {
  assertTrustedSender(event);
  if (typeof token !== 'string' || token.length < 16 || token.length > 8192) {
    throw new TypeError('invalid license token');
  }
  const directory = securityDirectory();
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  fs.writeFileSync(
    path.join(directory, LICENSE_FILE),
    secureProtector().encrypt(token),
    { mode: 0o600 },
  );
  return true;
});
function localAccounts() {
  if (!localAccountBroker) localAccountBroker = createLocalAccountBroker(() => offlineRuntime, secureProtector());
  return localAccountBroker;
}

ipcMain.handle('mia:account-connections:create', (event, credentials) => {
  assertTrustedSender(event);
  return localAccounts().create(credentials);
});
ipcMain.handle('mia:account-connections:list', (event) => {
  assertTrustedSender(event);
  return localAccounts().list();
});
ipcMain.handle('mia:account-connections:get', (event, connectionId) => {
  assertTrustedSender(event);
  return localAccounts().get(connectionId);
});
ipcMain.handle('mia:account-connections:reconnect', (event, connectionId, credentials) => {
  assertTrustedSender(event);
  return localAccounts().reconnect(connectionId, credentials);
});
ipcMain.handle('mia:account-connections:revoke', (event, connectionId) => {
  assertTrustedSender(event);
  return localAccounts().revoke(connectionId);
});
for (const [channel, method] of [
  ['mia:jobs:resume', 'resume'], ['mia:jobs:start', 'start'], ['mia:jobs:status', 'status'],
  ['mia:jobs:summary', 'summary'], ['mia:jobs:cancel', 'cancel'], ['mia:jobs:clear', 'clear'],
]) {
  ipcMain.handle(channel, (event, ...args) => {
    assertTrustedSender(event);
    return jobs()[method](...args);
  });
}

void app.whenReady().then(async () => {
  offlineRuntime = new OfflineRuntimeManager({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    dataDirectory: path.join(app.getPath('userData'), 'offline-runtime'),
  });
  await offlineRuntime.start();
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
}).catch(() => {
  dialog.showErrorBox('MIA WT', 'Không thể khởi động bộ xử lý dữ liệu cục bộ. Vui lòng mở lại ứng dụng hoặc cài đặt lại.');
  app.quit();
});

app.on('before-quit', (event) => {
  if (!offlineRuntime || runtimeShutdownStarted) return;
  event.preventDefault();
  runtimeShutdownStarted = true;
  void offlineRuntime.stop().finally(() => app.quit());
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
