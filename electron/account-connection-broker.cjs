'use strict';

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
  if (code === 'idempotency_conflict') return 'Yêu cầu đồng bộ bị xung đột idempotency.';
  if (code === 'connection_not_found' || code === 'resource_not_found') return 'Không tìm thấy kết nối tài khoản nguồn.';
  if (code === 'account_purge_failed') return 'Không thể xóa sạch dữ liệu tài khoản.';
  if (code === 'result_export_no_overview_data') return 'Không có dữ liệu Tổng quan để tạo Excel.';
  if (code === 'result_export_no_detail_data') return 'Không có dữ liệu Chi tiết để tạo Excel.';
  if (code === 'result_export_empty') return 'Không có dữ liệu phù hợp để tạo Excel.';
  if (code === 'result_reconciliation_coverage_missing') return 'Chưa đủ dữ liệu Tổng quan và Chi tiết để đối chiếu.';
  if (code === 'result_job_not_found') return 'Chưa có dữ liệu đồng bộ để tạo Excel.';
  if (code === 'result_export_template_missing') return 'Thiếu mẫu Excel nguồn.';
  if (code === 'artifact_write_denied') return 'Không có quyền ghi vào thư mục lưu trữ.';
  if (code === 'artifact_write_failed' || code === 'invalid_artifact_directory') return 'Không thể ghi file vào thư mục lưu trữ.';
  if (code === 'invalid_result_export_range') return 'Khoảng ngày xuất Excel không hợp lệ.';
  if (code === 'result_export_failed') return 'Không thể dựng file Excel từ dữ liệu đã lưu.';
  if (code === 'artifact_cancelled') return 'Đã dừng tải XML/HTML.';
  if (code === 'artifact_task_active') return 'Đang có một lượt tải XML/HTML khác.';
  if (code === 'artifact_batch_empty') return 'Không có artifact XML/HTML phù hợp để tải.';
  if (String(code).startsWith('source_http_')) return 'Dịch vụ Cổng HĐĐT đang tạm thời không khả dụng.';
  return String(code || 'Local runtime operation failed.');
}

function serializeError(error) {
  const publicCodes = new Set([
    'account_not_found', 'account_duplicate', 'account_in_use', 'account_purge_failed',
    'database_locked', 'database_unavailable', 'job_not_found', 'job_conflict',
    'stale_job_update', 'invalid_job_transition', 'authentication_failed',
    'invalid_source_credentials', 'source_account_locked', 'source_login_rejected',
    'source_token_missing', 'source_rate_limited', 'idempotency_conflict',
    'connection_not_found', 'resource_not_found', 'source_account_failed',
    'source_job_failed', 'result_export_no_overview_data',
    'result_export_no_detail_data', 'result_reconciliation_coverage_missing', 'result_export_empty', 'result_job_not_found',
    'result_export_template_missing', 'result_export_failed', 'artifact_write_denied',
    'artifact_write_failed', 'invalid_artifact_directory',
    'invalid_result_export_range', 'artifact_cancelled', 'artifact_task_active',
    'artifact_batch_empty',
  ]);
  const runtimeMessage = String(error?.message || '');
  if (publicCodes.has(runtimeMessage) || runtimeMessage.startsWith('source_http_')) {
    return { code: runtimeMessage, message: localErrorMessage(runtimeMessage) };
  }
  if (error instanceof BrokerInputError) {
    return {
      code: error.code,
      status: Number.isInteger(error.status) ? error.status : undefined,
      message: error.message,
    };
  }
  if (error?.name === 'JobInputError') {
    return { code: error.code, status: error.status, message: error.message };
  }
  return { code: 'internal_error', message: 'Local runtime request could not be processed.' };
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
