'use strict';

const crypto = require('node:crypto');
const { runBrokerCommand, validateConnectionId } = require('./account-connection-broker.cjs');

const JOB_ID_PATTERN = /^job_[A-Za-z0-9-]{1,128}$/;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const DIRECTIONS = new Set(['purchase', 'sold']);
const QUERY_TYPES = new Set(['query', 'sco-query']);
const SCOPES = new Set(['overview', 'detail']);
const DATA_TYPES = new Set(['invoice', 'xml', 'html', 'pdf']);

class JobInputError extends Error {
  constructor(code = 'invalid_job_input') {
    super('Job request is invalid.');
    this.name = 'JobInputError';
    this.code = code;
    this.status = 400;
  }
}

function uniqueEnum(value, allowed, max, allowEmpty = false) {
  if (!Array.isArray(value) || (!allowEmpty && value.length < 1) || value.length > max) throw new JobInputError();
  if (new Set(value).size !== value.length || value.some((item) => !allowed.has(item))) throw new JobInputError();
  return [...value].sort();
}

function validateIntent(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new JobInputError();
  const allowed = new Set(['connection_id', 'date_from', 'date_to', 'directions', 'query_types', 'scopes', 'data_types']);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new JobInputError();
  const connectionId = validateConnectionId(value.connection_id);
  if (!DATE_PATTERN.test(value.date_from) || !DATE_PATTERN.test(value.date_to) || value.date_from > value.date_to) throw new JobInputError();
  const intent = {
    connection_id: connectionId,
    date_from: value.date_from,
    date_to: value.date_to,
    directions: uniqueEnum(value.directions, DIRECTIONS, 2, true),
    query_types: uniqueEnum(value.query_types, QUERY_TYPES, 2),
    scopes: uniqueEnum(value.scopes, SCOPES, 2, true),
    data_types: uniqueEnum(value.data_types, DATA_TYPES, 4, true),
  };
  if (intent.directions.length === 0 || intent.scopes.length === 0 || intent.data_types.length === 0) {
    throw new JobInputError('empty_job_selection');
  }
  return intent;
}

function validateJobId(value) {
  if (typeof value !== 'string' || !JOB_ID_PATTERN.test(value)) throw new JobInputError('invalid_job_id');
  return value;
}

function idempotencyKey(intent) {
  return `desktop-${crypto.createHash('sha256').update(JSON.stringify(intent)).digest('hex')}`;
}

function createJobLifecycleBroker(getRuntime, now = () => new Date().toISOString(), protector) {
  if (typeof getRuntime !== 'function') throw new TypeError('Invalid offline runtime dependency.');
  const launchCrawler = async (record) => {
    if (!record || !protector?.decrypt || !['queued', 'waiting_account', 'running'].includes(record.status)) return;
    const runtime = getRuntime();
    const secret = await runtime.invoke('accounts.secret', { account_id: record.connection_id });
    const password = protector.decrypt(Buffer.from(secret.encrypted_password, 'base64'));
    try {
      await runtime.invoke('crawler.start', {
        job_id: record.job_id, connection_id: record.connection_id, intent: record.intent,
        username: secret.username, password,
      }, { timeoutMs: 15000 });
    } finally {
      // Drop the only plaintext reference immediately after it crosses the local stdio boundary.
    }
  };
  return Object.freeze({
    resume: () => runBrokerCommand(async () => {
      const record = await getRuntime().invoke('jobs.resume');
      await launchCrawler(record);
      return record;
    }),
    start: (rawIntent) => runBrokerCommand(async () => {
      const intent = validateIntent(rawIntent);
      const record = await getRuntime().invoke('jobs.start', {
        connection_id: intent.connection_id, intent, idempotency_key: idempotencyKey(intent), timestamp: now(),
      });
      await launchCrawler(record);
      return { record, accepted: { job_id: record.job_id, status: record.status, current_stage: record.stage, worker_slot_id: null } };
    }),
    status: (jobId) => runBrokerCommand(() => getRuntime().invoke('jobs.status', { job_id: validateJobId(jobId) })),
    summary: (jobId) => runBrokerCommand(() => getRuntime().invoke('jobs.summary', { job_id: validateJobId(jobId) })),
    cancel: (jobId) => runBrokerCommand(() => getRuntime().invoke('jobs.cancel', { job_id: validateJobId(jobId), timestamp: now() })),
    clear: () => runBrokerCommand(async () => { await getRuntime().invoke('jobs.clear'); return null; }),
  });
}

module.exports = { createJobLifecycleBroker, idempotencyKey, validateIntent, validateJobId };
