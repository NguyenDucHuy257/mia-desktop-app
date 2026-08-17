'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { MiaApiConfigurationError, MiaApiError } = require('./mia-api-client.cjs');

const JOB_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;
const CONNECTION_ID_PATTERN = /^conn_[A-Za-z0-9._:-]{1,123}$/;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const DIRECTIONS = new Set(['purchase', 'sold']);
const QUERY_TYPES = new Set(['query', 'sco-query']);
const JOB_STATUSES = new Set(['queued', 'waiting_account', 'running', 'cancelling', 'completed', 'completed_with_warning', 'failed', 'cancelled', 'abandoned']);

class JobInputError extends Error {
  constructor(code = 'invalid_job_input') {
    super('Job request is invalid.');
    this.name = 'JobInputError';
    this.code = code;
    this.status = 400;
  }
}

function uniqueEnum(value, allowed, max) {
  if (!Array.isArray(value) || value.length < 1 || value.length > max) throw new JobInputError();
  if (new Set(value).size !== value.length || value.some((item) => !allowed.has(item))) throw new JobInputError();
  return [...value];
}

function validateIntent(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new JobInputError();
  const allowed = new Set([
    'connection_id', 'date_from', 'date_to', 'directions', 'query_types', 'force_refresh',
    'refresh_latest_month', 'result_scope', 'include_xml', 'include_mvt',
  ]);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new JobInputError();
  if (!CONNECTION_ID_PATTERN.test(value.connection_id)) throw new JobInputError();
  if (!DATE_PATTERN.test(value.date_from) || !DATE_PATTERN.test(value.date_to) || value.date_from > value.date_to) {
    throw new JobInputError();
  }
  const resultScope = value.result_scope ?? 'detail';
  if (!['overview', 'detail'].includes(resultScope)) throw new JobInputError();
  for (const key of ['force_refresh', 'refresh_latest_month', 'include_xml', 'include_mvt']) {
    if (value[key] !== undefined && typeof value[key] !== 'boolean') throw new JobInputError();
  }
  if (resultScope === 'overview' && (value.include_xml || value.include_mvt)) throw new JobInputError('invalid_job_options');
  return {
    connection_id: value.connection_id,
    date_from: value.date_from,
    date_to: value.date_to,
    directions: uniqueEnum(value.directions, DIRECTIONS, 2),
    query_types: uniqueEnum(value.query_types, QUERY_TYPES, 2),
    force_refresh: value.force_refresh ?? false,
    refresh_latest_month: value.refresh_latest_month ?? false,
    result_scope: resultScope,
    include_xml: value.include_xml ?? false,
    include_mvt: value.include_mvt ?? false,
  };
}

function validateJobId(value) {
  if (typeof value !== 'string' || !JOB_ID_PATTERN.test(value)) throw new JobInputError('invalid_job_id');
  return value;
}

function sanitizeAccepted(value) {
  if (!value || typeof value !== 'object') throw new Error('invalid API response');
  return {
    job_id: validateJobId(value.job_id),
    status: String(value.status),
    current_stage: typeof value.current_stage === 'string' ? value.current_stage : null,
    worker_slot_id: typeof value.worker_slot_id === 'string' ? value.worker_slot_id : null,
  };
}

function percent(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('invalid API response');
  return Math.max(0, Math.min(100, value));
}

function sanitizeStatus(value) {
  if (!value || typeof value !== 'object' || !JOB_STATUSES.has(value.status)) throw new Error('invalid API response');
  const month = value.current_month;
  return {
    job_id: validateJobId(value.job_id), status: value.status,
    stage: typeof value.stage === 'string' ? value.stage : null,
    overall_percent: percent(value.overall_percent),
    current_month: month === null ? null : {
      key: String(month.key), index: Number(month.index), total: Number(month.total),
      processed: Number(month.processed), planned: Number(month.planned), percent: percent(month.percent),
    },
    updated_at: String(value.updated_at),
    error: value.error && typeof value.error === 'object' ? {
      code: String(value.error.code), message: String(value.error.message), retryable: Boolean(value.error.retryable),
    } : null,
  };
}

function sanitizeSummary(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.stages)) throw new Error('invalid API response');
  return {
    job_id: validateJobId(value.job_id), status: String(value.status), warning_count: Number(value.warning_count),
    stages: value.stages.map((stage) => ({ stage: String(stage.stage), status: String(stage.status), progress_percent: percent(stage.progress_percent) })),
    coverage_plan: value.coverage_plan && typeof value.coverage_plan === 'object' ? value.coverage_plan : {},
    work: value.work && typeof value.work === 'object' ? value.work : {},
    post_processing: value.post_processing && typeof value.post_processing === 'object' ? value.post_processing : {},
  };
}

