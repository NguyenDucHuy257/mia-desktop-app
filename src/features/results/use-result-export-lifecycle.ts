import { useCallback, useRef, useState } from 'react';
import type { ArtifactExportRequest } from '../../lib/runtime-bridge';

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

export interface ResultExportLifecycle {
  active: boolean;
  owner: ResultExportOwner | null;
  completed: number;
  total: number;
  run(owner: ResultExportOwner, requests: ArtifactExportRequest[]): Promise<ResultExportRunSummary>;
}

function busyError(owner: ResultExportOwner | null) {
  const error = new Error('result_export_busy') as Error & { code?: string };
  error.code = owner === 'bulk' ? 'result_export_busy_bulk' : 'result_export_busy_results';
  return error;
}

/**
 * One app-wide Excel export lane.
 *
 * Source Excel writers are intentionally sequential and template based. Keeping
 * one lane prevents two openpyxl/template writers from racing on the same local
 * runtime while also giving every screen a shared busy state. This is not a
 * crawler worker pool and does not change source crawl concurrency.
 */
export function useResultExportLifecycle(): ResultExportLifecycle {
  const [state, setState] = useState({
    active: false,
    owner: null as ResultExportOwner | null,
    completed: 0,
    total: 0,
  });
  const activeOwner = useRef<ResultExportOwner | null>(null);

  const run = useCallback(async (owner: ResultExportOwner, requests: ArtifactExportRequest[]) => {
    if (activeOwner.current) throw busyError(activeOwner.current);
    if (!requests.length) return { count: 0, files: [], total: 0, completed: 0, failures: [] };
    const artifacts = window.miaRuntime?.artifacts;
    if (!artifacts?.export) {
      const error = new Error('result_export_runtime_unavailable') as Error & { code?: string };
      error.code = 'result_export_runtime_unavailable';
      throw error;
    }

    activeOwner.current = owner;
    setState({ active: true, owner, completed: 0, total: requests.length });
    const summary: ResultExportRunSummary = {
      count: 0,
      files: [],
      total: requests.length,
      completed: 0,
      failures: [],
    };

    try {
      for (const request of requests) {
        try {
          const result = await artifacts.export(request);
          summary.count += Number(result.count || 0);
          summary.files.push(...(result.files || []));
        } catch (error) {
          summary.failures.push({ request, error });
        } finally {
          summary.completed += 1;
          setState({ active: true, owner, completed: summary.completed, total: requests.length });
        }
      }
      return summary;
    } finally {
      activeOwner.current = null;
      setState({ active: false, owner: null, completed: 0, total: 0 });
    }
  }, []);

  return { ...state, run };
}
