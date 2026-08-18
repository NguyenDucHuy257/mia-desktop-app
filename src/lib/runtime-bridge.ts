import type { AccountConnection, CreateJobRequest, JobAccepted, JobStatusResponse, JobSummaryResponse } from './api/contracts';

export interface MiaDeviceIdentity {
  algorithm: 'Ed25519';
  publicKeyPem: string;
  fingerprint: string;
}

export interface MiaAccountCredentials {
  username: string;
  password: string;
}

export interface MiaAccountConnectionsBridge {
  create(credentials: MiaAccountCredentials): Promise<AccountConnection>;
  list(): Promise<AccountConnection[]>;
  get(connectionId: string): Promise<AccountConnection>;
  reconnect(connectionId: string, credentials: MiaAccountCredentials): Promise<AccountConnection>;
  revoke(connectionId: string): Promise<void>;
}

export interface MiaRuntimeBridge {
  platform: string;
  getDeviceIdentity(): Promise<MiaDeviceIdentity>;
  signDeviceChallenge(challenge: string): Promise<string>;
  storeLicenseToken(token: string): Promise<boolean>;
  accountConnections: MiaAccountConnectionsBridge;
  jobs: {
    resume(): Promise<PersistedJob | null>;
    start(intent: CreateJobRequest): Promise<{ record: PersistedJob; accepted: JobAccepted }>;
    status(jobId: string): Promise<JobStatusResponse>;
    summary(jobId: string): Promise<JobSummaryResponse>;
    cancel(jobId: string): Promise<JobStatusResponse>;
    clear(): Promise<void>;
  };
  results: {
    overview(query: ResultQuery): Promise<LocalResultPage<OverviewResult>>;
    details(query: ResultQuery): Promise<LocalResultPage<DetailResult>>;
  };
}

export interface ResultQuery { connection_id: string; cursor?: string | null; limit?: number; search?: string; direction?: 'purchase' | 'sold' | null }
export interface OverviewResult { overview_id: number; direction: 'purchase' | 'sold'; business_key: string; payload: Record<string, unknown> }
export interface DetailResult { detail_id: number; direction: 'purchase' | 'sold'; business_key: string; line_key: string; payload: Record<string, unknown> }
export interface LocalResultPage<T> { items: T[]; pagination: { limit: number; has_more: boolean; next_cursor: string | null } }

export interface PersistedJob {
  job_id: string | null;
  connection_id: string;
  intent: CreateJobRequest;
  idempotency_key: string;
  created_at: string;
  updated_at: string;
  status?: string;
  stage?: string | null;
  overall_percent?: number;
  current_month?: JobStatusResponse['current_month'];
  error?: JobStatusResponse['error'];
  event_sequence?: number;
}

declare global {
  interface Window {
    miaRuntime?: MiaRuntimeBridge;
  }
}
