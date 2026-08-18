import type { JobStatus, JobStatusResponse, JobSummaryResponse } from '../../lib/api/contracts';
import type { PersistedJob } from '../../lib/runtime-bridge';

export const TERMINAL_JOB_STATUSES = new Set<JobStatus>([
  'completed', 'completed_with_warning', 'failed', 'cancelled', 'abandoned',
]);

export interface JobViewState {
  phase: 'idle' | 'starting' | 'polling' | 'retrying' | 'terminal' | 'error';
  record: PersistedJob | null;
  status: JobStatusResponse | null;
  summary: JobSummaryResponse | null;
  retryCount: number;
  message: string | null;
}

export const initialJobState: JobViewState = {
  phase: 'idle', record: null, status: null, summary: null, retryCount: 0, message: null,
};

export type JobAction =
  | { type: 'starting' }
  | { type: 'resumed'; record: PersistedJob }
  | { type: 'accepted'; record: PersistedJob }
  | { type: 'status'; value: JobStatusResponse }
  | { type: 'summary'; value: JobSummaryResponse }
  | { type: 'retry'; count: number }
  | { type: 'error'; message: string }
  | { type: 'dismiss-message' }
  | { type: 'cleared' };

export function clampPercent(value: number) {
  return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0;
}

export function jobReducer(state: JobViewState, action: JobAction): JobViewState {
  switch (action.type) {
    case 'starting': return { ...initialJobState, phase: 'starting' };
    case 'resumed':
    case 'accepted': return { ...state, phase: 'polling', record: action.record, retryCount: 0, message: null };
    case 'status': {
      if (state.status?.job_id === action.value.job_id) {
        const currentSequence = state.status.event_sequence ?? 0;
        const incomingSequence = action.value.event_sequence ?? 0;
        if (incomingSequence < currentSequence || (
          incomingSequence === currentSequence && action.value.updated_at < state.status.updated_at
        )) return state;
      }
      const value = {
        ...action.value,
        overall_percent: clampPercent(action.value.overall_percent),
        current_month: action.value.current_month ? {
          ...action.value.current_month,
          percent: clampPercent(action.value.current_month.percent),
        } : null,
      };
      return { ...state, status: value, phase: TERMINAL_JOB_STATUSES.has(value.status) ? 'terminal' : 'polling', retryCount: 0, message: null };
    }
    case 'summary': return { ...state, summary: action.value };
    case 'retry': return { ...state, phase: 'retrying', retryCount: action.count, message: 'Mất kết nối tạm thời, đang thử lại…' };
    case 'error': return { ...state, phase: 'error', message: action.message };
    case 'dismiss-message': return { ...state, message: null };
    case 'cleared': return initialJobState;
  }
}

export function backoffDelay(attempt: number, baseMs = 2_000, maximumMs = 30_000) {
  return Math.min(maximumMs, baseMs * 2 ** Math.max(0, attempt - 1));
}
