const { app, BrowserWindow, dialog, ipcMain, Menu, safeStorage, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { randomBytes } = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { ensureDeviceIdentity } = require('./device-identity.cjs');
const { collectDeviceEvidence } = require('./license/hardware-profile.cjs');
const { createLicenseApi } = require('./license/license-api.cjs');
const { LicenseManager } = require('./license/license-manager.cjs');
const { createProtectedLicenseStore } = require('./license/protected-license-store.cjs');
const { isTrustedAppUrl } = require('./security-policy.cjs');
const { createJobLifecycleBroker } = require('./job-lifecycle-broker.cjs');
const { OfflineRuntimeManager } = require('./offline-runtime-manager.cjs');
const { createLocalAccountBroker } = require('./local-account-broker.cjs');
const { createResultBroker } = require('./result-broker.cjs');
const { createArtifactBroker } = require('./artifact-file-broker.cjs');
const { clearDiagnosticLogs, readPreferences, readSanitizedLogEntries, readSanitizedLogs, writePreferences } = require('./local-preferences.cjs');
const { createReleaseUpdater } = require('./release-updater.cjs');
const { createDiagnosticLogger } = require('./app-logger.cjs');
const { validateExternalUrl } = require('./external-url-policy.cjs');

const RUNTIME_KEY_FILE = 'runtime-session-key.bin';
let jobLifecycleBroker;
let offlineRuntime;
let localAccountBroker;
let resultBroker;
let artifactBroker;
let releaseUpdater;
let runtimeShutdownStarted = false;
let electronLogger;
let rendererLogger;
let licenseManagerInstance;

function diagnosticDirectory() {
  return path.join(app.getPath('userData'), 'logs');
}

function electronLog() {
  if (!electronLogger) electronLogger = createDiagnosticLogger(diagnosticDirectory(), 'electron.log');
  return electronLogger;
}

function rendererLog() {
  if (!rendererLogger) rendererLogger = createDiagnosticLogger(diagnosticDirectory(), 'renderer.log');
  return rendererLogger;
}

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

function licenses() {
  if (licenseManagerInstance) return licenseManagerInstance;
  const enabled = process.env.MIA_LICENSE_V2_ENABLED === 'true';
  const protector = secureProtector();
  let api = null;
  if (enabled) {
    const baseUrl = process.env.MIA_LICENSE_API_URL || process.env.MIA_KEY_SERVER_URL;
    if (!baseUrl) throw Object.assign(new Error('MIA license API URL is not configured'), { code: 'license_api_not_configured' });
    api = createLicenseApi({
      baseUrl,
      allowInsecureLocalhost: process.env.MIA_LICENSE_ALLOW_INSECURE_LOCALHOST === 'true',
    });
  }
  licenseManagerInstance = new LicenseManager({
    enabled,
    api,
    securityDirectory: securityDirectory(),
    store: createProtectedLicenseStore(securityDirectory(), protector),
    ensureIdentity: () => ensureDeviceIdentity(securityDirectory(), protector),
    collectEvidence: () => collectDeviceEvidence(),
    logger: electronLog(),
  });
  return licenseManagerInstance;
}

function serializeLicenseError(error) {
  const allowed = new Set([
    'invalid_phone', 'license_api_not_configured', 'license_network_error', 'license_timeout',
    'insufficient_hardware', 'hardware_query_failed', 'hardware_query_timeout',
    'device_profile_corrupt', 'license_state_corrupt', 'internal_error',
  ]);
  const code = allowed.has(String(error?.code)) ? String(error.code) : 'internal_error';
  electronLog().error('license_ipc_failed', { code, error_type: error?.name, message: error?.message });
  return { ok: false, error: { code, message: code } };
}

async function handleLicense(method, ...args) {
  try {
    const data = await licenses()[method](...args);
    return { ok: true, data };
  } catch (error) {
    return serializeLicenseError(error);
  }
}

function runtimeSessionKey() {
  const directory = securityDirectory();
  const filename = path.join(directory, RUNTIME_KEY_FILE);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  if (fs.existsSync(filename)) return secureProtector().decrypt(fs.readFileSync(filename));
  const key = randomBytes(32).toString('base64url');
  fs.writeFileSync(filename, secureProtector().encrypt(key), { mode: 0o600 });
  return key;
}

function productionEntryUrl() {
  return pathToFileURL(path.join(__dirname, '..', 'dist', 'index.html')).toString();
}

function appIconPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'icon.ico')
    : path.join(__dirname, '..', 'icon.ico');
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
    icon: appIconPath(),
    autoHideMenuBar: true,
    backgroundColor: '#ffffff',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  window.setMenu(null);
  window.setMenuBarVisibility(false);

  window.once('ready-to-show', () => {
    electronLog().info('window_ready');
    window.show();
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('render-process-gone', (_event, details) => {
    electronLog().error('renderer_process_gone', { reason: details.reason, exit_code: details.exitCode });
  });
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

for (const [channel, method] of [
  ['mia:license:status', 'status'],
  ['mia:license:initialize', 'initialize'],
  ['mia:license:submit-phone', 'submitPhone'],
  ['mia:license:retry', 'retry'],
  ['mia:license:details', 'details'],
  ['mia:license:reveal-key', 'revealKey'],
  ['mia:license:update-phone', 'updatePhone'],
]) {
  ipcMain.handle(channel, (event, ...args) => {
    assertTrustedSender(event);
    return handleLicense(method, ...args);
  });
}
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
ipcMain.handle('mia:artifacts:cancel', (event) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.cancel();
});
ipcMain.handle('mia:artifacts:targets', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.targets(request);
});
ipcMain.handle('mia:artifacts:list', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.list(request);
});
ipcMain.handle('mia:artifacts:snapshot', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.snapshot(request);
});
ipcMain.handle('mia:artifacts:coverage', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.coverage(request);
});
ipcMain.handle('mia:artifacts:vat-return-coverage', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.vatReturnCoverage(request);
});
ipcMain.handle('mia:artifacts:vat-return-export', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.vatReturnExport(request);
});
ipcMain.handle('mia:artifacts:batch-start', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.startBatch(request);
});
ipcMain.handle('mia:artifacts:batch-status', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.batchStatus(request);
});
ipcMain.handle('mia:artifacts:batch-failures', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.batchFailures(request);
});
ipcMain.handle('mia:artifacts:batch-cancel', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.cancelBatch(request);
});
ipcMain.handle('mia:artifacts:open-directory', async (event, directory) => {
  assertTrustedSender(event);
  if (typeof directory !== 'string' || !path.isAbsolute(directory) || directory.length > 1024) throw new TypeError('invalid_artifact_directory');
  const error = await shell.openPath(path.resolve(directory));
  if (error) throw new Error('artifact_directory_open_failed');
  return true;
});
ipcMain.handle('mia:external:open', async (event, url) => {
  assertTrustedSender(event);
  await shell.openExternal(validateExternalUrl(url));
  return true;
});
ipcMain.handle('mia:preferences:get', (event) => { assertTrustedSender(event); return readPreferences(app.getPath('userData')); });
ipcMain.handle('mia:preferences:set', (event, value) => { assertTrustedSender(event); return writePreferences(app.getPath('userData'), value); });
ipcMain.handle('mia:logs:list', (event) => { assertTrustedSender(event); return readSanitizedLogs(app.getPath('userData')); });
ipcMain.handle('mia:logs:entries', (event) => { assertTrustedSender(event); return readSanitizedLogEntries(app.getPath('userData')); });
ipcMain.handle('mia:logs:clear', (event) => { assertTrustedSender(event); return clearDiagnosticLogs(app.getPath('userData')); });
ipcMain.handle('mia:logs:write', (event, payload) => {
  assertTrustedSender(event);
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new TypeError('invalid_log_payload');
  const level = String(payload.level ?? 'info').toLowerCase();
  if (!['info', 'warn', 'error'].includes(level)) throw new TypeError('invalid_log_level');
  if (typeof payload.event !== 'string' || !/^[a-z0-9_.-]{1,80}$/i.test(payload.event)) throw new TypeError('invalid_log_event');
  rendererLog().write(level, payload.event, payload.fields && typeof payload.fields === 'object' ? payload.fields : {});
  return true;
});
for (const [channel, method] of [['mia:updates:status', 'status'], ['mia:updates:check', 'check'], ['mia:updates:download', 'download'], ['mia:updates:install', 'install'], ['mia:updates:channel', 'setChannel']]) {
  ipcMain.handle(channel, (event, ...args) => { assertTrustedSender(event); return releaseUpdater[method](...args); });
}
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
  ['mia:jobs:resume', 'resume'], ['mia:jobs:resume-all', 'resumeAll'], ['mia:jobs:latest-all', 'latestAll'], ['mia:jobs:sync-states', 'syncStates'], ['mia:jobs:start', 'start'], ['mia:jobs:status', 'status'],
  ['mia:jobs:summary', 'summary'], ['mia:jobs:cancel', 'cancel'], ['mia:jobs:clear', 'clear'],
]) {
  ipcMain.handle(channel, (event, ...args) => {
    assertTrustedSender(event);
    return jobs()[method](...args);
  });
}
for (const [channel, method] of [['mia:results:overview', 'overview'], ['mia:results:details', 'details'], ['mia:results:reconciliation', 'reconciliation'], ['mia:results:facets', 'facets']]) {
  ipcMain.handle(channel, (event, query) => {
    assertTrustedSender(event);
    return results()[method](query);
  });
}

