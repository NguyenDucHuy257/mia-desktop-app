const { app, BrowserWindow, ipcMain, safeStorage } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { ensureDeviceIdentity, signChallenge } = require('./device-identity.cjs');
const { isTrustedAppUrl } = require('./security-policy.cjs');

const LICENSE_FILE = 'license-token.bin';

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

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
