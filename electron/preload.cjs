const { contextBridge, ipcRenderer } = require('electron');

async function invokeResult(channel, ...args) {
  const result = await ipcRenderer.invoke(channel, ...args);
  if (!result || typeof result !== 'object' || typeof result.ok !== 'boolean') {
    throw new Error('invalid IPC response');
  }
  if (result.ok) return result.data;

  const error = new Error(String(result.error?.message ?? 'MIA API request failed'));
  error.name = 'MiaRuntimeError';
  error.code = String(result.error?.code ?? 'internal_error');
  if (Number.isInteger(result.error?.status)) error.status = result.error.status;
  if (typeof result.error?.requestId === 'string') error.requestId = result.error.requestId;
  throw error;
}

contextBridge.exposeInMainWorld('miaRuntime', Object.freeze({
  platform: process.platform,
  getDeviceIdentity: () => ipcRenderer.invoke('mia:device-identity'),
  signDeviceChallenge: (challenge) =>
    ipcRenderer.invoke('mia:sign-device-challenge', challenge),
  storeLicenseToken: (token) => ipcRenderer.invoke('mia:license-store', token),
  accountConnections: Object.freeze({
    create: (credentials) => invokeResult('mia:account-connections:create', credentials),
    list: () => invokeResult('mia:account-connections:list'),
    get: (connectionId) => invokeResult('mia:account-connections:get', connectionId),
    reconnect: (connectionId, credentials) => (
      invokeResult('mia:account-connections:reconnect', connectionId, credentials)
    ),
    revoke: (connectionId) => invokeResult('mia:account-connections:revoke', connectionId),
  }),
  jobs: Object.freeze({
    resume: () => invokeResult('mia:jobs:resume'),
    resumeAll: () => invokeResult('mia:jobs:resume-all'),
    latestAll: () => invokeResult('mia:jobs:latest-all'),
    start: (intent) => invokeResult('mia:jobs:start', intent),
    status: (jobId) => invokeResult('mia:jobs:status', jobId),
    summary: (jobId) => invokeResult('mia:jobs:summary', jobId),
    cancel: (jobId) => invokeResult('mia:jobs:cancel', jobId),
    clear: () => invokeResult('mia:jobs:clear'),
  }),
  artifacts: Object.freeze({
    selectDirectory: () => ipcRenderer.invoke('mia:artifacts:select-directory'),
    export: (request) => invokeResult('mia:artifacts:export', request),
    list: (request) => invokeResult('mia:artifacts:list', request),
    openDirectory: (directory) => ipcRenderer.invoke('mia:artifacts:open-directory', directory),
    onExportProgress: (listener) => {
      if (typeof listener !== 'function') throw new TypeError('invalid export progress listener');
      const wrapped = (_event, progress) => listener(progress);
      ipcRenderer.on('mia:artifacts:export-progress', wrapped);
      return () => ipcRenderer.removeListener('mia:artifacts:export-progress', wrapped);
    },
    onInvoiceProgress: (listener) => {
      if (typeof listener !== 'function') throw new TypeError('invalid invoice artifact progress listener');
      const wrapped = (_event, progress) => listener(progress);
      ipcRenderer.on('mia:artifacts:invoice-progress', wrapped);
      return () => ipcRenderer.removeListener('mia:artifacts:invoice-progress', wrapped);
    },
  }),
  preferences: Object.freeze({
    get: () => ipcRenderer.invoke('mia:preferences:get'),
    set: (value) => ipcRenderer.invoke('mia:preferences:set', value),
  }),
  logs: Object.freeze({
    list: () => ipcRenderer.invoke('mia:logs:list'),
    write: (level, event, fields = {}) => ipcRenderer.invoke('mia:logs:write', { level, event, fields }),
  }),
  updates: Object.freeze({
    status: () => ipcRenderer.invoke('mia:updates:status'),
    check: () => ipcRenderer.invoke('mia:updates:check'),
    download: () => ipcRenderer.invoke('mia:updates:download'),
    install: () => ipcRenderer.invoke('mia:updates:install'),
    setChannel: (channel) => ipcRenderer.invoke('mia:updates:channel', channel),
  }),
  results: Object.freeze({
    overview: (query) => invokeResult('mia:results:overview', query),
    details: (query) => invokeResult('mia:results:details', query),
  }),
}));
