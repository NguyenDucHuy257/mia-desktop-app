class LicenseApiError extends Error {
  constructor(code, message, { status = null, transient = false } = {}) {
    super(message);
    this.name = 'LicenseApiError';
    this.code = code;
    this.status = status;
    this.transient = transient;
  }
}

function validateServerUrl(value, allowInsecureLocalhost = false) {
  let url;
  try { url = new URL(value); } catch { throw new TypeError('invalid MIA key server URL'); }
  const local = ['127.0.0.1', 'localhost', '::1'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(allowInsecureLocalhost && local && url.protocol === 'http:')) {
    throw new TypeError('MIA key server requires HTTPS');
  }
  url.pathname = url.pathname.replace(/\/$/, '');
  return url;
}

function createLicenseApi({ baseUrl, fetchImpl = globalThis.fetch, timeoutMs = 15_000, allowInsecureLocalhost = false }) {
  if (typeof fetchImpl !== 'function') throw new TypeError('fetch implementation is required');
  const root = validateServerUrl(baseUrl, allowInsecureLocalhost);
  const basePath = root.pathname.replace(/\/+$/, '');
  const endpoint = new URL(`${basePath}/verify-key-v2`, root.origin);

  async function verifyKeyV2(payload) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(endpoint, {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
        redirect: 'error',
      });
      const text = await response.text();
      if (text.length > 1024 * 1024) throw new LicenseApiError('response_too_large', 'Key server response is too large');
      let data;
      try { data = text ? JSON.parse(text) : {}; } catch { throw new LicenseApiError('invalid_response', 'Key server returned invalid JSON'); }
      if (!response.ok) {
        const detail = data?.detail && typeof data.detail === 'object' ? data.detail : data;
        throw new LicenseApiError(String(detail?.code || 'license_request_failed'), String(detail?.message || detail || 'Key verification failed'), {
          status: response.status,
          transient: response.status === 408 || response.status === 429 || response.status >= 500,
        });
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new LicenseApiError('invalid_response', 'Key server response is invalid');
      return data;
    } catch (error) {
      if (error instanceof LicenseApiError) throw error;
      const timeout = error?.name === 'AbortError';
      throw new LicenseApiError(timeout ? 'license_timeout' : 'license_network_error', timeout ? 'Key verification timed out' : 'Key server is unavailable', { transient: true });
    } finally {
      clearTimeout(timer);
    }
  }

  return Object.freeze({ verifyKeyV2 });
}

module.exports = { LicenseApiError, createLicenseApi, validateServerUrl };
