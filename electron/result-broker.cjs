'use strict';

const { runBrokerCommand, validateConnectionId } = require('./account-connection-broker.cjs');

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const MAX_RESULT_PAGE_SIZE = 50;
const MAX_RESULT_CURSOR_LENGTH = 4096;
const MAX_FILTER_COLUMNS = 80;
const MAX_FILTER_VALUES = 500;
const MAX_EXCLUSION_KEYS = 2000;
const MAX_EXCLUSION_RULES = 100;

function validateColumnFilters(value) {
  if (value === undefined || value === null) return {};
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length > MAX_FILTER_COLUMNS) throw new TypeError('invalid_result_filters');
  const output = {};
  for (const [column, rule] of Object.entries(value)) {
    if (!/^[A-Za-z0-9_]{1,80}$/.test(column) || !rule || typeof rule !== 'object' || Array.isArray(rule)) throw new TypeError('invalid_result_filters');
    if (Object.keys(rule).some((key) => !['values', 'search', 'operator', 'value', 'value_to'].includes(key))) throw new TypeError('invalid_result_filters');
    if (rule.values !== undefined && (!Array.isArray(rule.values) || rule.values.length > MAX_FILTER_VALUES || rule.values.some((item) => item !== null && !['string', 'number', 'boolean'].includes(typeof item)))) throw new TypeError('invalid_result_filters');
    if (rule.search !== undefined && (typeof rule.search !== 'string' || rule.search.length > 200)) throw new TypeError('invalid_result_filters');
    if (rule.operator !== undefined && !['contains', 'not_contains', 'starts_with', 'ends_with', 'equals', 'not_equals', 'gt', 'gte', 'lt', 'lte', 'number_equals', 'between'].includes(rule.operator)) throw new TypeError('invalid_result_filters');
    for (const name of ['value', 'value_to']) if (rule[name] !== undefined && rule[name] !== null && !['string', 'number', 'boolean'].includes(typeof rule[name])) throw new TypeError('invalid_result_filters');
    output[column] = { ...rule, ...(rule.values ? { values: [...rule.values] } : {}) };
  }
  return output;
}

function validateExclusion(value) {
  if (value === undefined || value === null) return { keys: [], rules: [] };
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some((key) => !['keys', 'rules'].includes(key))) throw new TypeError('invalid_result_exclusion');
  const keys = value.keys ?? [];
  const rules = value.rules ?? [];
  if (!Array.isArray(keys) || keys.length > MAX_EXCLUSION_KEYS || keys.some((key) => typeof key !== 'string' || key.length < 1 || key.length > 512)) throw new TypeError('invalid_result_exclusion');
  if (!Array.isArray(rules) || rules.length > MAX_EXCLUSION_RULES) throw new TypeError('invalid_result_exclusion');
  return {
    keys: [...new Set(keys)],
    rules: rules.map((rule) => {
      if (!rule || typeof rule !== 'object' || Array.isArray(rule) || !['overview', 'details'].includes(rule.kind) || !rule.query || typeof rule.query !== 'object') throw new TypeError('invalid_result_exclusion');
      const query = validateQuery({ ...rule.query, cursor: null, limit: 1, exclusion: undefined });
      const exceptKeys = rule.except_keys ?? [];
      if (!Array.isArray(exceptKeys) || exceptKeys.length > MAX_EXCLUSION_KEYS || exceptKeys.some((key) => typeof key !== 'string' || key.length > 512)) throw new TypeError('invalid_result_exclusion');
      return { kind: rule.kind, query, except_keys: [...new Set(exceptKeys)] };
    }),
  };
}

function validateSort(value) {
  if (value === undefined || value === null) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some((key) => !['column', 'direction'].includes(key))) throw new TypeError('invalid_result_sort');
  if (typeof value.column !== 'string' || !/^[A-Za-z0-9_]{1,80}$/.test(value.column) || !['asc', 'desc'].includes(value.direction)) throw new TypeError('invalid_result_sort');
  return { column: value.column, direction: value.direction };
}

