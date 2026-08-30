import type { AccountConnection, CreateJobRequest, InvoiceDirection, InvoiceQueryType, InvoiceSyncState, JobAccepted, JobStatusResponse, JobSummaryResponse } from './api/contracts';

export type LicenseStateName =
  | 'checking' | 'migrating' | 'active' | 'phone_required' | 'legacy_phone_required'
  | 'activation_required' | 'expired' | 'revoked' | 'offline'
  | 'verification_required' | 'error';

export interface LicenseStateResponse {
  state: LicenseStateName;
  active: boolean;
  mode?: string;
  reason?: string | null;
  activation_key?: string | null;
  phone?: string | null;
  details?: {
    license_id?: string;
    device_id?: string;
    canonical_key?: string;
    phone?: string | null;
    phone_status?: 'verified' | 'legacy' | 'pending';
    expires_at?: string | null;
  };
}

export interface LicenseDetails {
  state: LicenseStateName;
  active: boolean;
  phone: string | null;
  phone_status: 'verified' | 'legacy' | 'pending' | null;
  expires_at: string | null;
  device_bound: boolean;
  canonical_key: string | null;
  reason: string | null;
  mode: string | null;
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
  license: {
    status(): Promise<LicenseStateResponse>;
    initialize(): Promise<LicenseStateResponse>;
    submitPhone(phone: string): Promise<LicenseStateResponse>;
    retry(): Promise<LicenseStateResponse>;
    details(): Promise<LicenseDetails>;
    revealKey(): Promise<string | null>;
    updatePhone(phone: string): Promise<LicenseStateResponse>;
  };
  accountConnections: MiaAccountConnectionsBridge;
  jobs: {
    resume(): Promise<PersistedJob | null>;
    resumeAll(): Promise<PersistedJob[]>;
    latestAll(): Promise<PersistedJob[]>;
    syncStates(connectionIds: string[], direction: InvoiceDirection): Promise<InvoiceSyncState[]>;
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
    reconciliation(query: ResultQuery): Promise<LocalResultPage<ReconciliationResult>>;
    facets(query: ResultFacetQuery): Promise<ResultFacetResponse>;
  };
  external: {
    open(url: string): Promise<boolean>;
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
  scope: 'overview' | 'details' | 'reconciliation' | null;
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
  result_scopes?: Array<'overview' | 'details' | 'reconciliation'>;
  date_from?: string;
  date_to?: string;
  direction?: InvoiceDirection | null;
  query_type?: InvoiceQueryType | null;
  search?: string;
  result_filters?: Partial<Record<'overview' | 'details' | 'reconciliation', ResultFilterState>>;
  exclusion?: ResultExclusion;
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
  column_filters?: ColumnFilters;
  exclusion?: ResultExclusion;
  sort?: ResultSort;
}
export type ColumnFilterRule = {
  values?: Array<string | number | boolean | null>;
  search?: string;
  operator?: 'contains' | 'not_contains' | 'starts_with' | 'ends_with' | 'equals' | 'not_equals' | 'gt' | 'gte' | 'lt' | 'lte' | 'number_equals' | 'between';
  value?: string | number | boolean | null;
  value_to?: string | number | boolean | null;
};
export type ColumnFilters = Record<string, ColumnFilterRule>;
export interface ResultSort { column: string; direction: 'asc' | 'desc' }
export interface ResultFilterState { search: string; column_filters: ColumnFilters; sort?: ResultSort }
export interface ResultExclusionRule { kind: 'overview' | 'details'; query: ResultQuery; except_keys?: string[] }
export interface ResultExclusion { keys: string[]; rules: ResultExclusionRule[] }
export interface ResultFacetQuery extends ResultQuery { kind: 'overview' | 'details' | 'reconciliation'; column: string; facet_limit?: number }
export interface ResultFacetResponse { values: unknown[]; truncated: boolean; column_type: 'text' | 'number' | 'percent' }
export interface SourceResultRow {
  row_id: number | string;
  direction: InvoiceDirection;
  /** Fields projected into the exact source Excel template schema. */
  fields: Record<string, unknown>;
  invoice_key: string;
  excluded?: boolean;
}
export type OverviewResult = SourceResultRow;
export type DetailResult = SourceResultRow;
export type ReconciliationResult = SourceResultRow;
export interface ReconciliationSummary {
  selected_overview_invoice_count: number;
  selected_detail_invoice_count: number;
  selected_difference: number;
  overview_invoice_count: number;
  detail_invoice_count: number;
  difference: number;
  missing_detail_count: number;
  missing_overview_count: number;
  money_mismatch_count: number;
  issue_count: number;
  coverage_ranges: Array<{ date_from: string; date_to: string }>;
  uncovered_ranges: Array<{ date_from: string; date_to: string }>;
}
export interface LocalResultPage<T> {
  items: T[];
  /** Exact column key order read from the source Excel template. */
  columns?: string[];
  /** Exact Vietnamese header text read from the source Excel template. */
  column_labels?: Record<string, string>;
  total_count?: number;
  row_count?: number;
  invoice_count?: number;
  aggregate?: { matching_row_count: number; row_count: number; invoice_count: number; totals: Record<string, number | string> };
  column_types?: Record<string, 'text' | 'number' | 'percent'>;
  reconciliation?: ReconciliationSummary;
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
  scope_progress?: JobStatusResponse['scope_progress'];
  progress_totals?: JobStatusResponse['progress_totals'];
  error?: JobStatusResponse['error'];
  event_sequence?: number;
}

declare global {
  interface Window {
    miaRuntime?: MiaRuntimeBridge;
  }
}
