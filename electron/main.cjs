const { app, BrowserWindow, dialog, ipcMain, safeStorage, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { ensureDeviceIdentity, signChallenge } = require('./device-identity.cjs');
const { isTrustedAppUrl } = require('./security-policy.cjs');
const { createJobLifecycleBroker } = require('./job-lifecycle-broker.cjs');
const { OfflineRuntimeManager } = require('./offline-runtime-manager.cjs');
const { createLocalAccountBroker } = require('./local-account-broker.cjs');
const { createResultBroker } = require('./result-broker.cjs');
const { createArtifactBroker } = require('./artifact-file-broker.cjs');

const LICENSE_FILE = 'license-token.bin';
let jobLifecycleBroker;
let offlineRuntime;
let localAccountBroker;
let resultBroker;
let artifactBroker;
let runtimeShutdownStarted = false;

function jobs() {
  if (!jobLifecycleBroker) {
    jobLifecycleBroker = createJobLifecycleBroker(() => offlineRuntime, () => new Date().toISOString(), secureProtector());
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
ipcMain.handle('mia:artifacts:select-directory', async (event) => {
  assertTrustedSender(event);
  const owner = BrowserWindow.fromWebContents(event.sender);
  const result = await dialog.showOpenDialog(owner, { properties: ['openDirectory', 'createDirectory'] });
  return result.canceled ? null : result.filePaths[0] ?? null;
});
ipcMain.handle('mia:artifacts:export', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.export(request);
});
ipcMain.handle('mia:artifacts:list', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.list(request);
});
ipcMain.handle('mia:artifacts:open-directory', async (event, directory) => {
  assertTrustedSender(event);
  if (typeof directory !== 'string' || !path.isAbsolute(directory) || directory.length > 1024) throw new TypeError('invalid_artifact_directory');
  const error = await shell.openPath(path.resolve(directory));
  if (error) throw new Error('artifact_directory_open_failed');
  return true;
});
function localAccounts() {
  if (!localAccountBroker) localAccountBroker = createLocalAccountBroker(() => offlineRuntime, secureProtector());
  return localAccountBroker;
}
function results() {
  if (!resultBroker) resultBroker = createResultBroker(() => offlineRuntime);
  return resultBroker;
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
  ['mia:jobs:resume', 'resume'], ['mia:jobs:resume-all', 'resumeAll'], ['mia:jobs:start', 'start'], ['mia:jobs:status', 'status'],
  ['mia:jobs:summary', 'summary'], ['mia:jobs:cancel', 'cancel'], ['mia:jobs:clear', 'clear'],
]) {
  ipcMain.handle(channel, (event, ...args) => {
    assertTrustedSender(event);
    return jobs()[method](...args);
  });
}
for (const [channel, method] of [['mia:results:overview', 'overview'], ['mia:results:details', 'details']]) {
  ipcMain.handle(channel, (event, query) => {
    assertTrustedSender(event);
    return results()[method](query);
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
