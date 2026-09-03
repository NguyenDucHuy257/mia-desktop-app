const { contextBridge, ipcRenderer } = require('electron');

async function invokeIpc(channel, ...args) {
  try {
    return await ipcRenderer.invoke(channel, ...args);
  } catch (cause) {
    const message = String(cause?.message || cause || 'MIA IPC request failed');
    if (message.includes('LICENSE_REQUIRED')) {
      const error = new Error('LICENSE_REQUIRED');
      error.name = 'MiaRuntimeError';
      error.code = 'LICENSE_REQUIRED';
      throw error;
    }
    throw cause;
  }
}

async function invokeResult(channel, ...args) {
  const result = await invokeIpc(channel, ...args);
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
  license: Object.freeze({
    status: () => invokeResult('mia:license:status'),
    initialize: () => invokeResult('mia:license:initialize'),
    submitPhone: (phone) => invokeResult('mia:license:submit-phone', phone),
    retry: () => invokeResult('mia:license:retry'),
    details: () => invokeResult('mia:license:details'),
    revealKey: () => invokeResult('mia:license:reveal-key'),
    updatePhone: (phone) => invokeResult('mia:license:update-phone', phone),
  }),
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
    syncStates: (connectionIds, direction, dateFrom, dateTo) => invokeResult('mia:jobs:sync-states', connectionIds, direction, dateFrom, dateTo),
    start: (intent) => invokeResult('mia:jobs:start', intent),
    status: (jobId) => invokeResult('mia:jobs:status', jobId),
    summary: (jobId) => invokeResult('mia:jobs:summary', jobId),
    cancel: (jobId) => invokeResult('mia:jobs:cancel', jobId),
    clear: () => invokeResult('mia:jobs:clear'),
  }),
  artifacts: Object.freeze({
    selectDirectory: () => invokeIpc('mia:artifacts:select-directory'),
    export: (request) => invokeResult('mia:artifacts:export', request),
    cancel: () => invokeResult('mia:artifacts:cancel'),
    targets: (request) => invokeResult('mia:artifacts:targets', request),
    list: (request) => invokeResult('mia:artifacts:list', request),
    coverage: (request) => invokeResult('mia:artifacts:coverage', request),
    vatReturnCoverage: (request) => invokeResult('mia:artifacts:vat-return-coverage', request),
    vatReturnExport: (request) => invokeResult('mia:artifacts:vat-return-export', request),
    snapshot: (request) => invokeResult('mia:artifacts:snapshot', request),
    startBatch: (request) => invokeResult('mia:artifacts:batch-start', request),
    batchStatus: (request) => invokeResult('mia:artifacts:batch-status', request),
    batchFailures: (request) => invokeResult('mia:artifacts:batch-failures', request),
    cancelBatch: () => invokeResult('mia:artifacts:batch-cancel', {}),
    openDirectory: (directory) => invokeIpc('mia:artifacts:open-directory', directory),
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
    get: () => invokeIpc('mia:preferences:get'),
    set: (value) => invokeIpc('mia:preferences:set', value),
  }),
  logs: Object.freeze({
    list: () => invokeIpc('mia:logs:list'),
    entries: () => invokeIpc('mia:logs:entries'),
    clear: () => invokeIpc('mia:logs:clear'),
    write: (level, event, fields = {}) => invokeIpc('mia:logs:write', { level, event, fields }),
  }),
  updates: Object.freeze({
    status: () => invokeIpc('mia:updates:status'),
    check: () => invokeIpc('mia:updates:check'),
    download: () => invokeIpc('mia:updates:download'),
    install: () => invokeIpc('mia:updates:install'),
    setChannel: (channel) => invokeIpc('mia:updates:channel', channel),
  }),
  results: Object.freeze({
    overview: (query) => invokeResult('mia:results:overview', query),
    details: (query) => invokeResult('mia:results:details', query),
    reconciliation: (query) => invokeResult('mia:results:reconciliation', query),
    facets: (query) => invokeResult('mia:results:facets', query),
  }),
  external: Object.freeze({
    open: (url) => invokeIpc('mia:external:open', url),
  }),
}));
