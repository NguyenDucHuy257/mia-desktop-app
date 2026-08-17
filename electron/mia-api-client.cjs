'use strict';

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

class MiaApiError extends Error {
  constructor(status, code, message, requestId) {
    super(message);
    this.name = 'MiaApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

class MiaApiConfigurationError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'MiaApiConfigurationError';
    this.code = code;
  }
}

function assertSafeApiBaseUrl(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 2048) {
    throw new MiaApiConfigurationError('api_not_configured', 'MIA API base URL is not configured.');
  }

  let url;
  try {
    url = new URL(value);
  } catch {
    throw new MiaApiConfigurationError('invalid_api_url', 'MIA API base URL is invalid.');
  }

  const secure = url.protocol === 'https:';
  const localDevelopment = url.protocol === 'http:' && LOOPBACK_HOSTS.has(url.hostname);
  if (!secure && !localDevelopment) {
    throw new MiaApiConfigurationError(
      'insecure_api_url',
      'MIA API requires HTTPS except for a loopback development server.',
    );
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new MiaApiConfigurationError('invalid_api_url', 'MIA API base URL contains unsupported parts.');
  }

  const pathname = url.pathname === '/' ? '' : url.pathname.replace(/\/+$/, '');
  return `${url.origin}${pathname}`;
}

function assertAccessToken(value) {
  if (typeof value !== 'string' || value.length < 8 || value.length > 8192) {
    throw new MiaApiConfigurationError('api_not_configured', 'MIA API access token is not configured.');
  }
  return value;
}

class MiaMainApiClient {
  constructor({ baseUrl, getAccessToken, fetchImpl = globalThis.fetch, timeoutMs = 20_000 }) {
    if (typeof fetchImpl !== 'function') throw new TypeError('fetch implementation is required');
    if (typeof getAccessToken !== 'function') throw new TypeError('getAccessToken must be a function');
    this.baseUrl = assertSafeApiBaseUrl(baseUrl);
    this.getAccessToken = getAccessToken;
    this.fetchImpl = fetchImpl;
    this.timeoutMs = timeoutMs;
  }

  createConnection({ username, password }) {
    return this.request('/v1/account-connections', {
      method: 'POST',
      body: { username, password },
    });
  }

  getConnection(connectionId) {
    return this.request(`/v1/account-connections/${encodeURIComponent(connectionId)}`);
  }

  reconnectConnection(connectionId, { username, password }) {
    return this.request(`/v1/account-connections/${encodeURIComponent(connectionId)}/reconnect`, {
      method: 'POST',
      body: { username, password },
    });
  }

  async revokeConnection(connectionId) {
    await this.request(`/v1/account-connections/${encodeURIComponent(connectionId)}`, {
      method: 'DELETE',
    });
  }

  async request(pathname, options = {}) {
    const token = assertAccessToken(await this.getAccessToken());
    const headers = new Headers(options.headers);
    headers.set('X-MIA-API-Key', token);
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');

    let response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${pathname}`, {
        method: options.method ?? 'GET',
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (error) {
      const timedOut = error && typeof error === 'object' && (
        error.name === 'TimeoutError' || error.name === 'AbortError'
      );
      throw new MiaApiError(
        0,
        timedOut ? 'api_timeout' : 'network_error',
        timedOut ? 'MIA API request timed out.' : 'MIA API network request failed.',
      );
    }

    if (response.status === 204) return undefined;
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = payload && typeof payload === 'object' ? payload.error : undefined;
      const detail = payload && typeof payload === 'object' ? payload.detail : undefined;
      throw new MiaApiError(
        response.status,
        String(error?.code ?? 'http_error'),
        String(error?.message ?? detail ?? `HTTP ${response.status}`),
        typeof error?.request_id === 'string' ? error.request_id : undefined,
      );
    }
    return payload;
  }
}

function createMiaApiClientFromEnvironment(env = process.env, fetchImpl = globalThis.fetch) {
  return new MiaMainApiClient({
    baseUrl: env.MIA_API_BASE_URL,
    getAccessToken: () => env.MIA_API_ACCESS_TOKEN,
    fetchImpl,
  });
}

module.exports = {
  MiaApiConfigurationError,
  MiaApiError,
  MiaMainApiClient,
  assertSafeApiBaseUrl,
  createMiaApiClientFromEnvironment,
};
