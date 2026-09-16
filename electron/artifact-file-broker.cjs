'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { runBrokerCommand } = require('./account-connection-broker.cjs');
const { validateColumnFilters, validateExclusion, validateSort } = require('./result-broker.cjs');

const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
const EXTENSIONS = new Set(['.xml', '.html', '.pdf', '.xlsx']);
const KINDS = new Set(['xml', 'html', 'pdf', 'excel']);
const RESULT_SCOPES = new Set(['overview', 'details', 'reconciliation']);
const CONNECTION_ID = /^[A-Za-z0-9_-]{1,160}$/;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function validateArtifactName(value) {
  if (typeof value !== 'string' || value.length < 1 || value.length > 180) throw new TypeError('invalid_artifact_name');
  if (value !== path.basename(value) || value.includes('..') || /[<>:"/\\|?*\x00-\x1f]/.test(value) || RESERVED.test(value)) throw new TypeError('invalid_artifact_name');
  if (!EXTENSIONS.has(path.extname(value).toLowerCase())) throw new TypeError('invalid_artifact_extension');
  return value;
}

function resolveInside(directory, filename) {
  if (typeof directory !== 'string' || !path.isAbsolute(directory)) throw new TypeError('invalid_artifact_directory');
  const root = path.resolve(directory);
  const target = path.resolve(root, validateArtifactName(filename));
  if (path.dirname(target).toLowerCase() !== root.toLowerCase()) throw new TypeError('artifact_path_escape');
  return target;
}

async function atomicWrite(directory, filename, content) {
  if (!Buffer.isBuffer(content)) throw new TypeError('invalid_artifact_content');
  await fs.mkdir(directory, { recursive: true });
  const target = resolveInside(directory, filename);
  const temporary = path.join(path.dirname(target), `.${path.basename(target)}.${crypto.randomUUID()}.tmp`);
  let handle;
  try {
    handle = await fs.open(temporary, 'wx');
    await handle.writeFile(content);
    await handle.sync();
    await handle.close();
    handle = undefined;
    await fs.rename(temporary, target);
    return target;
  } catch (error) {
    await handle?.close().catch(() => undefined);
    await fs.rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}

function validateExportRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_artifact_request');
  const allowed = new Set(['destination', 'connection_ids', 'kinds', 'result_scopes', 'date_from', 'date_to', 'direction', 'query_type', 'query_types', 'search', 'result_filters', 'exclusion']);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new TypeError('invalid_artifact_request');
  if (typeof value.destination !== 'string' || !path.isAbsolute(value.destination) || value.destination.length > 1024) throw new TypeError('invalid_artifact_directory');
  if (!Array.isArray(value.connection_ids) || value.connection_ids.length < 1 || value.connection_ids.length > 50 || new Set(value.connection_ids).size !== value.connection_ids.length || value.connection_ids.some((id) => typeof id !== 'string' || !CONNECTION_ID.test(id))) throw new TypeError('invalid_artifact_accounts');
  if (!Array.isArray(value.kinds) || value.kinds.length < 1 || value.kinds.length > 4 || new Set(value.kinds).size !== value.kinds.length || value.kinds.some((kind) => !KINDS.has(kind))) throw new TypeError('invalid_artifact_kind');
  const base = { destination: path.resolve(value.destination), connection_ids: [...value.connection_ids], kinds: [...value.kinds] };
  if (value.result_scopes === undefined) {
    if (value.result_filters !== undefined || value.exclusion !== undefined || value.query_types !== undefined) throw new TypeError('invalid_artifact_request');
    if (value.date_from === undefined && value.date_to === undefined && value.direction === undefined && value.query_type === undefined && value.search === undefined) return base;
    const direction = value.direction ?? null;
    const queryType = value.query_type ?? null;
    const search = value.search ?? '';
    if (direction !== null && !['purchase', 'sold'].includes(direction)) throw new TypeError('invalid_artifact_direction');
    if (queryType !== null && !['query', 'sco-query'].includes(queryType)) throw new TypeError('invalid_artifact_query_type');
    if (typeof search !== 'string' || search.length > 200) throw new TypeError('invalid_artifact_search');
    if ((value.date_from !== undefined && !DATE_PATTERN.test(value.date_from)) || (value.date_to !== undefined && !DATE_PATTERN.test(value.date_to)) || value.date_from && value.date_to && value.date_from > value.date_to) throw new TypeError('invalid_artifact_range');
    return { ...base, direction, query_type: queryType, search: search.trim(), date_from: value.date_from ?? null, date_to: value.date_to ?? null };
  }

  if (!Array.isArray(value.result_scopes) || value.result_scopes.length < 1 || value.result_scopes.length > 3 || new Set(value.result_scopes).size !== value.result_scopes.length || value.result_scopes.some((scope) => !RESULT_SCOPES.has(scope))) throw new TypeError('invalid_result_export_scope');
  if (value.connection_ids.length !== 1 || value.kinds.length !== 1 || value.kinds[0] !== 'excel') throw new TypeError('invalid_result_export_request');
  if (typeof value.date_from !== 'string' || !DATE_PATTERN.test(value.date_from) || typeof value.date_to !== 'string' || !DATE_PATTERN.test(value.date_to) || value.date_from > value.date_to) throw new TypeError('invalid_result_export_range');
  const direction = value.direction ?? null;
  if (direction !== null && !['purchase', 'sold'].includes(direction)) throw new TypeError('invalid_result_export_direction');
  const queryType = value.query_type ?? null;
  const queryTypes = value.query_types ?? null;
  if (queryType !== null && !['query', 'sco-query'].includes(queryType)) throw new TypeError('invalid_result_export_query_type');
  if (queryTypes !== null && (!Array.isArray(queryTypes) || queryTypes.length < 1 || queryTypes.length > 2 || new Set(queryTypes).size !== queryTypes.length || queryTypes.some((item) => !['query', 'sco-query'].includes(item)))) throw new TypeError('invalid_result_export_query_type');
  if (queryType !== null && queryTypes !== null) throw new TypeError('invalid_result_export_query_type');
  const search = value.search ?? '';
  if (typeof search !== 'string' || search.length > 200) throw new TypeError('invalid_result_export_search');
  const resultFilters = value.result_filters ?? {};
  if (!resultFilters || typeof resultFilters !== 'object' || Array.isArray(resultFilters) || Object.keys(resultFilters).some((scope) => !RESULT_SCOPES.has(scope))) throw new TypeError('invalid_result_export_filters');
  const normalizedFilters = {};
  for (const [scope, filter] of Object.entries(resultFilters)) {
    if (!filter || typeof filter !== 'object' || Array.isArray(filter) || Object.keys(filter).some((key) => !['search', 'column_filters', 'sort'].includes(key))) throw new TypeError('invalid_result_export_filters');
    if (filter.search !== undefined && (typeof filter.search !== 'string' || filter.search.length > 200)) throw new TypeError('invalid_result_export_filters');
    normalizedFilters[scope] = { search: String(filter.search ?? '').trim(), column_filters: validateColumnFilters(filter.column_filters), sort: validateSort(filter.sort) };
  }
  return {
    ...base, result_scopes: [...value.result_scopes], date_from: value.date_from,
    date_to: value.date_to, direction, query_type: queryType,
    query_types: queryTypes === null ? null : [...queryTypes], search: search.trim(),
    result_filters: normalizedFilters, exclusion: validateExclusion(value.exclusion),
  };
}

function validateListRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_artifact_query');
  if (Object.keys(value).some((key) => !['connection_ids', 'kind', 'direction', 'query_type', 'search', 'cursor', 'limit', 'date_from', 'date_to'].includes(key))) throw new TypeError('invalid_artifact_query');
  const base = validateExportRequest({ destination: path.resolve('.'), connection_ids: value.connection_ids, kinds: [value.kind] });
  if (!['xml', 'html', 'pdf'].includes(value.kind) || ![undefined, null, 'purchase', 'sold'].includes(value.direction)) throw new TypeError('invalid_artifact_query');
  if (![undefined, null, 'query', 'sco-query'].includes(value.query_type)) throw new TypeError('invalid_artifact_query');
  if (value.search !== undefined && (typeof value.search !== 'string' || value.search.length > 200)) throw new TypeError('invalid_artifact_query');
  if (value.cursor !== undefined && value.cursor !== null && (typeof value.cursor !== 'string' || value.cursor.length > 64)) throw new TypeError('invalid_artifact_query');
  const limit = value.limit ?? 50;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw new TypeError('invalid_artifact_query');
  if ((value.date_from !== undefined && !DATE_PATTERN.test(value.date_from)) || (value.date_to !== undefined && !DATE_PATTERN.test(value.date_to)) || value.date_from && value.date_to && value.date_from > value.date_to) throw new TypeError('invalid_artifact_query');
  return { connection_ids: base.connection_ids, kind: value.kind, direction: value.direction ?? null, query_type: value.query_type ?? null, search: value.search ?? '', cursor: value.cursor ?? null, limit, date_from: value.date_from ?? null, date_to: value.date_to ?? null };
}

function validateArtifactSnapshotRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_artifact_snapshot');
  if (Object.keys(value).some((key) => !['connection_ids', 'directions', 'date_from', 'date_to'].includes(key))) throw new TypeError('invalid_artifact_snapshot');
  const base = validateExportRequest({ destination: path.resolve('.'), connection_ids: value.connection_ids, kinds: ['xml'] });
  if (!Array.isArray(value.directions) || value.directions.length < 1 || value.directions.length > 2 || new Set(value.directions).size !== value.directions.length || value.directions.some((direction) => !['purchase', 'sold'].includes(direction))) throw new TypeError('invalid_artifact_direction');
  if (typeof value.date_from !== 'string' || !DATE_PATTERN.test(value.date_from) || typeof value.date_to !== 'string' || !DATE_PATTERN.test(value.date_to) || value.date_from > value.date_to) throw new TypeError('invalid_artifact_range');
  return { connection_ids: base.connection_ids, directions: [...value.directions], date_from: value.date_from, date_to: value.date_to };
}

function validateArtifactBatchRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_artifact_request');
  if (Object.keys(value).some((key) => !['destination', 'connection_ids', 'directions', 'kinds', 'date_from', 'date_to', 'pdf_concurrency'].includes(key))) throw new TypeError('invalid_artifact_request');
  const snapshot = validateArtifactSnapshotRequest({ connection_ids: value.connection_ids, directions: value.directions, date_from: value.date_from, date_to: value.date_to });
  if (typeof value.destination !== 'string' || !path.isAbsolute(value.destination) || value.destination.length > 1024) throw new TypeError('invalid_artifact_directory');
  if (!Array.isArray(value.kinds) || value.kinds.length < 1 || value.kinds.length > 3 || new Set(value.kinds).size !== value.kinds.length || value.kinds.some((kind) => !['xml', 'html', 'pdf'].includes(kind))) throw new TypeError('invalid_artifact_kind');
  if (!Number.isInteger(value.pdf_concurrency) || value.pdf_concurrency < 1 || value.pdf_concurrency > 100) throw new TypeError('invalid_pdf_concurrency');
  return { ...snapshot, destination: path.resolve(value.destination), kinds: [...value.kinds], pdf_concurrency: value.pdf_concurrency };
}

function validateVatReturnCoverageRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_vat_return_coverage');
  if (Object.keys(value).some((key) => !['connection_ids', 'date_from', 'date_to'].includes(key))) throw new TypeError('invalid_vat_return_coverage');
  const snapshot = validateArtifactSnapshotRequest({ ...value, directions: ['purchase', 'sold'] });
  return { connection_ids: snapshot.connection_ids, date_from: snapshot.date_from, date_to: snapshot.date_to };
}

function validateVatReturnExportRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some((key) => !['destination', 'connection_ids', 'date_from', 'date_to', 'allow_incomplete'].includes(key)) || (value.allow_incomplete !== undefined && typeof value.allow_incomplete !== 'boolean')) throw new TypeError('invalid_vat_return_export');
  const coverage = validateVatReturnCoverageRequest({ connection_ids: value.connection_ids, date_from: value.date_from, date_to: value.date_to });
  if (coverage.connection_ids.length !== 1 || typeof value.destination !== 'string' || !path.isAbsolute(value.destination) || value.destination.length > 1024) throw new TypeError('invalid_vat_return_export');
  return { ...coverage, destination: path.resolve(value.destination), ...(value.allow_incomplete === true ? { allow_incomplete: true } : {}) };
}

function validateVatReturnIssuesRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some((key) => !['connection_id', 'date_from', 'date_to'].includes(key))
      || typeof value.connection_id !== 'string' || !value.connection_id.trim()) throw new TypeError('invalid_vat_return_issues');
  const range = validateVatReturnCoverageRequest({ connection_ids: [value.connection_id], date_from: value.date_from, date_to: value.date_to });
  return { connection_id: range.connection_ids[0], date_from: range.date_from, date_to: range.date_to };
}

function validateVatReturnIssueUpdate(value) {
  const allowed = { overview: new Set(['tgtcthue', 'tgtthue']), detail: new Set(['ten', 'tsuat', 'thtien', 'tthue']) };
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).some((key) => !['connection_id', 'source', 'record_id', 'values'].includes(key))
      || typeof value.connection_id !== 'string' || !value.connection_id.trim()
      || !allowed[value.source] || !Number.isInteger(value.record_id) || value.record_id <= 0
      || !value.values || typeof value.values !== 'object' || Array.isArray(value.values)) throw new TypeError('invalid_vat_return_issue_update');
  const entries = Object.entries(value.values);
  if (!entries.length || entries.some(([key, item]) => !allowed[value.source].has(key) || typeof item !== 'string' || item.length > 500)) throw new TypeError('invalid_vat_return_issue_update');
  return { connection_id: value.connection_id.trim(), source: value.source, record_id: value.record_id, values: Object.fromEntries(entries) };
}

