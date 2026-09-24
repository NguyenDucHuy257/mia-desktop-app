'use strict';

const fs = require('node:fs');
const path = require('node:path');

const MAX_LOG_BYTES = 2 * 1024 * 1024;
const MAX_FIELD_LENGTH = 800;
const SENSITIVE_KEY = /(password|token|secret|authorization|cookie|session|credential|api[_-]?key)/i;
const PRIVATE_IDENTIFIER_KEY = /^(?:key|canonical_key|activation_key|legacy_keys|public_key|email|target_email|phone|device_id|hardware|hardware_profile)$/i;
const SENSITIVE_TEXT = /(password|token|secret|authorization|cookie|session|credential|api[_-]?key)["']?\s*[=:]\s*(?:["'][^"']*["']|[^\s,;}\]]+)/gi;

function redactText(value) {
  return String(value)
    .replace(/\b\d{10,14}\b/g, '[redacted-id]')
    .replace(SENSITIVE_TEXT, '$1=[redacted]')
    .slice(0, MAX_FIELD_LENGTH);
}

function sanitizeFields(value, depth = 0) {
  if (depth > 3) return '[truncated]';
  if (value === null || value === undefined || typeof value === 'boolean' || typeof value === 'number') return value;
  if (typeof value === 'string') return redactText(value);
  if (Array.isArray(value)) return value.slice(0, 20).map((item) => sanitizeFields(item, depth + 1));
  if (typeof value !== 'object') return redactText(value);
  const output = {};
  for (const [key, item] of Object.entries(value).slice(0, 40)) {
    output[key] = SENSITIVE_KEY.test(key) || PRIVATE_IDENTIFIER_KEY.test(key)
      ? '[redacted]'
      : sanitizeFields(item, depth + 1);
  }
  return output;
}

function rotate(filename) {
  try {
    if (!fs.existsSync(filename) || fs.statSync(filename).size < MAX_LOG_BYTES) return;
    const backup = `${filename}.1`;
    fs.rmSync(backup, { force: true });
    fs.renameSync(filename, backup);
  } catch {
    // Diagnostics must never crash the application.
  }
}

function createDiagnosticLogger(directory, filename) {
  const target = path.join(directory, filename);
  return Object.freeze({
    write(level, event, fields = {}) {
      try {
        fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
        rotate(target);
        const safeEvent = /^[a-z0-9_.-]{1,80}$/i.test(String(event)) ? String(event) : 'invalid_event';
        const payload = JSON.stringify(sanitizeFields(fields));
        fs.appendFileSync(target, `${new Date().toISOString()} ${String(level).toUpperCase()} ${safeEvent} ${payload}\n`, { encoding: 'utf8', mode: 0o600 });
      } catch {
        // Logging is best-effort and cannot affect product behavior.
      }
    },
    info(event, fields) { this.write('info', event, fields); },
    warn(event, fields) { this.write('warn', event, fields); },
    error(event, fields) { this.write('error', event, fields); },
  });
}

module.exports = { createDiagnosticLogger, redactText, sanitizeFields };
