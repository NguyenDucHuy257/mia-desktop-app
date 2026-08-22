export type DiagnosticLevel = 'info' | 'warn' | 'error';

let installed = false;

export function diagnosticLog(event: string, fields: Record<string, unknown> = {}, level: DiagnosticLevel = 'info') {
  const writer = window.miaRuntime?.logs?.write;
  if (!writer) return;
  void writer(level, event, fields).catch(() => undefined);
}

export function installRendererDiagnostics() {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('error', (event) => {
    diagnosticLog('renderer_uncaught_error', {
      message: event.message,
      filename: event.filename,
      line: event.lineno,
      column: event.colno,
      stack: event.error instanceof Error ? event.error.stack : undefined,
    }, 'error');
  });
  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason;
    diagnosticLog('renderer_unhandled_rejection', {
      name: reason instanceof Error ? reason.name : undefined,
      message: reason instanceof Error ? reason.message : String(reason),
      stack: reason instanceof Error ? reason.stack : undefined,
    }, 'error');
  });
}