function checkedExportResult(result) {
  if (result && typeof result === 'object' && typeof result.error_code === 'string' && result.error_code) {
    const error = new Error(result.error_code);
    error.code = result.error_code;
    throw error;
  }
  return result;
}

async function invokeArtifactExport(getRuntime, value) {
  const result = await getRuntime().invoke(
    'artifacts.export',
    validateExportRequest(value),
    { timeoutMs: 30 * 60 * 1000 },
  );
  return checkedExportResult(result);
}

function waitForTaskPoll(delayMs = 500) {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

async function pollBackgroundExport(runtime, request, onStarted = () => {}) {
  const started = await runtime.invoke(
    'artifacts.export.start', request, { timeoutMs: 30_000 },
  );
  const taskId = started.task_id;
  onStarted(taskId);
  while (true) {
    let task;
    try {
      task = await runtime.invoke(
        'artifacts.export.status', { task_id: taskId }, { timeoutMs: 30_000 },
      );
    } catch (error) {
      // The worker owns the task. A temporarily busy local transport must not
      // report failure while Python is still writing the workbook.
      if (error?.code !== 'runtime_timeout') throw error;
      await waitForTaskPoll(250);
      continue;
    }
    if (task.status === 'completed') return { taskId, result: checkedExportResult(task.result) };
    if (task.status === 'cancelled') throw new Error('artifact_cancelled');
    if (task.status === 'failed') throw new Error(task.error || 'artifact_write_failed');
    await waitForTaskPoll();
  }
}

function createArtifactBroker(getRuntime) {
  let activeTaskId = null;
  let latestTaskId = null;
  const assertIdle = () => {
    if (activeTaskId) throw new Error('artifact_task_active');
  };
  return Object.freeze({
    coverage: (value) => runBrokerCommand(() => getRuntime().invoke('artifacts.coverage', validateArtifactSnapshotRequest(value))),
    vatReturnCoverage: (value) => runBrokerCommand(() => getRuntime().invoke(
      'artifacts.vat_return.coverage', validateVatReturnCoverageRequest(value),
      { timeoutMs: 30_000 },
    )),
    vatReturnIssues: (value) => runBrokerCommand(() => getRuntime().invoke(
      'artifacts.vat_return.issues', validateVatReturnIssuesRequest(value),
      { timeoutMs: 30_000 },
    )),
    vatReturnIssueUpdate: (value) => runBrokerCommand(() => getRuntime().invoke(
      'artifacts.vat_return.issue_update', validateVatReturnIssueUpdate(value),
      { timeoutMs: 30_000 },
    )),
    vatReturnExport: (value) => runBrokerCommand(async () => {
      const request = validateVatReturnExportRequest(value);
      assertIdle();
      activeTaskId = 'vat-return-starting';
      try {
        const runtime = getRuntime();
        const task = await pollBackgroundExport(runtime, {
          ...request, kinds: ['excel'], vat_return: true,
        }, (taskId) => { activeTaskId = taskId; });
        return task.result;
      } finally {
        activeTaskId = null;
      }
    }),
    snapshot: (value) => runBrokerCommand(() => getRuntime().invoke('artifacts.snapshot', validateArtifactSnapshotRequest(value))),
    startBatch: (value) => runBrokerCommand(async () => {
      assertIdle();
      activeTaskId = 'artifact-batch-starting';
      try {
        const started = await getRuntime().invoke('artifacts.batch.start', validateArtifactBatchRequest(value));
        activeTaskId = started.task_id;
        latestTaskId = started.task_id;
        return started;
      } catch (error) {
        activeTaskId = null;
        throw error;
      }
    }),
    batchStatus: (value) => runBrokerCommand(async () => {
      if (!value || typeof value !== 'object' || typeof value.task_id !== 'string' || value.task_id !== activeTaskId) throw new TypeError('invalid_artifact_task');
      const status = await getRuntime().invoke('artifacts.batch.status', { task_id: value.task_id });
      if (['completed', 'failed', 'stopped'].includes(status?.status)) activeTaskId = null;
      return status;
    }),
    batchFailures: (value) => runBrokerCommand(async () => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_artifact_failure_request');
      if (typeof value.task_id !== 'string' || value.task_id !== latestTaskId) throw new TypeError('invalid_artifact_task');
      if (typeof value.connection_id !== 'string' || !CONNECTION_ID.test(value.connection_id)) throw new TypeError('invalid_artifact_account');
      const offset = value.offset ?? 0;
      const limit = value.limit ?? 50;
      if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 10000) throw new TypeError('invalid_artifact_failure_page');
      return getRuntime().invoke('artifacts.batch.failures', {
        task_id: value.task_id, connection_id: value.connection_id, offset, limit,
      });
    }),
    cancelBatch: (value = {}) => runBrokerCommand(() => {
      if (!activeTaskId) return { cancelled: false };
      if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length) throw new TypeError('invalid_artifact_cancel_request');
      return getRuntime().invoke('artifacts.batch.cancel', { task_id: activeTaskId, kind: null });
    }),
    export: (value) => runBrokerCommand(async () => {
      const request = validateExportRequest(value);
      assertIdle();
      activeTaskId = 'result-export-starting';
      const kinds = new Set(request.kinds);
      if (!request.result_scopes && ![...kinds].every((kind) => kind === 'xml' || kind === 'html')) {
        try {
          return await invokeArtifactExport(getRuntime, request);
        } finally {
          activeTaskId = null;
        }
      }
      const runtime = getRuntime();
      // Starting is asynchronous in the Python runtime. Allow enough time for
      // temporary scheduling pressure without losing the task id and falsely
      // reporting failure while the export continues in the background.
      try {
        const task = await pollBackgroundExport(
          runtime, request, (taskId) => { activeTaskId = taskId; },
        );
        return task.result;
      } finally {
        activeTaskId = null;
      }
    }),
    cancel: () => runBrokerCommand(async () => {
      if (!activeTaskId) return { cancelled: false };
      const task = await getRuntime().invoke('artifacts.export.cancel', { task_id: activeTaskId });
      return { cancelled: task.status === 'cancelling' || task.status === 'cancelled' };
    }),
    targets: (value) => runBrokerCommand(() => {
      const request = validateExportRequest(value);
      if (!request.kinds.every((kind) => kind === 'xml' || kind === 'html')) throw new TypeError('invalid_artifact_kind');
      return getRuntime().invoke('artifacts.targets', request);
    }),
    list: (value) => runBrokerCommand(() => getRuntime().invoke('artifacts.list', validateListRequest(value))),
  });
}

module.exports = { atomicWrite, createArtifactBroker, resolveInside, validateArtifactName, validateExportRequest, validateListRequest, validateArtifactSnapshotRequest, validateArtifactBatchRequest, validateVatReturnCoverageRequest, validateVatReturnExportRequest, validateVatReturnIssuesRequest, validateVatReturnIssueUpdate };
