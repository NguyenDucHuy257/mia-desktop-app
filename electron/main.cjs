const { app, BrowserWindow, dialog, ipcMain, Menu, safeStorage, session, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { ensureDeviceIdentity } = require('./device-identity.cjs');
const { collectDeviceEvidence } = require('./license/hardware-profile.cjs');
const { createLicenseApi } = require('./license/license-api.cjs');
const { LicenseManager } = require('./license/license-manager.cjs');
const { createLicenseRequestGuard, isLicenseDataRequest } = require('./license/entitlements.cjs');
const { createProtectedLicenseStore } = require('./license/protected-license-store.cjs');
const { isLicenseAccessGranted, licenseRequiredError } = require('./license/access-control.cjs');
const { GUIDE_VIDEO_EMBED_PREFIX, guideVideoRequestHeaders, isTrustedAppUrl } = require('./security-policy.cjs');
const { createJobLifecycleBroker } = require('./job-lifecycle-broker.cjs');
const { OfflineRuntimeManager } = require('./offline-runtime-manager.cjs');
const { createOfflineAuthManager, normalizePassword } = require('./offline-auth.cjs');
const { createLocalAccountBroker } = require('./local-account-broker.cjs');
const { createResultBroker } = require('./result-broker.cjs');
const { createArtifactBroker } = require('./artifact-file-broker.cjs');
const { clearDiagnosticLogs, readPreferences, readSanitizedLogEntries, readSanitizedLogs, writePreferences } = require('./local-preferences.cjs');
const { createReleaseUpdater } = require('./release-updater.cjs');
const { createDiagnosticLogger } = require('./app-logger.cjs');
const { validateExternalUrl } = require('./external-url-policy.cjs');
const { loadOrCreateRuntimeSessionKey } = require('./runtime-session-key.cjs');
const { PROXY_PROVIDER } = require('./proxy-provider-config.cjs');
const { createProxyImportStore } = require('./proxy-import-store.cjs');

// Keep the established user-data directory after changing the displayed product name.
// This preserves existing accounts, local SQLite data, preferences, and licenses.
const LEGACY_USER_DATA_DIRECTORY = ['MIA', 'WT'].join(' ');
if (app.isPackaged) {
  app.setPath('userData', path.join(app.getPath('appData'), LEGACY_USER_DATA_DIRECTORY));
}

// Only the primary desktop process may own the local runtime and SQLite store.
// A second launch should focus the existing window instead of starting another
// runtime process that would contend for the same local resources.
const hasSingleInstanceLock = app.requestSingleInstanceLock();

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
let licenseSessionActive = false;
let offlineAuthManager;
let offlineAuthSessionActive = false;
let offlineRuntimeStartPromise;
let proxyImportStore;

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
  // Packaged builds are always protected. Development may explicitly disable
  // verification, but a disabled manager remains fail-closed.
  const enabled = app.isPackaged || process.env.MIA_LICENSE_V2_ENABLED !== 'false';
  const protector = secureProtector();
  let api = null;
  if (enabled) {
    const baseUrl = app.isPackaged
      ? 'https://gotax.vn'
      : process.env.MIA_LICENSE_API_URL || process.env.MIA_KEY_SERVER_URL || 'https://gotax.vn';
    api = createLicenseApi({
      baseUrl,
      allowInsecureLocalhost: !app.isPackaged && process.env.MIA_LICENSE_ALLOW_INSECURE_LOCALHOST === 'true',
    });
  }
  licenseManagerInstance = new LicenseManager({
    enabled,
    api,
    currentVersion: app.getVersion(),
    securityDirectory: securityDirectory(),
    store: createProtectedLicenseStore(securityDirectory(), protector),
    ensureIdentity: () => ensureDeviceIdentity(securityDirectory(), protector),
    collectEvidence: () => collectDeviceEvidence(),
    // Source 3.9.0 stores the registration phone relative to its launch folder.
    // Read only known files; never scan disks for customer records.
    legacyPhonePaths: [...new Set([process.cwd(), path.dirname(app.getPath('exe'))])]
      .map((directory) => path.join(directory, '__pycache__', 'sdt.txt')),
    logger: electronLog(),
    requireRecoveryEmail: true,
  });
  return licenseManagerInstance;
}