void app.whenReady().then(async () => {
  Menu.setApplicationMenu(null);
  electronLog().info('app_ready', {
    app_version: app.getVersion(),
    electron_version: process.versions.electron,
    node_version: process.versions.node,
    packaged: app.isPackaged,
    platform: process.platform,
    arch: process.arch,
    user_data: app.getPath('userData'),
  });
  const { autoUpdater } = require('electron-updater');
  releaseUpdater = createReleaseUpdater({ isPackaged: app.isPackaged, autoUpdater });
  offlineRuntime = new OfflineRuntimeManager({
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath,
    dataDirectory: path.join(app.getPath('userData'), 'offline-runtime'),
    env: { MIA_SESSION_ENCRYPTION_KEY: runtimeSessionKey(), MIA_SESSION_ENCRYPTION_KEY_ID: 'desktop-dpapi-v1' },
    logger: electronLog(),
    onNotification(method, payload) {
      const channel = method === 'export.progress'
        ? 'mia:artifacts:export-progress'
        : method === 'artifact.progress'
          ? 'mia:artifacts:invoice-progress'
          : null;
      if (!channel) return;
      for (const window of BrowserWindow.getAllWindows()) {
        if (!window.isDestroyed()) window.webContents.send(channel, payload);
      }
    },
  });
  await offlineRuntime.start();
  electronLog().info('offline_runtime_ready');
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
}).catch((error) => {
  electronLog().error('app_start_failed', { name: error?.name, code: error?.code, message: error?.message, stack: error?.stack });
  dialog.showErrorBox('MIA WT', 'Không thể khởi động bộ xử lý dữ liệu cục bộ. Vui lòng mở lại ứng dụng hoặc cài đặt lại.');
  app.quit();
});

app.on('before-quit', (event) => {
  if (!offlineRuntime || runtimeShutdownStarted) return;
  event.preventDefault();
  runtimeShutdownStarted = true;
  electronLog().info('app_shutdown_started');
  void offlineRuntime.stop().finally(() => app.quit());
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
