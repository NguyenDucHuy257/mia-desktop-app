const fs = require('node:fs/promises');
const path = require('node:path');
const { sanitizeFields, redactText } = require('./app-logger.cjs');

// Export structured diagnostic fields only, never raw messages or request bodies.
const CODE_FIELDS = new Set(['code', 'reason', 'error_type', 'state', 'mode']);
const NUMBER_FIELDS = new Set(['status', 'candidates', 'schemas', 'account_count', 'successful', 'failed']);
const BOOLEAN_FIELDS = new Set(['valid', 'expired', 'migrated', 'recovered', 'has_key', 'has_device_id', 'has_mst', 'transient', 'active']);
function safeFields(value) {
  const result = {};
  for (const [key, item] of Object.entries(value || {})) {
    if (CODE_FIELDS.has(key) && typeof item === 'string' && /^[a-zA-Z_][a-zA-Z_0-9.-]{0,79}$/.test(item)
        && !/keyv2|^[a-f0-9]{32,}$/i.test(item)) result[key] = item;
    if (NUMBER_FIELDS.has(key) && Number.isFinite(item)) result[key] = item;
    if (BOOLEAN_FIELDS.has(key) && typeof item === 'boolean') result[key] = item;
  }
  return result;
}
async function buildSupportDiagnostics(directory, version, state) {
  const events = [];
  const unavailable = [];
  for (const source of ['electron', 'renderer']) {
    for (const suffix of ['.1', '']) {
      let handle;
      try {
        handle = await fs.open(path.join(directory, 'logs', `${source}.log${suffix}`), 'r');
        const { size } = await handle.stat();
        const start = Math.max(0, size - 2 * 1024 * 1024);
        const buffer = Buffer.alloc(size - start);
        const { bytesRead } = await handle.read(buffer, 0, buffer.length, start);
        const lines = buffer.subarray(0, bytesRead).toString('utf8').split(/\r?\n/);
        if (start) lines.shift();
        for (const line of lines) {
          const match = /^(\d{4}-\d{2}-\d{2}T[\d:.]+Z) (INFO|WARN|ERROR) ([a-z0-9_.-]+) (.*)$/i.exec(line);
          if (!match) continue;
          try { events.push({ timestamp: match[1], source, level: match[2], event: match[3], fields: sanitizeFields(JSON.parse(match[4])) }); }
          catch { /* Ignore incomplete records, never export raw text. */ }
        }
      } catch (error) {
        if (error.code !== 'ENOENT') unavailable.push(source + suffix);
      } finally { await handle?.close(); }
    }
  }
  for (const [source, relative, copies] of [
    ['runtime', ['offline-runtime', 'logs', 'runtime.log'], 2],
    ['crawler', ['offline-runtime', 'logs', 'crawler.log'], 2],
  ]) {
    for (let copy = copies; copy >= 0; copy -= 1) {
      const filename = path.join(directory, ...relative) + (copy ? `.${copy}` : '');
      try {
        const content = await fs.readFile(filename, 'utf8');
        for (const line of content.split(/\r?\n/).slice(-1000)) {
          if (!/(?:^|\s)(?:WARN|WARNING|ERROR)(?:\s|$)/i.test(line)) continue;
          const timestamp = line.match(/^(\d{4}-\d{2}-\d{2}[T ][^\s]+)/)?.[1] || '';
          events.push({ timestamp, source, level: /ERROR/i.test(line) ? 'ERROR' : 'WARN', event: `${source}_diagnostic`, fields: { line: redactText(line) } });
        }
      } catch (error) { if (error.code !== 'ENOENT') unavailable.push(`${source}${copy ? `.${copy}` : ''}`); }
    }
  }
  events.sort((a, b) => a.timestamp.localeCompare(b.timestamp));
  const retained = events.slice(-1500);
  return {
    schema: 3, app: 'MIA TOOL 2026', version, exported_at: new Date().toISOString(),
    scope: 'application_diagnostics', state: sanitizeFields(state), unavailable,
    summary: {
      retained_events: retained.length,
      errors: retained.filter((item) => item.level === 'ERROR').length,
      warnings: retained.filter((item) => item.level === 'WARN').length,
      sources: [...new Set(retained.map((item) => item.source))],
    },
    note: 'Sanitized local diagnostics across Electron, renderer, runtime and crawler. Passwords, OTPs, tokens, cookies, full keys, tax IDs and hardware fingerprint values are excluded or redacted.',
    events: retained,
  };
}
module.exports = { buildSupportDiagnostics, safeFields };