function offlineAuth() {
  if (!offlineAuthManager) {
    offlineAuthManager = createOfflineAuthManager({
      directory: securityDirectory(),
      protector: secureProtector(),
      logger: electronLog(),
    });
  }
  return offlineAuthManager;
}

function serializeLicenseError(error) {
  const allowed = new Set([
    'invalid_phone', 'license_api_not_configured', 'license_network_error', 'license_timeout',
    'insufficient_hardware', 'hardware_query_failed', 'hardware_query_timeout',
    'device_profile_corrupt', 'license_state_corrupt', 'invalid_email', 'invalid_response',
    'recovery_code_invalid', 'recovery_code_expired', 'recovery_code_locked',
    'recovery_email_not_registered', 'recovery_wait_before_resend', 'recovery_rate_limited',
    'recovery_email_unavailable', 'recovery_not_configured', 'internal_error',
  ]);
  const code = allowed.has(String(error?.code)) ? String(error.code) : 'internal_error';
  electronLog().error('license_ipc_failed', {
    code, error_type: error?.name, message: error?.message,
    status: Number.isInteger(error?.status) ? error.status : null,
    request_id: typeof error?.requestId === 'string' ? error.requestId : null,
    transient: Boolean(error?.transient),
  });
  return { ok: false, error: {
    code, message: code,
    status: Number.isInteger(error?.status) ? error.status : undefined,
    requestId: typeof error?.requestId === 'string' ? error.requestId : undefined,
  } };
}

async function handleLicense(method, ...args) {
  try {
    const verifiesAccess = ['initialize', 'submitPhone', 'retry', 'updatePhone', 'confirmContact'].includes(method);
    if (verifiesAccess) {
      licenseSessionActive = false;
      offlineAuthSessionActive = false;
      offlineAuthManager?.lock();
    }
    const data = await licenses()[method](...args);
    if (verifiesAccess && isLicenseAccessGranted(data)) {
      licenseSessionActive = true;
    }
    return { ok: true, data };
  } catch (error) {
    return serializeLicenseError(error);
  }
}


const OFFLINE_AUTH_MESSAGES = Object.freeze({
  offline_password_invalid: 'Mật khẩu phải có từ 8 đến 128 ký tự.',
  offline_password_confirmation_mismatch: 'Mật khẩu xác nhận không khớp.',
  offline_password_incorrect: 'Mật khẩu không đúng.',
  offline_password_already_configured: 'Máy này đã có mật khẩu đăng nhập.',
  offline_password_not_configured: 'Máy này chưa tạo mật khẩu đăng nhập.',
  offline_password_unchanged: 'Mật khẩu mới phải khác mật khẩu hiện tại.',
  offline_auth_rate_limited: 'Bạn đã nhập sai nhiều lần. Vui lòng chờ rồi thử lại.',
  offline_auth_state_corrupt: 'Dữ liệu mật khẩu trên máy bị lỗi. Vui lòng liên hệ hỗ trợ.',
  offline_auth_required: 'Cần đăng nhập bằng mật khẩu trên máy.',
  recovery_code_invalid: 'Mã xác nhận không đúng.',
  recovery_code_expired: 'Mã xác nhận đã hết hạn.',
  recovery_code_locked: 'Mã xác nhận đã bị khóa do nhập sai quá số lần.',
  recovery_email_not_registered: 'Chưa đăng ký email khôi phục.',
  recovery_wait_before_resend: 'Vui lòng chờ trước khi gửi lại mã.',
  recovery_rate_limited: 'Đã yêu cầu quá nhiều mã. Vui lòng thử lại sau.',
  recovery_email_unavailable: 'Không thể gửi email xác nhận lúc này.',
  recovery_not_configured: 'Tính năng khôi phục chưa được cấu hình trên server.',
});

function serializeOfflineAuthError(error) {
  const code = Object.hasOwn(OFFLINE_AUTH_MESSAGES, error?.code || '') ? error.code : 'internal_error';
  electronLog().warn('offline_auth_request_failed', { code, error_type: error?.name || 'Error' });
  return { ok: false, error: { code, message: OFFLINE_AUTH_MESSAGES[code] || 'Không thể xử lý mật khẩu trên máy.' } };
}

