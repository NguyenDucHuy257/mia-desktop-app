class LicenseApiError extends Error {
  constructor(code, message, { status = null, transient = false } = {}) {
    super(message);
    this.name = 'LicenseApiError';
    this.code = code;
    this.status = status;
    this.transient = transient;
  }
}

function validateBaseUrl(value, allowInsecureLocalhost = false) {
  let url;
  try { url = new URL(value); } catch { throw new TypeError('invalid MIA license API URL'); }
  const local = ['127.0.0.1', 'localhost', '::1'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(allowInsecureLocalhost && local && url.protocol === 'http:')) {
    throw new TypeError('MIA license API requires HTTPS');
  }
  url.pathname = url.pathname.replace(/\/$/, '');
  return url;
}

function createLicenseApi({ baseUrl, fetchImpl = globalThis.fetch, timeoutMs = 15_000, allowInsecureLocalhost = false }) {
  if (typeof fetchImpl !== 'function') throw new TypeError('fetch implementation is required');
  const root = validateBaseUrl(baseUrl, allowInsecureLocalhost);
  async function post(endpoint, payload) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(new URL(root.pathname + endpoint, root), {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
        redirect: 'error',
      });
      const text = await response.text();
      if (text.length > 1024 * 1024) throw new LicenseApiError('response_too_large', 'License server response is too large');
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { throw new LicenseApiError('invalid_response', 'License server returned invalid JSON'); }
      if (!response.ok) {
        const detail = data?.detail && typeof data.detail === 'object' ? data.detail : data;
        throw new LicenseApiError(String(detail?.code || 'license_request_failed'), String(detail?.message || 'License request failed'), {
          status: response.status,
          transient: response.status === 408 || response.status === 429 || response.status >= 500,
        });
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new LicenseApiError('invalid_response', 'License server response is invalid');
      return data;
    } catch (error) {
      if (error instanceof LicenseApiError) throw error;
      const timeout = error?.name === 'AbortError';
      throw new LicenseApiError(timeout ? 'license_timeout' : 'license_network_error', timeout ? 'License request timed out' : 'License server is unavailable', { transient: true });
    } finally {
      clearTimeout(timer);
    }
  }
  return Object.freeze({
    challenge: (payload) => post('/challenge', payload),
    verify: (payload) => post('/verify', payload),
    migrate: (payload) => post('/migrate', payload),
    activate: (payload) => post('/activate', payload),
    recover: (payload) => post('/recover', payload),
    updatePhone: (payload) => post('/update-phone', payload),
  });
}

module.exports = { LicenseApiError, createLicenseApi, validateBaseUrl };
