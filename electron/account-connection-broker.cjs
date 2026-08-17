'use strict';

const {
  MiaApiConfigurationError,
  MiaApiError,
} = require('./mia-api-client.cjs');

const TAX_CODE_PATTERN = /^\d{10}(?:-\d{3})?$/;
const CONNECTION_ID_PATTERN = /^[A-Za-z0-9._:-]{1,128}$/;

class BrokerInputError extends Error {
  constructor(message) {
    super(message);
    this.name = 'BrokerInputError';
    this.code = 'invalid_credentials';
    this.status = 400;
  }
}

function validateCredentials(value) {
  if (!value || typeof value !== 'object') throw new BrokerInputError('Account credentials are invalid.');
  const username = typeof value.username === 'string' ? value.username.trim() : '';
  const password = typeof value.password === 'string' ? value.password : '';
  if (!TAX_CODE_PATTERN.test(username) || password.length === 0 || password.length > 256) {
    throw new BrokerInputError('Account credentials are invalid.');
  }
  return { username, password };
}

function validateConnectionId(value) {
  if (typeof value !== 'string' || !CONNECTION_ID_PATTERN.test(value)) {
    const error = new BrokerInputError('Connection identifier is invalid.');
    error.code = 'invalid_connection_id';
    throw error;
  }
  return value;
}

function serializeError(error) {
  if (error instanceof MiaApiError) {
    return {
      code: error.code,
      status: Number.isInteger(error.status) ? error.status : undefined,
      message: 'MIA API request failed.',
      requestId: error.requestId,
    };
  }
  if (error instanceof MiaApiConfigurationError || error instanceof BrokerInputError) {
    return {
      code: error.code,
      status: Number.isInteger(error.status) ? error.status : undefined,
      message: error.message,
    };
  }
  return { code: 'internal_error', message: 'MIA API request could not be processed.' };
}

async function runBrokerCommand(command) {
  try {
    return { ok: true, data: await command() };
  } catch (error) {
    return { ok: false, error: serializeError(error) };
  }
}

function createAccountConnectionBroker(getClient) {
  if (typeof getClient !== 'function') throw new TypeError('getClient must be a function');
  return Object.freeze({
    create: (credentials) => runBrokerCommand(() => {
      const validCredentials = validateCredentials(credentials);
      return getClient().createConnection(validCredentials);
    }),
    get: (connectionId) => runBrokerCommand(() => {
      const validConnectionId = validateConnectionId(connectionId);
      return getClient().getConnection(validConnectionId);
    }),
    reconnect: (connectionId, credentials) => runBrokerCommand(() => {
      const validConnectionId = validateConnectionId(connectionId);
      const validCredentials = validateCredentials(credentials);
      return getClient().reconnectConnection(validConnectionId, validCredentials);
    }),
    revoke: (connectionId) => runBrokerCommand(async () => {
      const validConnectionId = validateConnectionId(connectionId);
      await getClient().revokeConnection(validConnectionId);
      return null;
    }),
  });
}

module.exports = {
  BrokerInputError,
  createAccountConnectionBroker,
  runBrokerCommand,
  validateConnectionId,
  validateCredentials,
};