function validateQuery(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_result_query');
  const allowed = new Set(['connection_id', 'cursor', 'limit', 'search', 'direction', 'query_type', 'query_types', 'date_from', 'date_to', 'column_filters', 'exclusion', 'sort']);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new TypeError('invalid_result_query');
  const limit = value.limit ?? MAX_RESULT_PAGE_SIZE;
  const cursor = value.cursor ?? null;
  const search = value.search ?? '';
  const direction = value.direction ?? null;
  const queryType = value.query_type ?? null;
  const queryTypes = value.query_types ?? null;
  const dateFrom = value.date_from ?? null;
  const dateTo = value.date_to ?? null;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_RESULT_PAGE_SIZE) throw new TypeError('invalid_result_query');
  // Result cursors are opaque source-owned keyset cursors wrapped with the
  // desktop direction index.  They are intentionally not parsed here; only a
  // bounded string is accepted so page 2+ can round-trip without truncation.
  if (cursor !== null && (typeof cursor !== 'string' || cursor.length > MAX_RESULT_CURSOR_LENGTH)) throw new TypeError('invalid_result_query');
  if (typeof search !== 'string' || search.length > 200) throw new TypeError('invalid_result_query');
  if (direction !== null && !['purchase', 'sold'].includes(direction)) throw new TypeError('invalid_result_query');
  if (queryType !== null && !['query', 'sco-query'].includes(queryType)) throw new TypeError('invalid_result_query');
  if (queryTypes !== null && (!Array.isArray(queryTypes) || queryTypes.length < 1 || queryTypes.length > 2 || new Set(queryTypes).size !== queryTypes.length || queryTypes.some((item) => !['query', 'sco-query'].includes(item)))) throw new TypeError('invalid_result_query');
  if (queryType !== null && queryTypes !== null) throw new TypeError('invalid_result_query');
  if (dateFrom !== null && (typeof dateFrom !== 'string' || !DATE_PATTERN.test(dateFrom))) throw new TypeError('invalid_result_query');
  if (dateTo !== null && (typeof dateTo !== 'string' || !DATE_PATTERN.test(dateTo))) throw new TypeError('invalid_result_query');
  if ((dateFrom === null) !== (dateTo === null) || dateFrom && dateTo && dateFrom > dateTo) throw new TypeError('invalid_result_query');
  return {
    connection_id: validateConnectionId(value.connection_id), cursor, limit,
    search: search.trim(), direction, query_type: queryType,
    query_types: queryTypes === null ? null : [...queryTypes],
    date_from: dateFrom, date_to: dateTo,
    column_filters: validateColumnFilters(value.column_filters),
    exclusion: validateExclusion(value.exclusion),
    sort: validateSort(value.sort),
  };
}

function validateFacetQuery(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_result_facet');
  const { kind, column, facet_limit: facetLimit } = value;
  if (!['overview', 'details', 'reconciliation'].includes(kind) || typeof column !== 'string' || !/^[A-Za-z0-9_]{1,80}$/.test(column)) throw new TypeError('invalid_result_facet');
  if (facetLimit !== undefined && (!Number.isInteger(facetLimit) || facetLimit < 1 || facetLimit > 500)) throw new TypeError('invalid_result_facet');
  const { kind: _kind, column: _column, facet_limit: _facetLimit, ...base } = value;
  const query = validateQuery({ ...base, cursor: null, limit: 1 });
  return { ...query, kind, column, facet_limit: facetLimit ?? 250 };
}

function createResultBroker(getRuntime) {
  const invokeResult = (method, query) => getRuntime().invoke(
    `results.${method}`, validateQuery(query),
    { timeoutMs: method === 'reconciliation' ? 30_000 : 15_000 },
  );
  return Object.freeze({
    materialStart: (query) => runBrokerCommand(() => invokeResult('materialStart', query)),
    materialStatus: (query) => runBrokerCommand(() => invokeResult('materialStatus', query)),
    overview: (query) => runBrokerCommand(() => invokeResult('overview', query)),
    details: (query) => runBrokerCommand(() => invokeResult('details', query)),
    reconciliation: (query) => runBrokerCommand(() => invokeResult('reconciliation', query)),
    facets: (query) => runBrokerCommand(() => getRuntime().invoke('results.facets', validateFacetQuery(query))),
  });
}

module.exports = { MAX_RESULT_CURSOR_LENGTH, MAX_RESULT_PAGE_SIZE, createResultBroker, validateColumnFilters, validateExclusion, validateFacetQuery, validateQuery, validateSort };
