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

function localErrorMessage(code) {
  if (code === 'authentication_failed') return 'Không thể xác thực tài khoản với Cổng HĐĐT.';
  if (code === 'invalid_source_credentials') return 'Tên đăng nhập hoặc mật khẩu không đúng.';
  if (code === 'source_account_locked') return 'Tài khoản đã bị khóa vì nhập sai thông tin quá số lần quy định.';
  if (code === 'source_login_rejected') return 'Cổng hóa đơn từ chối đăng nhập.';
  if (code === 'source_token_missing') return 'Cổng hóa đơn không trả về phiên đăng nhập hợp lệ.';
  if (code === 'source_rate_limited') return 'Cổng hóa đơn đang giới hạn truy cập. Vui lòng thử lại sau.';
  if (String(code).startsWith('source_http_')) return 'Dịch vụ Cổng HĐĐT đang tạm thời không khả dụng.';
  return 'Local account operation failed.';
}

function serializeError(error) {
  const localCodes = new Set([
    'account_not_found', 'account_duplicate', 'account_in_use', 'database_locked', 'database_unavailable',
    'job_not_found', 'job_conflict', 'stale_job_update', 'invalid_job_transition', 'authentication_failed',
    'invalid_source_credentials', 'source_account_locked', 'source_login_rejected', 'source_token_missing',
    'source_rate_limited',
  ]);
  if (localCodes.has(error?.message) || String(error?.message || '').startsWith('source_http_')) {
    return { code: error.message, message: localErrorMessage(error.message) };
  }
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
  if (error?.name === 'JobInputError') {
    return { code: error.code, status: error.status, message: error.message };
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