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
    cancel(): Promise<{ cancelled: boolean }>;
    targets(request: ArtifactExportRequest): Promise<{ keys: string[]; total: number }>;
    list(request: ArtifactListRequest): Promise<LocalResultPage<ArtifactItem>>;
    coverage(request: ArtifactSnapshotRequest): Promise<ArtifactCoverage>;
    snapshot(request: ArtifactSnapshotRequest): Promise<ArtifactSnapshot>;
    startBatch(request: ArtifactBatchRequest): Promise<{ task_id: string; status: string }>;
    batchStatus(request: { task_id: string }): Promise<ArtifactBatchStatus>;
    batchFailures(request: { task_id: string; connection_id: string; offset?: number; limit?: number }): Promise<ArtifactFailureList>;
    cancelBatch(): Promise<{ cancelled: boolean }>;
    openDirectory(directory: string): Promise<boolean>;
    onExportProgress(listener: (progress: RuntimeExportProgress) => void): () => void;
    onInvoiceProgress(listener: (progress: RuntimeArtifactProgress) => void): () => void;
  };
  preferences: { get(): Promise<LocalPreferences>; set(value: LocalPreferences): Promise<LocalPreferences> };
  logs: {
    list(): Promise<string[]>;
    entries(): Promise<DiagnosticLogEntry[]>;
    clear(): Promise<boolean>;
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

export interface DiagnosticLogEntry {
  id: string;
  timestamp: string;
  level: 'info' | 'warn' | 'error';
  source: string;
  event: string;
  details: string;
}

export type ExcelExportPhase = 'prepare' | 'query' | 'load_template' | 'build_rows' | 'write_rows' | 'format' | 'save' | 'completed';
export interface RuntimeExportProgress {
  status: 'running' | 'completed' | 'failed';
  scope: 'overview' | 'details' | null;
  phase: ExcelExportPhase;
  processed: number;
  total: number;
  percent: number;
}
export interface RuntimeArtifactProgress {
  status: 'running' | 'completed' | 'failed';
  processed: number;
  total: number;
  percent: number;
  artifact_key?: string | null;
  kind?: 'xml' | 'html' | 'pdf' | null;
}

export interface UpdateStatus { phase: 'disabled' | 'idle' | 'checking' | 'available' | 'current' | 'downloading' | 'ready' | 'error'; version: string | null; percent: number; error: string | null }
export interface LocalPreferences { concurrency: number; retries: number; exportFolder: string; pdfConcurrency: number }
export type InvoiceArtifactKind = 'xml' | 'html' | 'pdf';
export interface ArtifactSnapshotRequest { connection_ids: string[]; directions: InvoiceDirection[]; date_from: string; date_to: string }
export interface ArtifactAccountSnapshot { connection_id: string; ready: boolean; missing_ranges: Array<{ date_from: string; date_to: string }>; total: number; cached: Record<InvoiceArtifactKind, number> }
export interface ArtifactCoverageAccount { connection_id: string; ready: boolean; missing_ranges: Array<{ date_from: string; date_to: string }> }
export interface ArtifactCoverage extends ArtifactSnapshotRequest { accounts: ArtifactCoverageAccount[] }
export interface ArtifactSnapshot extends ArtifactSnapshotRequest { accounts: ArtifactAccountSnapshot[] }
export interface ArtifactFormatProgress { status: 'preparing' | 'running' | 'stopping' | 'stopped' | 'completed' | 'failed'; processed: number; total: number; percent: number; current_invoice: string | null; failed: number; skipped?: number }
export interface ArtifactAccountProgress extends ArtifactAccountSnapshot { status: 'ready' | 'not_ready' | 'downloading' | 'completed' | 'error' | 'stopped'; error?: string; failure_count?: number }
export interface ArtifactBatchStatus { task_id: string; status: 'running' | 'completed' | 'failed' | 'stopped'; current_account_id: string | null; accounts: Record<string, ArtifactAccountProgress>; formats: Partial<Record<InvoiceArtifactKind, ArtifactFormatProgress>>; warning_count: number; error?: string | null }
export interface ArtifactFailureRecord { account_id: string; invoice_key: string; date: string; direction: InvoiceDirection; khmshdon: string; khhdon: string; shdon: string; nbmst: string; partner_name: string; affected_formats: InvoiceArtifactKind[]; category: string; message: string }
export interface ArtifactFailureList { task_id: string; connection_id: string; items: ArtifactFailureRecord[]; total: number; offset: number; limit: number }
export interface ArtifactBatchRequest extends ArtifactSnapshotRequest { destination: string; kinds: InvoiceArtifactKind[]; pdf_concurrency: number }
export interface ArtifactExportRequest {
  destination: string;
  connection_ids: string[];
  kinds: Array<'xml' | 'html' | 'pdf' | 'excel'>;
  result_scopes?: Array<'overview' | 'details'>;
  date_from?: string;
  date_to?: string;
  direction?: InvoiceDirection | null;
  query_type?: InvoiceQueryType | null;
  search?: string;
}
export interface ArtifactListRequest { connection_ids: string[]; kind: 'xml' | 'html' | 'pdf'; direction?: InvoiceDirection | null; query_type?: InvoiceQueryType | null; search?: string; cursor?: string | null; limit?: number; date_from?: string; date_to?: string }
export interface ArtifactItem { artifact_id: string; connection_id: string; job_id: string; filename: string; kind: 'xml' | 'html' | 'pdf'; direction: InvoiceDirection | null; size: number; updated_at: number }

export interface ResultQuery {
  connection_id: string;
  cursor?: string | null;
  limit?: number;
  search?: string;
  direction?: InvoiceDirection | null;
  query_type?: InvoiceQueryType | null;
  date_from?: string;
  date_to?: string;
}
export interface SourceResultRow {
  row_id: number | string;
  direction: InvoiceDirection;
  /** Fields projected into the exact source Excel template schema. */
  fields: Record<string, unknown>;
}
export type OverviewResult = SourceResultRow;
export type DetailResult = SourceResultRow;
export interface LocalResultPage<T> {
  items: T[];
  /** Exact column key order read from the source Excel template. */
  columns?: string[];
  /** Exact Vietnamese header text read from the source Excel template. */
  column_labels?: Record<string, string>;
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
  current_artifact?: {
    direction: InvoiceDirection;
    query_type: InvoiceQueryType;
    nbmst: string;
    khhdon: string;
    shdon: string;
    khmshdon: string;
  } | null;
  artifact_progress?: JobStatusResponse['artifact_progress'];
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
