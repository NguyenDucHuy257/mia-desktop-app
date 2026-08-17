import type {
  AccountConnection,
  CreateJobRequest,
  JobAccepted,
  JobStatusResponse,
  JobSummaryResponse,
  ResultPage,
} from './contracts';

export class MiaApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = 'MiaApiError';
  }
}

interface MiaApiClientOptions {
  baseUrl: string;
  getAccessToken(): string | Promise<string>;
  fetchImpl?: typeof fetch;
}

export class MiaApiClient {
  private readonly baseUrl: string;
  private readonly getAccessToken: MiaApiClientOptions['getAccessToken'];
  private readonly fetchImpl: typeof fetch;

  constructor(options: MiaApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    this.getAccessToken = options.getAccessToken;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  live() { return this.request<{ status: 'ok' }>('/health/live', { authenticated: false }); }
  ready() { return this.request<{ status: 'ready' }>('/health/ready', { authenticated: false }); }

  createConnection(username: string, password: string) {
    return this.request<AccountConnection>('/v1/account-connections', {
      method: 'POST',
      body: { username, password },
    });
  }

  getConnection(connectionId: string) {
    return this.request<AccountConnection>(`/v1/account-connections/${encodeURIComponent(connectionId)}`);
  }

  reconnectConnection(connectionId: string, username: string, password: string) {
    return this.request<AccountConnection>(`/v1/account-connections/${encodeURIComponent(connectionId)}/reconnect`, {
      method: 'POST',
      body: { username, password },
    });
  }

  async revokeConnection(connectionId: string) {
    await this.request<void>(`/v1/account-connections/${encodeURIComponent(connectionId)}`, { method: 'DELETE' });
  }

  createJob(body: CreateJobRequest, idempotencyKey: string = crypto.randomUUID()) {
    return this.request<JobAccepted>('/v1/jobs', {
      method: 'POST',
      body,
      headers: { 'Idempotency-Key': idempotencyKey },
    });
  }

  getJob(jobId: string) {
    return this.request<JobStatusResponse>(`/v1/jobs/${encodeURIComponent(jobId)}`);
  }

  getJobSummary(jobId: string) {
    return this.request<JobSummaryResponse>(`/v1/jobs/${encodeURIComponent(jobId)}/summary`);
  }

  cancelJob(jobId: string) {
    return this.request<JobStatusResponse>(`/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
  }

  getOverviewResults<T>(jobId: string, limit = 200, cursor?: string) {
    return this.resultPage<T>(jobId, 'overview', limit, cursor);
  }

  getDetailResults<T>(jobId: string, limit = 200, cursor?: string) {
    return this.resultPage<T>(jobId, 'details', limit, cursor);
  }

  private resultPage<T>(jobId: string, kind: 'overview' | 'details', limit: number, cursor?: string) {
    const query = new URLSearchParams({ limit: String(limit) });
    if (cursor) query.set('cursor', cursor);
    return this.request<ResultPage<T>>(`/v1/jobs/${encodeURIComponent(jobId)}/results/${kind}?${query}`);
  }

  private async request<T>(
    path: string,
    options: {
      method?: string;
      body?: unknown;
      headers?: Record<string, string>;
      authenticated?: boolean;
    } = {},
  ): Promise<T> {
    const headers = new Headers(options.headers);
    if (options.authenticated !== false) headers.set('X-MIA-API-Key', await this.getAccessToken());
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    if (response.status === 204) return undefined as T;
    const payload = await response.json().catch(() => null) as null | Record<string, unknown>;
    if (!response.ok) {
      const error = payload?.error as Record<string, unknown> | undefined;
      const detail = payload?.detail;
      throw new MiaApiError(
        response.status,
        String(error?.code ?? 'http_error'),
        String(error?.message ?? detail ?? `HTTP ${response.status}`),
        typeof error?.request_id === 'string' ? error.request_id : undefined,
      );
    }
    return payload as T;
  }
}
