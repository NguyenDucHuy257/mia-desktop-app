'use strict';

const MESSAGES = {
  license_policy_missing: 'Máy chủ chưa trả quyền sử dụng. Cần cập nhật máy chủ key trước khi sử dụng phiên bản này.',
  license_policy_invalid: 'Cấu hình quyền key không hợp lệ. Vui lòng kiểm tra loại key và danh sách MST trên máy chủ.',
  license_tax_code_denied: 'MST này không nằm trong danh sách được cấp phép của key. Không thể đồng bộ hoặc tải dữ liệu tài khoản này.',
  license_date_denied: 'Key dùng thử chỉ cho phép dữ liệu từ 01/08/2026 đến 31/08/2026. Vui lòng chọn lại khoảng thời gian.',
};
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
    || ids.some((id) => typeof id !== 'string' || !/^(?:\d{10}|\d{12})(?:-U?\d{3})?$/.test(id))
    || new Set(ids).size !== ids.length
    || (trial && (value.max_tax_codes !== Number(trial[1] || 1) || !ids.length
      || ids.length > value.max_tax_codes || value.date_from !== '2026-08-01' || value.date_to !== '2026-08-31'))
    || (paidLimited && (value.max_tax_codes !== Number(paidLimited[1]) || !ids.length || ids.length > value.max_tax_codes))
    || (!trial && (value.date_from !== null || value.date_to !== null))
    || (!trial && !paidLimited && value.max_tax_codes !== (ids.length || null))) {
    throw policyError('license_policy_invalid');
  }
  return Object.freeze({ ...value, allowed_tax_codes: Object.freeze([...ids]) });
}

// This runs in Electron, before any data RPC, including exports from cached DB.
// Resolve connection IDs using trusted runtime account records, not renderer MSTs.
function createLicenseRequestGuard(getState) {
  return async (method, params, call) => {
    const dataRequest = method.startsWith('results.')
      || method.startsWith('artifacts.') && !/\.(status|cancel|failures)$/.test(method)
      || ['source.jobs.start', 'source.accounts.create', 'source.accounts.reconnect'].includes(method);
    if (!dataRequest) return params;
    const state = getState();
    if (!state?.active || state.reason !== 'ok') throw policyError('license_policy_missing');
    const policy = validateEntitlements(state.entitlements);
    const query = method === 'source.jobs.start' ? params.intent : params;
    const checkTaxCode = (id) => {
      if (policy.allowed_tax_codes.length && !policy.allowed_tax_codes.includes(String(id || '').trim())) {
        throw policyError('license_tax_code_denied');
      }
    };
    if (query.username) checkTaxCode(query.username);
    const connectionIds = query.connection_ids || (query.connection_id ? [query.connection_id] : []);
    for (const id of policy.allowed_tax_codes.length ? connectionIds : []) {
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

module.exports = { MESSAGES, policyError, validateEntitlements, createLicenseRequestGuard };
