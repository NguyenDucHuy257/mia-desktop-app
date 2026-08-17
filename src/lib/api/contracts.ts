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
  force_refresh?: boolean;
  refresh_latest_month?: boolean;
  result_scope?: 'overview' | 'detail';
  include_xml?: boolean;
  include_mvt?: boolean;
}

export interface JobAccepted {
  job_id: string;
  status: string;
  current_stage: string | null;
  worker_slot_id: string | null;
}

export interface JobStatusResponse {
  job_id: string;
  status: JobStatus;
  stage: string | null;
  overall_percent: number;
  current_month: null | {
    key: string;
    index: number;
    total: number;
    processed: number;
    planned: number;
    percent: number;
  };
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
