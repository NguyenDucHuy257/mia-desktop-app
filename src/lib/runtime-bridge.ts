import type { AccountConnection, CreateJobRequest, InvoiceDirection, InvoiceQueryType, JobAccepted, JobStatusResponse, JobSummaryResponse } from './api/contracts';

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
    resumeAll(): Promise<PersistedJob[]>;
    latestAll(): Promise<PersistedJob[]>;
    start(intent: CreateJobRequest): Promise<{ record: PersistedJob; accepted: JobAccepted }>;
    status(jobId: string): Promise<JobStatusResponse>;
    summary(jobId: string): Promise<JobSummaryResponse>;
    cancel(jobId: string): Promise<JobStatusResponse>;
    clear(): Promise<void>;
  };
  artifacts: {
    selectDirectory(): Promise<string | null>;
    export(request: ArtifactExportRequest): Promise<{ count: number; files: string[] }>;
    list(request: ArtifactListRequest): Promise<LocalResultPage<ArtifactItem>>;
    openDirectory(directory: string): Promise<boolean>;
  };
  preferences: { get(): Promise<LocalPreferences>; set(value: LocalPreferences): Promise<LocalPreferences> };
  logs: {
    list(): Promise<string[]>;
    write(level: 'info' | 'warn' | 'error', event: string, fields?: Record<string, unknown>): Promise<boolean>;
  };
  updates: {
    status(): Promise<UpdateStatus>;
    check(): Promise<UpdateStatus>;
    download(): Promise<UpdateStatus>;
    install(): Promise<void>;
    setChannel(channel: 'stable' | 'beta'): Promise<UpdateStatus>;
  };
  results: {
    overview(query: ResultQuery): Promise<LocalResultPage<OverviewResult>>;
    details(query: ResultQuery): Promise<LocalResultPage<DetailResult>>;
  };
}

export interface UpdateStatus { phase: 'disabled' | 'idle' | 'checking' | 'available' | 'current' | 'downloading' | 'ready' | 'error'; version: string | null; percent: number; error: string | null }
export interface LocalPreferences { concurrency: number; retries: number }
export interface ArtifactExportRequest {
  destination: string;
  connection_ids: string[];
  kinds: Array<'xml' | 'html' | 'pdf' | 'excel'>;
  result_scopes?: Array<'overview' | 'details'>;
  date_from?: string;
  date_to?: string;
  direction?: InvoiceDirection | null;
  search?: string;
}
export interface ArtifactListRequest { connection_ids: string[]; kind: 'xml' | 'html' | 'pdf'; direction?: InvoiceDirection | null; search?: string; cursor?: string | null; limit?: number; date_from?: string; date_to?: string }
export interface ArtifactItem { artifact_id: string; connection_id: string; job_id: string; filename: string; kind: 'xml' | 'html' | 'pdf'; direction: InvoiceDirection | null; size: number; updated_at: number }

export interface ResultQuery {
  connection_id: string;
  cursor?: string | null;
  limit?: number;
  search?: string;
  direction?: InvoiceDirection | null;
  date_from?: string;
  date_to?: string;
}
export interface SourceResultRow {
  row_id: number | string;
  direction: InvoiceDirection;
  /** Exact public fields read from source SQLite/result reader; no raw/path fields. */
  fields: Record<string, unknown>;
}
export type OverviewResult = SourceResultRow;
export type DetailResult = SourceResultRow;
export interface LocalResultPage<T> {
  items: T[];
  /** Stable public source field order for dynamic result tables. */
  columns?: string[];
  total_count?: number;
  row_count?: number;
  invoice_count?: number;
  pagination: { limit: number; has_more: boolean; next_cursor: string | null };
}

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
  stage_percent?: number;
  current_direction?: InvoiceDirection | null;
  current_query_type?: InvoiceQueryType | null;
  message?: string | null;
  current_month?: JobStatusResponse['current_month'];
  error?: JobStatusResponse['error'];
  event_sequence?: number;
}

declare global {
  interface Window {
    miaRuntime?: MiaRuntimeBridge;
  }
}
