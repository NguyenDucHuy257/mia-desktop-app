'use strict';

const { runBrokerCommand, validateConnectionId } = require('./account-connection-broker.cjs');

function validateQuery(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_result_query');
  const allowed = new Set(['connection_id', 'cursor', 'limit', 'search', 'direction']);
  if (Object.keys(value).some((key) => !allowed.has(key))) throw new TypeError('invalid_result_query');
  const limit = value.limit ?? 50;
  const cursor = value.cursor ?? null;
  const search = value.search ?? '';
  const direction = value.direction ?? null;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw new TypeError('invalid_result_query');
  if (cursor !== null && (typeof cursor !== 'string' || cursor.length > 64)) throw new TypeError('invalid_result_query');
  if (typeof search !== 'string' || search.length > 200) throw new TypeError('invalid_result_query');
  if (direction !== null && !['purchase', 'sold'].includes(direction)) throw new TypeError('invalid_result_query');
  return { connection_id: validateConnectionId(value.connection_id), cursor, limit, search: search.trim(), direction };
}

function createResultBroker(getRuntime) {
  return Object.freeze({
    overview: (query) => runBrokerCommand(() => getRuntime().invoke('results.overview', validateQuery(query))),
    details: (query) => runBrokerCommand(() => getRuntime().invoke('results.details', validateQuery(query))),
  });
}

module.exports = { createResultBroker, validateQuery };
