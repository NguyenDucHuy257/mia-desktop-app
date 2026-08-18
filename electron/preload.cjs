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
    start: (intent) => invokeResult('mia:jobs:start', intent),
    status: (jobId) => invokeResult('mia:jobs:status', jobId),
    summary: (jobId) => invokeResult('mia:jobs:summary', jobId),
    cancel: (jobId) => invokeResult('mia:jobs:cancel', jobId),
    clear: () => invokeResult('mia:jobs:clear'),
  }),
  results: Object.freeze({
    overview: (query) => invokeResult('mia:results:overview', query),
    details: (query) => invokeResult('mia:results:details', query),
  }),
}));