// Account reports deliberately exclude arbitrary runtime log lines. Only the
// structured wire records written by our sanitized crawler instrumentation
// are included, scoped to the selected job, never another account's history.
const { randomUUID } = require('node:crypto');
function accountSnapshot(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new TypeError('invalid_diagnostic_request');
  if (typeof input.connection_id !== 'string' || !/^[\w-]{1,128}$/.test(input.connection_id)) throw new TypeError('invalid_connection_id');
  if (input.job_id != null && (typeof input.job_id !== 'string' || !/^[\w-]{1,128}$/.test(input.job_id))) throw new TypeError('invalid_job_id');
  const result = {};
  for (const key of ['connection_id', 'job_id', 'date_from', 'date_to', 'direction', 'status', 'error_code', 'error_message', 'account_status', 'current_stage', 'overview_ready', 'detail_ready', 'overall_percent']) {
    const value = input[key];
    if (typeof value === 'string' || typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value)) || value === null) result[key] = value;
  }
  for (const key of ['missing_overview_ranges', 'missing_detail_ranges']) {
    if (Array.isArray(input[key])) result[key] = input[key].slice(0, 100).map(range => ({ date_from: String(range?.date_from || '').slice(0, 10), date_to: String(range?.date_to || '').slice(0, 10) }));
  }
  return sanitizeFields(result);
}
async function buildAccountDiagnostics(directory, version, input) {
  const snapshot = accountSnapshot(input);
  const events = [], unavailable = [];
  let malformed_records = 0, truncated = false;
  for (const suffix of ['.2', '.1', '']) {
    let handle;
    try {
      handle = await fs.open(path.join(directory, 'offline-runtime', 'logs', `crawl-diagnostics.log${suffix}`), 'r');
      const { size } = await handle.stat();
      const start = Math.max(0, size - 2 * 1024 * 1024);
      truncated ||= start > 0;
      const buffer = Buffer.alloc(size - start);
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, start);
      const lines = buffer.subarray(0, bytesRead).toString('utf8').split(/\r?\n/);
      if (start) lines.shift();
      for (const line of lines) {
        const marker = line.indexOf(' crawl_diagnostic ');
        if (marker < 0) continue;
        try {
          const record = JSON.parse(line.slice(marker + ' crawl_diagnostic '.length));
          if (!snapshot.job_id || record.job_id !== snapshot.job_id) continue;
          const safe = {};
          for (const key of ['timestamp', 'event', 'job_id', 'stage', 'direction', 'query_type', 'date_from', 'date_to', 'method', 'endpoint', 'host', 'params', 'request_id', 'timeout', 'route', 'request_headers', 'body_fields', 'http_status', 'elapsed_ms', 'response_bytes', 'response_headers', 'response_sha256', 'upstream_error', 'exception_chain', 'error_type', 'frames', 'page_summary', 'generation', 'normal_failures', 'retry_limit', 'consecutive_429', 'cooldown_seconds']) {
            if (record[key] !== undefined) safe[key] = record[key];
          }
          events.push(sanitizeFields(safe));
        } catch { malformed_records += 1; }
      }
    } catch (error) {
      if (error.code !== 'ENOENT') unavailable.push(`crawl-diagnostics.log${suffix}`);
    } finally { await handle?.close(); }
  }
  return {
    schema: 2, app: 'MIA TOOL 2026', version, report_id: randomUUID(),
    exported_at: new Date().toISOString(), scope: 'account_crawl', snapshot,
    history_available: events.length > 0, unavailable, malformed_records,
    truncated: truncated || events.length > 1500,
    note: 'Only retained structured events for this job. Old failures before instrumentation or rotated history cannot be reconstructed. Credentials, CAPTCHA, cookies and successful invoice bodies are excluded; cursor and seller identity are hashed.',
    events: events.slice(-1500),
  };
}
module.exports.buildAccountDiagnostics = buildAccountDiagnostics;