async function handleOfflineAuth(method, ...args) {
  try {
    const data = await offlineAuth()[method](...args);
    if (data?.unlocked) {
      await ensureOfflineRuntimeStarted();
      offlineAuthSessionActive = true;
    }
    return { ok: true, data };
  } catch (error) {
    return serializeOfflineAuthError(error);
  }
}

function runtimeSessionKey() {
  return loadOrCreateRuntimeSessionKey({
    directory: securityDirectory(),
    protector: secureProtector(),
    logger: electronLog(),
  });
}

function productionEntryUrl() {
  return pathToFileURL(path.join(__dirname, '..', 'dist', 'index.html')).toString();
}

function appIconPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'icon.ico')
    : path.join(__dirname, '..', 'icon.ico');
}

function assertTrustedSender(event, { licenseRequired = true, offlineAuthRequired = licenseRequired } = {}) {
  const senderUrl = event.senderFrame?.url ?? event.sender.getURL();
  if (!isTrustedAppUrl(senderUrl, {
    devServerUrl: process.env.VITE_DEV_SERVER_URL,
    productionEntryUrl: productionEntryUrl(),
  })) {
    throw new Error('untrusted IPC sender');
  }
  if (licenseRequired && !licenseSessionActive) throw licenseRequiredError();
  if (offlineAuthRequired && !offlineAuthSessionActive) {
    throw Object.assign(new Error('OFFLINE_AUTH_REQUIRED'), { code: 'OFFLINE_AUTH_REQUIRED' });
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
  ['mia:license:request-contact', 'requestContactVerification'],
  ['mia:license:confirm-contact', 'confirmContact'],
]) {
  ipcMain.handle(channel, (event, ...args) => {
    const preAuthMethod = ['status', 'initialize', 'submitPhone', 'retry', 'requestContactVerification', 'confirmContact'].includes(method);
    assertTrustedSender(event, preAuthMethod
      ? { licenseRequired: false, offlineAuthRequired: false }
      : { licenseRequired: true, offlineAuthRequired: true });
    return handleLicense(method, ...args);
  });
}
for (const [channel, method] of [
  ['mia:offline-auth:status', 'status'],
  ['mia:offline-auth:create', 'create'],
  ['mia:offline-auth:unlock', 'unlock'],
  ['mia:offline-auth:change', 'change'],
]) {
  ipcMain.handle(channel, (event, ...args) => {
    assertTrustedSender(event, { licenseRequired: true, offlineAuthRequired: false });
    return handleOfflineAuth(method, ...args);
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
ipcMain.handle('mia:artifacts:vat-return-issues', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.vatReturnIssues(request);
});
ipcMain.handle('mia:artifacts:vat-return-issue-update', (event, request) => {
  assertTrustedSender(event);
  if (!artifactBroker) artifactBroker = createArtifactBroker(() => offlineRuntime);
  return artifactBroker.vatReturnIssueUpdate(request);
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
ipcMain.handle('mia:logs:exportSupport', async (event) => {
  assertTrustedSender(event, { licenseRequired: false, offlineAuthRequired: false });
  const { buildSupportDiagnostics } = require('./support-diagnostics.cjs');
  const report = await buildSupportDiagnostics(app.getPath('userData'), app.getVersion(), licenses().status());
  const chosen = await dialog.showSaveDialog({
    title: 'Lưu log lỗi để gửi hỗ trợ',
    defaultPath: `MIA_Log_Loi_${new Date().toISOString().replace(/[:.]/g, '-')}.json`,
    filters: [{ name: 'Log chẩn đoán', extensions: ['json'] }],
  });
  if (chosen.canceled || !chosen.filePath) return { saved: false };
  await fs.promises.writeFile(chosen.filePath, JSON.stringify(report, null, 2), { encoding: 'utf8', mode: 0o600 });
  return { saved: true };
});
ipcMain.handle('mia:proxy-provider:open-purchase-page', async (event) => {
  assertTrustedSender(event);
  await shell.openExternal(validateExternalUrl(PROXY_PROVIDER.purchaseUrl));
  return true;
});
function importedProxies() {
  if (!proxyImportStore) proxyImportStore = createProxyImportStore(app.getPath('userData'), safeStorage);
  return proxyImportStore;
}
async function applyImportedProxies() {
  const state = importedProxies().load();
  await ensureOfflineRuntimeStarted();
  await offlineRuntime.invoke('source.proxies.configure', { proxies: state.proxies });
  return { count: state.count, source_name: state.source_name, imported_at: state.imported_at };
}
ipcMain.handle('mia:invoice-proxies:status', async (event) => {
  assertTrustedSender(event);
  return applyImportedProxies();
});
ipcMain.handle('mia:invoice-proxies:import', async (event) => {
  assertTrustedSender(event);
  const owner = BrowserWindow.fromWebContents(event.sender);
  const selection = await dialog.showOpenDialog(owner, {
    title: 'Chọn file Proxy MKVN',
    properties: ['openFile'],
    filters: [{ name: 'Danh sách Proxy', extensions: ['txt'] }],
  });
  if (selection.canceled || !selection.filePaths[0]) return { cancelled: true, count: 0, source_name: '', imported_at: '' };
  const result = importedProxies().importFile(selection.filePaths[0]);
  await applyImportedProxies();
  electronLog().info('invoice_proxy_file_imported', { count: result.count, source_name: result.source_name });
  return { cancelled: false, ...result };
});
ipcMain.handle('mia:logs:exportAccount', async (event, request) => {
  assertTrustedSender(event);
  const { buildAccountDiagnostics } = require('./support-diagnostics.cjs');
  const report = await buildAccountDiagnostics(app.getPath('userData'), app.getVersion(), request);
  const chosen = await dialog.showSaveDialog({
    title: 'Tải mã lỗi tài khoản để gửi kỹ thuật',
    defaultPath: `MIA_Ma_Loi_${report.report_id}.json`,
    filters: [{ name: 'Mã lỗi và nhật ký chẩn đoán', extensions: ['json'] }],
  });
  if (chosen.canceled || !chosen.filePath) return { saved: false };
  await fs.promises.writeFile(chosen.filePath, JSON.stringify(report, null, 2), { encoding: 'utf8', mode: 0o600 });
  return { saved: true };
});
ipcMain.handle('mia:logs:clear', (event) => { assertTrustedSender(event); return clearDiagnosticLogs(app.getPath('userData')); });
ipcMain.handle('mia:logs:write', (event, payload) => {
  // Renderer failures can happen on the license/password gates, before either
  // authenticated session exists. Payload validation and sanitization below
  // keep this pre-auth diagnostic channel write-only and non-sensitive.
  assertTrustedSender(event, { licenseRequired: false, offlineAuthRequired: false });
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
  if (!localAccountBroker) localAccountBroker = createLocalAccountBroker(
    () => offlineRuntime,
    async (username) => {
      // A server-side VIP -> VIP1/TEST policy change must take effect without
      // relying on a desktop restart or a stale encrypted license snapshot.
      const state = await licenses().verifyTaxCode(username);
      if (['mst_not_authorized', 'mst_limit_reached', 'invalid_mst'].includes(state.reason)) {
        throw Object.assign(new Error('MST is not licensed.'), { code: 'license_tax_code_denied' });
      }
      const guard = createLicenseRequestGuard(() => state);
      await guard('source.accounts.create', { username }, async () => {
        throw new Error('unexpected_account_lookup');
      });
    },
  );
  return localAccountBroker;
}
ipcMain.handle('mia:offline-auth:request-recovery', async (event) => {
  assertTrustedSender(event, { licenseRequired: true, offlineAuthRequired: false });
  try { return { ok: true, data: await licenses().requestPasswordReset() }; }
  catch (error) { return serializeOfflineAuthError(error); }
});
ipcMain.handle('mia:offline-auth:recover', async (event, challengeId, code, newPassword, confirmation) => {
  assertTrustedSender(event, { licenseRequired: true, offlineAuthRequired: false });
  try {
    normalizePassword(newPassword);
    if (newPassword !== confirmation) throw Object.assign(new Error('password confirmation mismatch'), { code: 'offline_password_confirmation_mismatch' });
    const verified = await licenses().verifyPasswordReset(challengeId, code);
    if (verified?.verified !== true) throw Object.assign(new Error('recovery code invalid'), { code: 'recovery_code_invalid' });
    const data = await offlineAuth().recover(newPassword, confirmation);
    await ensureOfflineRuntimeStarted();
    offlineAuthSessionActive = true;
    return { ok: true, data };
  } catch (error) { return serializeOfflineAuthError(error); }
});
function results() {
  if (!resultBroker) resultBroker = createResultBroker(() => offlineRuntime);
  return resultBroker;
}

ipcMain.handle('mia:account-connections:create', (event, credentials) => {
  assertTrustedSender(event);
  return localAccounts().create(credentials);
});
ipcMain.handle('mia:account-connections:list', async (event) => {
  assertTrustedSender(event);
  // The first renderer request can arrive while the packaged runtime is still
  // completing its startup handshake. Always wait for the same ready promise
  // used by offline unlock before reading the persistent account registry.
  await ensureOfflineRuntimeStarted();
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
for (const [channel, method] of [['mia:results:materialStart', 'materialStart'], ['mia:results:materialStatus', 'materialStatus'], ['mia:results:overview', 'overview'], ['mia:results:details', 'details'], ['mia:results:reconciliation', 'reconciliation'], ['mia:results:facets', 'facets']]) {
  ipcMain.handle(channel, (event, query) => {
    assertTrustedSender(event);
    return results()[method](query);
  });
}

function ensureOfflineRuntimeStarted() {
  if (!offlineRuntime) {
    offlineRuntime = new OfflineRuntimeManager({
      authorizeRequest: async (method, params, call) => {
        // Refresh server policy before every protected data operation. Account
        // create/reconnect already refresh at the outer broker boundary, then
        // pass through this guard as a second independent MST check.
        if (isLicenseDataRequest(method)
            && !['source.accounts.create', 'source.accounts.reconnect'].includes(method)) {
          await licenses().initialize();
        }
        return createLicenseRequestGuard(() => licenses().status())(method, params, call);
      },
      isPackaged: app.isPackaged,
      resourcesPath: process.resourcesPath,
      dataDirectory: path.join(app.getPath('userData'), 'offline-runtime'),
      env: { MIA_SESSION_ENCRYPTION_KEY: runtimeSessionKey(), MIA_SESSION_ENCRYPTION_KEY_ID: 'desktop-dpapi-v1' },
      logger: electronLog(),
      afterStart: async (client) => {
        const state = importedProxies().load();
        await client.call('source.proxies.configure', { proxies: state.proxies });
        electronLog().info('invoice_proxy_pool_restored', { count: state.count });
      },
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
  }
  if (!offlineRuntimeStartPromise) {
    offlineRuntimeStartPromise = offlineRuntime.start()
      .then((health) => {
        electronLog().info('offline_runtime_ready');
        return health;
      })
      .catch((error) => {
        offlineRuntimeStartPromise = undefined;
        throw error;
      });
  }
  return offlineRuntimeStartPromise;
}

if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    const window = BrowserWindow.getAllWindows().find((candidate) => !candidate.isDestroyed());
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  });

  void app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    // YouTube rejects embedded playback from Electron's file:// origin with
    // player error 153 unless the initial embed request identifies the app.
    // Scope the override to the single privacy-enhanced embed endpoint only.
    session.defaultSession.webRequest.onBeforeSendHeaders(
      { urls: [`${GUIDE_VIDEO_EMBED_PREFIX}*`] },
      (details, callback) => callback({
        requestHeaders: guideVideoRequestHeaders(details.url, details.requestHeaders),
      }),
    );
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

    // The renderer initially contains only the fail-closed activation gate.
    // Runtime and business IPC remain unavailable until KEYV2 verification
    // succeeds and the local offline password has been unlocked.
    createWindow();
    await handleLicense('initialize');
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  }).catch((error) => {
    electronLog().error('app_start_failed', { name: error?.name, code: error?.code, message: error?.message, stack: error?.stack });
    dialog.showErrorBox('MIA TOOL 2026', 'Không thể khởi động bộ xử lý dữ liệu cục bộ. Vui lòng mở lại ứng dụng hoặc cài đặt lại.');
    app.quit();
  });
}

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
