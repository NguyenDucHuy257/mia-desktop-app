import type { CreateJobRequest, JobStatusResponse } from '../../lib/api/contracts';

export const MAX_BATCH_ACCOUNTS = 50;
export const DEFAULT_BATCH_CONCURRENCY = 2;

export function normalizeBatch(intents: CreateJobRequest[]) {
  if (intents.length > MAX_BATCH_ACCOUNTS) throw new Error('batch_too_large');
  const seen = new Set<string>();
  return intents.filter((intent) => {
    if (seen.has(intent.connection_id)) return false;
    seen.add(intent.connection_id);
    return true;
  });
}

export function aggregateProgress(statuses: Array<JobStatusResponse | undefined>) {
  if (statuses.length === 0) return 0;
  return Math.max(0, Math.min(100, Math.floor(statuses.reduce((sum, status) => sum + (status?.overall_percent ?? 0), 0) / statuses.length)));
}
