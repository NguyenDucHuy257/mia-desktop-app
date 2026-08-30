export type InvoiceDirection = 'purchase' | 'sold';
export type InvoiceQueryType = 'query' | 'sco-query';
export type JobStatus =
  | 'queued'
  | 'waiting_account'
  | 'running'
  | 'cancelling'
  | 'completed'
  | 'completed_with_warning'
  | 'failed'
  | 'cancelled'
  | 'abandoned';

export interface AccountConnection {
  connection_id: string;
  username: string;
  company_name?: string | null;
  status: string;
  token_generation: number;
  created_at: string;
  updated_at: string;
  reused: boolean;
}

export interface CreateJobRequest {
  connection_id: string;
  date_from: string;
  date_to: string;
  directions: InvoiceDirection[];
  query_types: InvoiceQueryType[];
  scopes?: Array<'overview' | 'detail'>;
  data_types?: Array<'invoice' | 'xml' | 'html' | 'pdf'>;
  force_refresh?: boolean;
  refresh_latest_month?: boolean;
  sync_mode?: 'new' | 'supplement';
  result_scope?: 'overview' | 'detail';
  include_xml?: boolean;
  include_mvt?: boolean;
}

export interface InvoiceSyncState {
  connection_id: string;
  direction: InvoiceDirection;
  status: 'not_synced' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  current_month: string | null;
  current_until?: string | null;
  sync_from: string | null;
  sync_until: string | null;
  invoice_count: number;
  detail_invoice_count: number;
  baseline_invoice_count: number | null;
  added_invoice_count: number | null;
  replaced_old_count?: number | null;
  downloaded_new_count?: number | null;
  last_job_id: string | null;
  sync_mode: 'new' | 'supplement' | null;
}

/** Local JSON-RPC acceptance envelope. No server worker-slot concept exists. */
export interface JobAccepted {
  job_id: string;
  status: string;
  current_stage: string | null;
}

export interface JobStatusResponse {
  job_id: string;
  status: JobStatus;
  stage: string | null;
  overall_percent: number;
  /** Source stage_progress_percent. Renderer may format it but never derives it. */
  stage_percent?: number;
  /** Exact source unit currently executing; counters remain source-owned. */
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
  artifact_progress?: {
    current_key: string;
    processed: number;
    completed_xml: number;
    completed_html: number;
    items: Record<string, { xml: 'running' | 'completed' | 'failed'; html: 'running' | 'completed' | 'failed' }>;
  } | null;
  /** Raw source progress_state.message token used as presentation input. */
  message?: string | null;
  event_sequence?: number;
  current_month: null | {
    key: string;
    index: number;
    total: number;
    processed: number;
    planned: number;
    percent: number;
  };
  scope_progress?: { scope: string; processed: number; total: number } | null;
  progress_totals?: Record<string, { processed: number; total: number }>;
  updated_at: string;
  error: null | { code: string; message: string; retryable: boolean };
}

export interface JobSummaryResponse {
  job_id: string;
  status: string;
  warning_count: number;
  stages: Array<{
    stage: string;
    status: string;
    progress_percent: number;
  }>;
  coverage_plan: Record<string, unknown>;
  work: Record<string, unknown>;
  post_processing: Record<string, unknown>;
}

export interface ResultPage<T> {
  items: T[];
  total_count?: number;
  row_count?: number;
  invoice_count?: number;
  pagination: { limit: number; has_more: boolean; next_cursor: string | null };
}