function sanitizePublicValue(value, depth = 0) {
  if (depth > 8) throw new Error('invalid API response');
  if (value === null || ['string', 'number', 'boolean'].includes(typeof value)) return value;
  if (Array.isArray(value)) return value.map((item) => sanitizePublicValue(item, depth + 1));
  if (!value || typeof value !== 'object') throw new Error('invalid API response');
  const blocked = /password|token|authorization|proxy|session|(?:^|_)path$/i;
  return Object.fromEntries(Object.entries(value)
    .filter(([key]) => !blocked.test(key))
    .map(([key, item]) => [key, sanitizePublicValue(item, depth + 1)]));
}

function sanitizeResultPage(value) {
  if (!value || typeof value !== 'object' || !Array.isArray(value.items) || !value.pagination || typeof value.pagination !== 'object') throw new Error('invalid API response');
  const pagination = value.pagination;
  if (!Number.isInteger(pagination.limit) || typeof pagination.has_more !== 'boolean' || !(pagination.next_cursor === null || typeof pagination.next_cursor === 'string')) throw new Error('invalid API response');
  return {
    items: value.items.map((item) => sanitizePublicValue(item)),
    ...(Number.isInteger(value.total_count) ? { total_count: value.total_count } : {}),
    ...(Number.isInteger(value.invoice_count) ? { invoice_count: value.invoice_count } : {}),
    ...(Number.isInteger(value.row_count) ? { row_count: value.row_count } : {}),
    pagination: { limit: pagination.limit, has_more: pagination.has_more, next_cursor: pagination.next_cursor },
  };
}

function serializeError(error) {
  if (error instanceof MiaApiError) return {
    code: error.code, status: error.status, message: 'MIA API request failed.', requestId: error.requestId,
  };
  if (error instanceof MiaApiConfigurationError || error instanceof JobInputError) return {
    code: error.code, status: error.status, message: error.message,
  };
  return { code: 'internal_error', message: 'MIA API request could not be processed.' };
}

async function command(action) {
  try { return { ok: true, data: await action() }; }
  catch (error) { return { ok: false, error: serializeError(error) }; }
}

function createJobStore(filePath) {
  function read() {
    try {
      const value = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      return value && typeof value === 'object' ? value : null;
    } catch (error) {
      if (error.code === 'ENOENT' || error instanceof SyntaxError) return null;
      throw error;
    }
  }
  function write(value) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
    const temporary = `${filePath}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(value), { encoding: 'utf8', mode: 0o600 });
    fs.renameSync(temporary, filePath);
  }
  return { read, write, clear: () => { try { fs.unlinkSync(filePath); } catch (error) { if (error.code !== 'ENOENT') throw error; } } };
}

function createJobLifecycleBroker(getClient, store, now = () => new Date().toISOString(), randomUUID = crypto.randomUUID) {
  return Object.freeze({
    resume: () => command(async () => {
      const record = store.read();
      if (!record || record.job_id) return record;
      const intent = validateIntent(record.intent);
      if (typeof record.idempotency_key !== 'string' || record.idempotency_key.length < 8 || record.idempotency_key.length > 256) throw new JobInputError();
      const accepted = sanitizeAccepted(await getClient().createJob(intent, record.idempotency_key));
      const saved = { ...record, job_id: accepted.job_id, updated_at: now() };
      store.write(saved);
      return saved;
    }),
    start: (rawIntent) => command(async () => {
      const intent = validateIntent(rawIntent);
      const existing = store.read();
      const sameIntent = existing && JSON.stringify(existing.intent) === JSON.stringify(intent);
      const record = sameIntent ? existing : {
        job_id: null,
        connection_id: intent.connection_id,
        intent,
        idempotency_key: `desktop-${randomUUID()}`,
        created_at: now(),
        updated_at: now(),
      };
      store.write(record);
      const accepted = sanitizeAccepted(await getClient().createJob(intent, record.idempotency_key));
      const saved = { ...record, job_id: validateJobId(accepted.job_id), updated_at: now() };
      store.write(saved);
      return { record: saved, accepted };
    }),
    status: (jobId) => command(async () => sanitizeStatus(await getClient().getJob(validateJobId(jobId)))),
    summary: (jobId) => command(async () => sanitizeSummary(await getClient().getJobSummary(validateJobId(jobId)))),
    cancel: (jobId) => command(async () => sanitizeStatus(await getClient().cancelJob(validateJobId(jobId)))),
    overview: (jobId, limit, cursor) => command(async () => sanitizeResultPage(await getClient().getOverviewResults(validateJobId(jobId), validateLimit(limit), validateCursor(cursor)))),
    details: (jobId, limit, cursor) => command(async () => sanitizeResultPage(await getClient().getDetailResults(validateJobId(jobId), validateLimit(limit), validateCursor(cursor)))),
    clear: () => command(() => { store.clear(); return null; }),
  });
}

function validateLimit(value) {
  if (!Number.isInteger(value) || value < 1 || value > 1000) throw new JobInputError('invalid_result_limit');
  return value;
}

function validateCursor(value) {
  if (value === undefined || value === null || value === '') return undefined;
  if (typeof value !== 'string' || value.length > 4096) throw new JobInputError('invalid_result_cursor');
  return value;
}

module.exports = { createJobLifecycleBroker, createJobStore, validateIntent, validateJobId };
