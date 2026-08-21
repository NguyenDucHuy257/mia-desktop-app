'use strict';

const crypto = require('node:crypto');
const { runBrokerCommand, validateConnectionId } = require('./account-connection-broker.cjs');

const JOB_ID_PATTERN = /^(?:job_[A-Za-z0-9-]{1,128}|[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12})$/i;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const DIRECTIONS = new Set(['purchase', 'sold']);
const QUERY_TYPES = new Set(['query', 'sco-query']);
const SCOPES = new Set(['overview', 'detail']);
const DATA_TYPES = new Set(['invoice', 'xml', 'html', 'pdf']);
const TERMINAL_STATUSES = new Set(['completed', 'completed_with_warning', 'failed', 'cancelled', 'abandoned']);
const JOB_POLICY_NAMESPACE = 'desktop-v4';

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
  const allowed = new Set(['connection_id', 'date_from', 'date_to', 'directions', 'query_types', 'scopes', 'data_types', 'force_refresh']);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new JobInputError();
  if (Object.hasOwn(value, 'force_refresh') && typeof value.force_refresh !== 'boolean') throw new JobInputError();
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
  if (Object.hasOwn(value, 'force_refresh')) intent.force_refresh = value.force_refresh;
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
  return `${JOB_POLICY_NAMESPACE}-${crypto.createHash('sha256').update(JSON.stringify(intent)).digest('hex')}`;
}

function createJobLifecycleBroker(getRuntime, now = () => new Date().toISOString(), protector, createAttemptId = crypto.randomUUID) {
  if (typeof getRuntime !== 'function') throw new TypeError('Invalid offline runtime dependency.');
  const attemptKeys = new Map();
  const startProductionJob = async (intent, key) => {
    if (!protector?.decrypt) throw new TypeError('Secure credential protector is required.');
    const runtime = getRuntime();
    const secret = await runtime.invoke('accounts.secret', { account_id: intent.connection_id });
    const password = protector.decrypt(Buffer.from(secret.encrypted_password, 'base64'));
    try {
      return await runtime.invoke('source.jobs.start', {
        intent, username: secret.username, password, idempotency_key: key,
      }, { timeoutMs: 15000 });
    } finally {
      // The plaintext reference is released immediately after the local call.
    }
  };
  return Object.freeze({
    resume: () => runBrokerCommand(async () => {
      const records = await getRuntime().invoke('source.jobs.resume_all');
      return records[0] ?? null;
    }),
    resumeAll: () => runBrokerCommand(async () => getRuntime().invoke('source.jobs.resume_all')),
    latestAll: () => runBrokerCommand(async () => getRuntime().invoke('source.jobs.latest')),
    start: (rawIntent) => runBrokerCommand(async () => {
      const intent = validateIntent(rawIntent);
      const baseKey = idempotencyKey(intent);
      const currentKey = attemptKeys.get(baseKey) || baseKey;
      let record = await startProductionJob(intent, currentKey);
      if (TERMINAL_STATUSES.has(record.status)) {
        const nextKey = `${baseKey}-${createAttemptId()}`;
        attemptKeys.set(baseKey, nextKey);
        record = await startProductionJob(intent, nextKey);
      }
      return { record, accepted: { job_id: record.job_id, status: record.status, current_stage: record.stage, worker_slot_id: null } };
    }),
    status: (jobId) => runBrokerCommand(() => getRuntime().invoke('source.jobs.status', { job_id: validateJobId(jobId) })),
    summary: (jobId) => runBrokerCommand(() => getRuntime().invoke('source.jobs.summary', { job_id: validateJobId(jobId) })),
    cancel: (jobId) => runBrokerCommand(() => getRuntime().invoke('source.jobs.cancel', { job_id: validateJobId(jobId) })),
    clear: () => runBrokerCommand(async () => null),
  });
}

module.exports = { JOB_POLICY_NAMESPACE, createJobLifecycleBroker, idempotencyKey, validateIntent, validateJobId };
