export interface ResultRequestAttempt {
  attempt: number;
  durationMs: number;
  outcome: 'ok' | 'retry' | 'failed';
  code?: string;
  status?: number;
  errorType?: string;
}

const TRANSIENT_RESULT_ERROR_CODES = new Set([
  'runtime_timeout',
  'runtime_not_running',
  'runtime_write_failed',
  'database_locked',
  'database_unavailable',
  'storage_database_failure',
]);

function errorMetadata(error: unknown) {
  const value = error as { code?: unknown; status?: unknown; name?: unknown };
  return {
    code: typeof value?.code === 'string' ? value.code : undefined,
    status: Number.isInteger(value?.status) ? Number(value.status) : undefined,
    errorType: typeof value?.name === 'string' ? value.name : undefined,
  };
}

export function isTransientResultError(error: unknown) {
  const { code, status } = errorMetadata(error);
  return Boolean(
    code && TRANSIENT_RESULT_ERROR_CODES.has(code)
    || status === 408
    || status === 429
    || status !== undefined && status >= 500,
  );
}

export async function requestResultWithRetry<T>(
  request: () => Promise<T>,
  onAttempt: (attempt: ResultRequestAttempt) => void,
  options: { attempts?: number; delayMs?: number } = {},
) {
  const attempts = Math.max(1, Math.min(3, options.attempts ?? 2));
  const delayMs = Math.max(0, options.delayMs ?? 180);
  let lastError: unknown;
  for (let index = 0; index < attempts; index += 1) {
    const attempt = index + 1;
    const started = performance.now();
    try {
      const result = await request();
      onAttempt({ attempt, durationMs: Math.round(performance.now() - started), outcome: 'ok' });
      return result;
    } catch (error) {
      lastError = error;
      const retry = attempt < attempts && isTransientResultError(error);
      onAttempt({
        attempt,
        durationMs: Math.round(performance.now() - started),
        outcome: retry ? 'retry' : 'failed',
        ...errorMetadata(error),
      });
      if (!retry) throw error;
      if (delayMs) await new Promise(resolve => setTimeout(resolve, delayMs * attempt));
    }
  }
  throw lastError;
}

export function coalesceResultRequest<T>(
  inFlight: Map<string, Promise<T>>, key: string, request: () => Promise<T>,
) {
  const existing = inFlight.get(key);
  if (existing) return existing;
  const promise = request().finally(() => {
    if (inFlight.get(key) === promise) inFlight.delete(key);
  });
  inFlight.set(key, promise);
  return promise;
}
