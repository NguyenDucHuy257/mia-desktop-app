'use strict';

const { runBrokerCommand, validateConnectionId } = require('./account-connection-broker.cjs');

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const MAX_COLUMN_FILTERS = 40;
const MAX_EXCLUDED_INVOICES = 2000;

function validateColumnFilters(value) {
  if (value === undefined || value === null) return {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_result_query');
  const entries = Object.entries(value);
  if (entries.length > MAX_COLUMN_FILTERS) throw new TypeError('invalid_result_query');
  const output = {};
  for (const [key, filter] of entries) {
    if (typeof key !== 'string' || key.length < 1 || key.length > 80 || /[\x00-\x1f]/.test(key)) throw new TypeError('invalid_result_query');
    if (typeof filter !== 'string' || filter.length > 200) throw new TypeError('invalid_result_query');
    const normalized = filter.trim();
    if (normalized) output[key] = normalized;
  }
  return output;
}

function validateExcludedBusinessKeys(value) {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > MAX_EXCLUDED_INVOICES) throw new TypeError('invalid_result_query');
  const output = [];
  const seen = new Set();
  for (const item of value) {
    if (typeof item !== 'string' || item.length < 1 || item.length > 512 || /[\x00-\x1f]/.test(item)) throw new TypeError('invalid_result_query');
    if (!seen.has(item)) { seen.add(item); output.push(item); }
  }
  return output;
}

function validateQuery(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_result_query');
  const allowed = new Set(['connection_id', 'cursor', 'limit', 'search', 'direction', 'date_from', 'date_to', 'column_filters', 'exclude_business_keys', 'include_meta']);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new TypeError('invalid_result_query');
  const limit = value.limit ?? 50;
  const cursor = value.cursor ?? null;
  const search = value.search ?? '';
  const direction = value.direction ?? null;
  const dateFrom = value.date_from ?? null;
  const dateTo = value.date_to ?? null;
  const includeMeta = value.include_meta ?? false;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw new TypeError('invalid_result_query');
  if (cursor !== null && (typeof cursor !== 'string' || cursor.length > 256)) throw new TypeError('invalid_result_query');
  if (typeof search !== 'string' || search.length > 200) throw new TypeError('invalid_result_query');
  if (direction !== null && !['purchase', 'sold'].includes(direction)) throw new TypeError('invalid_result_query');
  if (dateFrom !== null && (typeof dateFrom !== 'string' || !DATE_PATTERN.test(dateFrom))) throw new TypeError('invalid_result_query');
  if (dateTo !== null && (typeof dateTo !== 'string' || !DATE_PATTERN.test(dateTo))) throw new TypeError('invalid_result_query');
  if ((dateFrom === null) !== (dateTo === null) || dateFrom && dateTo && dateFrom > dateTo) throw new TypeError('invalid_result_query');
  if (typeof includeMeta !== 'boolean') throw new TypeError('invalid_result_query');
  return {
    connection_id: validateConnectionId(value.connection_id), cursor, limit,
    search: search.trim(), direction, date_from: dateFrom, date_to: dateTo,
    column_filters: validateColumnFilters(value.column_filters),
    exclude_business_keys: validateExcludedBusinessKeys(value.exclude_business_keys),
    include_meta: includeMeta,
  };
}

function createResultBroker(getRuntime) {
  return Object.freeze({
    overview: (query) => runBrokerCommand(() => getRuntime().invoke('results.overview', validateQuery(query))),
    details: (query) => runBrokerCommand(() => getRuntime().invoke('results.details', validateQuery(query))),
  });
}

module.exports = { createResultBroker, validateColumnFilters, validateExcludedBusinessKeys, validateQuery };
