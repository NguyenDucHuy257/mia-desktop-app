// Electron's contextBridge only preserves `message` on Error objects that cross
// from preload into the renderer. Preload therefore prefixes every public code
// as `[code] message`. Always resolve the code through this helper so UI copy
// is looked up by code instead of falling back to the raw identifier.
const CODE_PREFIX = /^\[([A-Za-z0-9_.:-]+)\]\s*/;

export function ipcErrorCode(error: unknown, fallback = 'internal_error'): string {
  const value = error as { code?: unknown; message?: unknown } | null | undefined;
  if (typeof value?.code === 'string' && value.code) return value.code;
  const message = typeof value?.message === 'string' ? value.message : '';
  return CODE_PREFIX.exec(message)?.[1] ?? fallback;
}

export function ipcErrorMessage(error: unknown): string {
  const value = error as { message?: unknown } | null | undefined;
  const message = typeof value?.message === 'string' ? value.message : '';
  return message.replace(CODE_PREFIX, '');
}
