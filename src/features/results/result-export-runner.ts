import type { ArtifactExportRequest, ExcelExportPhase, RuntimeExportProgress } from '../../lib/runtime-bridge';

export type ResultExportOwner = 'bulk' | 'results';

export interface ResultExportFailure {
  request: ArtifactExportRequest;
  error: unknown;
}

export interface ResultExportRunSummary {
  count: number;
  files: string[];
  total: number;
  completed: number;
  failures: ResultExportFailure[];
}

export interface ExcelExportProgress {
  active: boolean;
  owner: ResultExportOwner | null;
  accountIndex: number;
  accountTotal: number;
  completed: number;
  scope: RuntimeExportProgress['scope'];
  phase: ExcelExportPhase;
  processed: number;
  total: number;
  percent: number;
}

export interface ExportLane { owner: ResultExportOwner | null }

interface ArtifactExportGateway {
  export(request: ArtifactExportRequest): Promise<{ count: number; files: string[] }>;
  onExportProgress?(listener: (progress: RuntimeExportProgress) => void): () => void;
}

export function aggregateBulkPercent(completed: number, accountTotal: number, currentPercent: number) {
  if (accountTotal <= 0) return 0;
  const fraction = Math.max(0, Math.min(100, currentPercent)) / 100;
  return Math.max(0, Math.min(100, ((completed + fraction) / accountTotal) * 100));
}

export function busyError(owner: ResultExportOwner | null) {
  const error = new Error('result_export_busy') as Error & { code?: string };
  error.code = owner === 'bulk' ? 'result_export_busy_bulk' : 'result_export_busy_results';
  return error;
}

export async function runResultExports(
  owner: ResultExportOwner,
  requests: ArtifactExportRequest[],
  gateway: ArtifactExportGateway,
  lane: ExportLane,
  onProgress: (progress: ExcelExportProgress) => void,
): Promise<ResultExportRunSummary> {
  if (lane.owner) throw busyError(lane.owner);
  if (!requests.length) return { count: 0, files: [], total: 0, completed: 0, failures: [] };

  lane.owner = owner;
  const summary: ResultExportRunSummary = {
    count: 0,
    files: [],
    total: requests.length,
    completed: 0,
    failures: [],
  };
  let accountIndex = 1;
  let accountPercent = 0;
  let latest: RuntimeExportProgress = {
    status: 'running', scope: null, phase: 'prepare', processed: 0, total: 0, percent: 0,
  };

  const publish = (active: boolean, phase = latest.phase) => onProgress({
    active,
    owner: active ? owner : null,
    accountIndex,
    accountTotal: requests.length,
    completed: summary.completed,
    scope: latest.scope,
    phase,
    processed: latest.processed,
    total: latest.total,
    percent: aggregateBulkPercent(summary.completed, requests.length, accountPercent),
  });

  let unsubscribe: (() => void) | undefined;
  try {
    unsubscribe = gateway.onExportProgress?.((progress) => {
      latest = progress;
      accountPercent = Math.max(accountPercent, Math.max(0, Math.min(100, progress.percent)));
      publish(true);
    });
    publish(true, 'prepare');
    for (let index = 0; index < requests.length; index += 1) {
      accountIndex = index + 1;
      accountPercent = 0;
      latest = {
        status: 'running', scope: null, phase: 'prepare', processed: 0, total: 0, percent: 0,
      };
      publish(true, 'prepare');
      const request = requests[index];
      try {
        const result = await gateway.export(request);
        summary.count += Number(result.count || 0);
        summary.files.push(...(result.files || []));
      } catch (error) {
        summary.failures.push({ request, error });
      } finally {
        summary.completed += 1;
        accountPercent = 0;
        publish(true, index === requests.length - 1 ? 'completed' : 'prepare');
      }
    }
    return summary;
  } finally {
    unsubscribe?.();
    accountIndex = requests.length;
    accountPercent = 0;
    latest = { ...latest, phase: 'completed', processed: 1, total: 1, percent: 100 };
    onProgress({
      active: false,
      owner: null,
      accountIndex: requests.length,
      accountTotal: requests.length,
      completed: summary.completed,
      scope: latest.scope,
      phase: 'completed',
      processed: 1,
      total: 1,
      percent: 100,
    });
    lane.owner = null;
  }
}
