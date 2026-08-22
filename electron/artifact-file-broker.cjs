'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { runBrokerCommand } = require('./account-connection-broker.cjs');

const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
const EXTENSIONS = new Set(['.xml', '.html', '.pdf', '.xlsx']);
const KINDS = new Set(['xml', 'html', 'pdf', 'excel']);
const RESULT_SCOPES = new Set(['overview', 'details']);
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
  const requested = resolveInside(directory, filename);
  const extension = path.extname(requested);
  const stem = requested.slice(0, -extension.length);
  let target = requested;
  for (let copy = 1; copy <= 999; copy += 1) {
    try { await fs.access(target); target = `${stem} (${copy})${extension}`; } catch { break; }
  }
  const temporary = path.join(path.dirname(target), `.${path.basename(target)}.${crypto.randomUUID()}.tmp`);
  try {
    await fs.writeFile(temporary, content, { flag: 'wx' });
    await fs.rename(temporary, target);
    return target;
  } catch (error) {
    await fs.rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}

function validateExportRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_artifact_request');
  const allowed = new Set(['destination', 'connection_ids', 'kinds', 'result_scopes', 'date_from', 'date_to', 'direction', 'query_type', 'search']);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new TypeError('invalid_artifact_request');
  if (typeof value.destination !== 'string' || !path.isAbsolute(value.destination) || value.destination.length > 1024) throw new TypeError('invalid_artifact_directory');
  if (!Array.isArray(value.connection_ids) || value.connection_ids.length < 1 || value.connection_ids.length > 50 || new Set(value.connection_ids).size !== value.connection_ids.length || value.connection_ids.some((id) => typeof id !== 'string' || !CONNECTION_ID.test(id))) throw new TypeError('invalid_artifact_accounts');
  if (!Array.isArray(value.kinds) || value.kinds.length < 1 || value.kinds.length > 4 || new Set(value.kinds).size !== value.kinds.length || value.kinds.some((kind) => !KINDS.has(kind))) throw new TypeError('invalid_artifact_kind');
  const base = { destination: path.resolve(value.destination), connection_ids: [...value.connection_ids], kinds: [...value.kinds] };
  if (value.result_scopes === undefined) {
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

  if (!Array.isArray(value.result_scopes) || value.result_scopes.length < 1 || value.result_scopes.length > 2 || new Set(value.result_scopes).size !== value.result_scopes.length || value.result_scopes.some((scope) => !RESULT_SCOPES.has(scope))) throw new TypeError('invalid_result_export_scope');
  if (value.connection_ids.length !== 1 || value.kinds.length !== 1 || value.kinds[0] !== 'excel') throw new TypeError('invalid_result_export_request');
  if (typeof value.date_from !== 'string' || !DATE_PATTERN.test(value.date_from) || typeof value.date_to !== 'string' || !DATE_PATTERN.test(value.date_to) || value.date_from > value.date_to) throw new TypeError('invalid_result_export_range');
  const direction = value.direction ?? null;
  if (direction !== null && !['purchase', 'sold'].includes(direction)) throw new TypeError('invalid_result_export_direction');
  const queryType = value.query_type ?? null;
  if (queryType !== null && !['query', 'sco-query'].includes(queryType)) throw new TypeError('invalid_result_export_query_type');
  const search = value.search ?? '';
  if (typeof search !== 'string' || search.length > 200) throw new TypeError('invalid_result_export_search');
  return {
    ...base, result_scopes: [...value.result_scopes], date_from: value.date_from,
    date_to: value.date_to, direction, query_type: queryType, search: search.trim(),
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

async function invokeArtifactExport(getRuntime, value) {
  const result = await getRuntime().invoke(
    'artifacts.export',
    validateExportRequest(value),
    { timeoutMs: 30 * 60 * 1000 },
  );
  if (result && typeof result === 'object' && typeof result.error_code === 'string' && result.error_code) {
    throw new Error(result.error_code);
  }
  return result;
}

function waitForTaskPoll(delayMs = 100) {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

function createArtifactBroker(getRuntime) {
  let activeTaskId = null;
  return Object.freeze({
    export: (value) => runBrokerCommand(async () => {
      const request = validateExportRequest(value);
      const kinds = new Set(request.kinds);
      if (request.result_scopes || ![...kinds].every((kind) => kind === 'xml' || kind === 'html')) {
        return invokeArtifactExport(getRuntime, request);
      }
      const runtime = getRuntime();
      const started = await runtime.invoke('artifacts.export.start', request);
      activeTaskId = started.task_id;
      try {
        while (true) {
          const task = await runtime.invoke('artifacts.export.status', { task_id: activeTaskId });
          if (task.status === 'completed') return task.result;
          if (task.status === 'cancelled') throw new Error('artifact_cancelled');
          if (task.status === 'failed') throw new Error(task.error || 'artifact_write_failed');
          await waitForTaskPoll();
        }
      } finally {
        activeTaskId = null;
      }
    }),
    cancel: () => runBrokerCommand(async () => {
      if (!activeTaskId) return { cancelled: false };
      const task = await getRuntime().invoke('artifacts.export.cancel', { task_id: activeTaskId });
      return { cancelled: task.status === 'cancelling' || task.status === 'cancelled' };
    }),
    list: (value) => runBrokerCommand(() => getRuntime().invoke('artifacts.list', validateListRequest(value))),
  });
}

module.exports = { atomicWrite, createArtifactBroker, resolveInside, validateArtifactName, validateExportRequest, validateListRequest };
