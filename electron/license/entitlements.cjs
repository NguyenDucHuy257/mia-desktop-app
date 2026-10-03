'use strict';

const MESSAGES = {
  license_policy_missing: 'Máy chủ chưa trả quyền sử dụng. Cần cập nhật máy chủ key trước khi sử dụng phiên bản này.',
  license_policy_invalid: 'Cấu hình quyền key không hợp lệ. Vui lòng kiểm tra loại key và danh sách MST trên máy chủ.',
  license_tax_code_denied: 'MST này không nằm trong danh sách được cấp phép của key. Không thể đồng bộ hoặc tải dữ liệu tài khoản này.',
  license_mst_limit_reached: 'Key đã dùng hết số lượng MST được cấp. Không thể thêm tài khoản mới; vui lòng liên hệ hỗ trợ để nâng gói.',
  license_date_denied: 'Key dùng thử chỉ cho phép dữ liệu từ 01/08/2026 đến 31/08/2026. Vui lòng chọn lại khoảng thời gian.',
  license_local_state_failed: 'Không ghi được trạng thái bản quyền trên máy. Kiểm tra quyền thư mục dữ liệu hoặc phần mềm diệt virus rồi thử lại; không cần cấp lại key.',
};
// Node fs error codes (EPERM, EBUSY, EACCES, ENOSPC, ...) stored as the
// license reason mean a local write failed after the server already answered.
const LOCAL_FS_CODE = /^E[A-Z0-9]{2,20}$/;
function localStateErrorCode(state) {
  return LOCAL_FS_CODE.test(String(state?.reason || '')) ? 'license_local_state_failed' : null;
}
// The portal accepts more login formats than the classic 10/12-digit MST
// (branches, delegated users such as 0303761733-U001, ...). Mirror the account
// form USERNAME_PATTERN and the key server MST_RE instead of a tax-code shape.
const TAX_CODE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$/;
function policyError(code) {
  return Object.assign(new Error(MESSAGES[code] || code), { code });
}
function validateEntitlements(value) {
  if (!value) throw policyError('license_policy_missing');
  const trial = /^TEST([1-9]\d*)?$/.exec(value.plan);
  const paidLimited = /^VIP([1-9]\d*)$/.exec(value.plan);
  const ids = value.allowed_tax_codes;
  if (value.version !== 1 || !['V', 'VIP'].includes(value.plan) && !trial && !paidLimited
    || value.trial !== Boolean(trial) || !Array.isArray(ids)
    || ids.some((id) => typeof id !== 'string' || !TAX_CODE_PATTERN.test(id))
    || new Set(ids).size !== ids.length
    // A limited plan may carry an empty list right after activation: dynamic
    // quota, the server binds the first max_tax_codes MSTs used on the key.
    || (trial && (value.max_tax_codes !== Number(trial[1] || 1)
      || ids.length > value.max_tax_codes || value.date_from !== '2026-08-01' || value.date_to !== '2026-08-31'))
    || (paidLimited && (value.max_tax_codes !== Number(paidLimited[1]) || ids.length > value.max_tax_codes))
    || (!trial && (value.date_from !== null || value.date_to !== null))
    || (!trial && !paidLimited && value.max_tax_codes !== (ids.length || null))) {
    throw policyError('license_policy_invalid');
  }
  return Object.freeze({ ...value, allowed_tax_codes: Object.freeze([...ids]) });
}

function isLicenseDataRequest(method) {
  return method.startsWith('results.')
    || method.startsWith('artifacts.') && !/\.(status|cancel|failures)$/.test(method)
    || ['source.jobs.start', 'source.accounts.create', 'source.accounts.reconnect'].includes(method);
}

// This runs in Electron, before any data RPC, including exports from cached DB.
// Resolve connection IDs using trusted runtime account records, not renderer MSTs.
function createLicenseRequestGuard(getState) {
  return async (method, params, call) => {
    if (!isLicenseDataRequest(method)) return params;
    const state = getState();
    if (!state?.active || state.reason !== 'ok') throw policyError(localStateErrorCode(state) || 'license_policy_missing');
    const policy = validateEntitlements(state.entitlements);
    const query = method === 'source.jobs.start' ? params.intent : params;
    // A limited plan (max_tax_codes set) is always scoped to the MSTs the
    // server has declared or bound, even while that list is still empty.
    const restricted = policy.max_tax_codes !== null || policy.allowed_tax_codes.length > 0;
    const checkTaxCode = (id) => {
      if (restricted && !policy.allowed_tax_codes.includes(String(id || '').trim())) {
        throw policyError('license_tax_code_denied');
      }
    };
    if (query.username) checkTaxCode(query.username);
    const connectionIds = query.connection_ids || (query.connection_id ? [query.connection_id] : []);
    for (const id of restricted ? connectionIds : []) {
      const account = await call('source.accounts.get', { connection_id: id });
      checkTaxCode(account.username);
    }
    if (policy.trial && !method.startsWith('source.accounts.')) {
      const from = query.date_from || policy.date_from;
      const to = query.date_to || policy.date_to;
      if (from < policy.date_from || to > policy.date_to || from > to) throw policyError('license_date_denied');
      const bounded = { ...query, date_from: from, date_to: to };
      return method === 'source.jobs.start' ? { ...params, intent: bounded } : bounded;
    }
    return params;
  };
}

module.exports = { MESSAGES, policyError, validateEntitlements, isLicenseDataRequest, createLicenseRequestGuard, localStateErrorCode };
