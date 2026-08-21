export type DiagnosticLevel = 'info' | 'warn' | 'error';

export function diagnosticLog(event: string, fields: Record<string, unknown> = {}, level: DiagnosticLevel = 'info') {
  const writer = window.miaRuntime?.logs?.write;
  if (!writer) return;
  void writer(level, event, fields).catch(() => undefined);
}
