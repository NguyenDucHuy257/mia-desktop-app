'use strict';

const { runBrokerCommand, validateConnectionId } = require('./account-connection-broker.cjs');

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function validateQuery(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_result_query');
  const allowed = new Set(['connection_id', 'cursor', 'limit', 'search', 'direction', 'date_from', 'date_to']);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new TypeError('invalid_result_query');
  const limit = value.limit ?? 50;
  const cursor = value.cursor ?? null;
  const search = value.search ?? '';
  const direction = value.direction ?? null;
  const dateFrom = value.date_from ?? null;
  const dateTo = value.date_to ?? null;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw new TypeError('invalid_result_query');
  if (cursor !== null && (typeof cursor !== 'string' || cursor.length > 64)) throw new TypeError('invalid_result_query');
  if (typeof search !== 'string' || search.length > 200) throw new TypeError('invalid_result_query');
  if (direction !== null && !['purchase', 'sold'].includes(direction)) throw new TypeError('invalid_result_query');
  if (dateFrom !== null && (typeof dateFrom !== 'string' || !DATE_PATTERN.test(dateFrom))) throw new TypeError('invalid_result_query');
  if (dateTo !== null && (typeof dateTo !== 'string' || !DATE_PATTERN.test(dateTo))) throw new TypeError('invalid_result_query');
  if ((dateFrom === null) !== (dateTo === null) || dateFrom && dateTo && dateFrom > dateTo) throw new TypeError('invalid_result_query');
  return {
    connection_id: validateConnectionId(value.connection_id), cursor, limit,
    search: search.trim(), direction, date_from: dateFrom, date_to: dateTo,
  };
}

function createResultBroker(getRuntime) {
  return Object.freeze({
    overview: (query) => runBrokerCommand(() => getRuntime().invoke('results.overview', validateQuery(query))),
    details: (query) => runBrokerCommand(() => getRuntime().invoke('results.details', validateQuery(query))),
  });
}

module.exports = { createResultBroker, validateQuery };
