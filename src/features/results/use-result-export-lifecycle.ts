import { useCallback, useRef, useState } from 'react';
import type { ArtifactExportRequest } from '../../lib/runtime-bridge';
import {
  runResultExports,
  type ExcelExportProgress,
  type ExportLane,
  type ResultExportOwner,
  type ResultExportRunSummary,
} from './result-export-runner';

export type {
  ExcelExportProgress,
  ResultExportFailure,
  ResultExportOwner,
  ResultExportRunSummary,
} from './result-export-runner';

export interface ResultExportLifecycle extends ExcelExportProgress {
  run(owner: ResultExportOwner, requests: ArtifactExportRequest[]): Promise<ResultExportRunSummary>;
}

const INITIAL_PROGRESS: ExcelExportProgress = {
  active: false,
  owner: null,
  accountIndex: 0,
  accountTotal: 0,
  completed: 0,
  scope: null,
  phase: 'prepare',
  processed: 0,
  total: 0,
  percent: 0,
};

/** One app-wide, sequential Excel export lane with backend-sourced progress. */
export function useResultExportLifecycle(): ResultExportLifecycle {
  const [state, setState] = useState<ExcelExportProgress>(INITIAL_PROGRESS);
  const lane = useRef<ExportLane>({ owner: null });

  const run = useCallback(async (owner: ResultExportOwner, requests: ArtifactExportRequest[]) => {
    const artifacts = window.miaRuntime?.artifacts;
    if (!artifacts?.export) {
      const error = new Error('result_export_runtime_unavailable') as Error & { code?: string };
      error.code = 'result_export_runtime_unavailable';
      throw error;
    }
    return runResultExports(owner, requests, artifacts, lane.current, setState);
  }, []);

  return { ...state, run };
}
