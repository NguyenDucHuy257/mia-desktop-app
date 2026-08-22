'use strict';

const { spawn } = require('node:child_process');
const path = require('node:path');

const DEFAULT_MAX_MESSAGE_BYTES = 1024 * 1024;
const STDERR_TAIL_BYTES = 4096;
const SAFE_ENV_NAMES = [
  'PATH', 'Path', 'PATHEXT', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP',
  'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'HOME', 'LANG',
];
const SAFE_RUNTIME_ENV_NAMES = [
  'MIA_RUNTIME_DATA_DIR', 'MIA_RUNTIME_LOG_LEVEL', 'PLAYWRIGHT_BROWSERS_PATH',
  'MIA_SESSION_ENCRYPTION_KEY', 'MIA_SESSION_ENCRYPTION_KEY_ID',
];

class RuntimeProtocolError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'RuntimeProtocolError';
    this.code = code;
  }
}

function sanitizeRuntimeStderr(value) {
  return String(value || '')
    .replace(/\b\d{10,14}\b/g, '[redacted-id]')
    .replace(/(password|token|secret|authorization|cookie|session|credential|api[_-]?key)\s*[=:]\s*\S+/gi, '$1=[redacted]')
    .slice(-STDERR_TAIL_BYTES)
    .trim();
}

function runtimeEnvironment(source = process.env, additions = {}) {
  const result = { PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' };
  for (const name of SAFE_ENV_NAMES) {
    if (typeof source[name] === 'string') result[name] = source[name];
  }
  for (const name of SAFE_RUNTIME_ENV_NAMES) {
    if (typeof additions[name] === 'string') result[name] = additions[name];
  }
  return result;
}

const EXPORT_PROGRESS_PHASES = new Set([
  'prepare', 'query', 'load_template', 'build_rows', 'write_rows',
  'format', 'save', 'completed',
]);

function validateRuntimeNotification(message) {
  if (!message || message.jsonrpc !== '2.0') return null;
  const value = message.params;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (message.method === 'artifact.progress') {
    const allowed = new Set(['status', 'processed', 'total', 'percent', 'artifact_key']);
    if (Object.keys(value).some((key) => !allowed.has(key))) return null;
    if (!['running', 'completed', 'failed'].includes(value.status)) return null;
    if (!Number.isInteger(value.processed) || value.processed < 0) return null;
    if (!Number.isInteger(value.total) || value.total < 0) return null;
    if (typeof value.percent !== 'number' || !Number.isFinite(value.percent) || value.percent < 0 || value.percent > 100) return null;
    if (value.artifact_key !== undefined && value.artifact_key !== null && (typeof value.artifact_key !== 'string' || value.artifact_key.length > 512)) return null;
    return Object.freeze({ status: value.status, processed: value.processed, total: value.total, percent: value.percent, artifact_key: value.artifact_key ?? null });
  }
  if (message.method !== 'export.progress') return null;
  const allowed = new Set(['status', 'scope', 'phase', 'processed', 'total', 'percent']);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  if (!['running', 'completed', 'failed'].includes(value.status)) return null;
  if (![null, 'overview', 'details'].includes(value.scope ?? null)) return null;
  if (!EXPORT_PROGRESS_PHASES.has(value.phase)) return null;
  if (!Number.isInteger(value.processed) || value.processed < 0) return null;
  if (!Number.isInteger(value.total) || value.total < 0) return null;
  if (typeof value.percent !== 'number' || !Number.isFinite(value.percent) || value.percent < 0 || value.percent > 100) return null;
  return Object.freeze({
    status: value.status,
    scope: value.scope ?? null,
    phase: value.phase,
    processed: value.processed,
    total: value.total,
    percent: value.percent,
  });
}

function defaultRuntimeScript() {
  return path.join(__dirname, '..', 'runtime', 'python', 'mia_runtime.py');
}

function packagedRuntimeExecutable(resourcesPath = process.resourcesPath) {
  return path.join(resourcesPath, 'runtime', 'mia-runtime.exe');
}

class PythonRuntimeClient {
  constructor(options = {}) {
    this.pythonExecutable = options.pythonExecutable || process.env.MIA_PYTHON_EXECUTABLE || 'python';
    this.runtimeScript = options.runtimeScript || defaultRuntimeScript();
    this.runtimeExecutable = options.runtimeExecutable;
    this.defaultTimeoutMs = options.defaultTimeoutMs || 5000;
    this.shutdownTimeoutMs = options.shutdownTimeoutMs || 1000;
    this.maxMessageBytes = options.maxMessageBytes || DEFAULT_MAX_MESSAGE_BYTES;
    this.logger = options.logger;
    this.child = undefined;
    this.nextId = 1;
    this.pending = new Map();
    this.stdoutBuffer = Buffer.alloc(0);
    this.stderrTail = '';
    this.stopping = false;
    this.extraEnv = options.env || {};
    this.onNotification = options.onNotification;
  }

  async start() {
    if (this.child) return;
    const executable = this.runtimeExecutable || this.pythonExecutable;
    const args = this.runtimeExecutable ? [] : ['-E', '-u', this.runtimeScript];
    this.stderrTail = '';
    this.logger?.info('python_runtime_spawn', { executable, packaged: Boolean(this.runtimeExecutable), cwd: this.runtimeExecutable ? path.dirname(this.runtimeExecutable) : path.dirname(this.runtimeScript) });
    const child = spawn(executable, args, {
      cwd: this.runtimeExecutable ? path.dirname(this.runtimeExecutable) : path.dirname(this.runtimeScript),
      env: runtimeEnvironment(process.env, this.extraEnv),
      shell: false,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.child = child;
    this.stopping = false;
    child.stdout.on('data', (chunk) => this.#handleStdout(chunk));
    child.stderr.on('data', (chunk) => {
      const message = sanitizeRuntimeStderr(chunk.toString('utf8'));
      if (!message) return;
      this.stderrTail = sanitizeRuntimeStderr(`${this.stderrTail}\n${message}`);
      this.logger?.error('python_runtime_stderr', { message });
    });
    child.once('error', (error) => {
      this.logger?.error('python_runtime_process_error', { name: error?.name, message: error?.message, stack: error?.stack });
      this.#handleExit(new RuntimeProtocolError('runtime_spawn_failed', error.message));
    });
    child.once('close', (code, signal) => {
      this.logger?.warn('python_runtime_closed', { code, signal, stopping: this.stopping });
      const diagnostics = this.stderrTail ? ` Python stderr: ${this.stderrTail}` : '';
      this.#handleExit(new RuntimeProtocolError(
        'runtime_exited',
        `Runtime exited unexpectedly (code=${code ?? 'n/a'}, signal=${signal ?? 'n/a'}).${diagnostics}`,
      ));
    });
    await new Promise((resolve, reject) => {
      child.once('spawn', () => {
        this.logger?.info('python_runtime_spawned', { pid: child.pid });
        resolve();
      });
      child.once('error', reject);
    });
  }

  async call(method, params = {}, options = {}) {
    if (!this.child || this.child.exitCode !== null || this.child.killed) {
      throw new RuntimeProtocolError('runtime_not_running', 'Runtime is not running.');
    }
    if (typeof method !== 'string' || !/^[a-z][a-z0-9_.-]{0,127}$/i.test(method)) {
      throw new TypeError('Invalid runtime method.');
    }
    const id = this.nextId++;
    const encoded = Buffer.from(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`, 'utf8');
    if (encoded.length > this.maxMessageBytes) {
      throw new RuntimeProtocolError('request_too_large', 'Runtime request exceeds the message limit.');
    }
    const timeoutMs = options.timeoutMs || this.defaultTimeoutMs;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.logger?.warn('python_runtime_timeout', { method, request_id: id, timeout_ms: timeoutMs });
        reject(new RuntimeProtocolError('runtime_timeout', 'Runtime request timed out.'));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, method });
      this.child.stdin.write(encoded, (error) => {
        if (!error) return;
        clearTimeout(timer);
        this.pending.delete(id);
        this.logger?.error('python_runtime_write_failed', { method, request_id: id, message: error.message });
        reject(new RuntimeProtocolError('runtime_write_failed', 'Could not write to runtime.'));
      });
    });
  }

  async stop() {
    const child = this.child;
    if (!child) return;
    this.stopping = true;
    if (child.exitCode === null && !child.killed) {
      await this.call('system.shutdown', {}, { timeoutMs: this.shutdownTimeoutMs }).catch(() => undefined);
    }
    if (child.exitCode === null && !child.killed) child.kill();
    await new Promise((resolve) => {
      if (child.exitCode !== null) return resolve();
      child.once('close', resolve);
      setTimeout(resolve, this.shutdownTimeoutMs).unref();
    });
    this.child = undefined;
    this.stopping = false;
  }

  terminate() {
    if (this.child && this.child.exitCode === null) this.child.kill();
  }

  #handleStdout(chunk) {
    this.stdoutBuffer = Buffer.concat([this.stdoutBuffer, chunk]);
    if (this.stdoutBuffer.length > this.maxMessageBytes && !this.stdoutBuffer.includes(10)) {
      this.#protocolFailure('response_too_large', 'Runtime response exceeds the message limit.');
      return;
    }
    let newline;
    while ((newline = this.stdoutBuffer.indexOf(10)) !== -1) {
      const line = this.stdoutBuffer.subarray(0, newline);
      this.stdoutBuffer = this.stdoutBuffer.subarray(newline + 1);
      if (line.length > this.maxMessageBytes) {
        this.#protocolFailure('response_too_large', 'Runtime response exceeds the message limit.');
        return;
      }
      this.#handleLine(line);
    }
  }

  #handleLine(line) {
    let message;
    try {
      message = JSON.parse(line.toString('utf8'));
    } catch {
      this.#protocolFailure('invalid_response', 'Runtime returned invalid JSON.');
      return;
    }
    if (message && message.jsonrpc === '2.0' && !Object.hasOwn(message, 'id')) {
      const notification = validateRuntimeNotification(message);
      if (!notification) {
        this.#protocolFailure('invalid_response', 'Runtime returned an invalid JSON-RPC notification.');
        return;
      }
      try {
        this.onNotification?.(message.method, notification);
      } catch (error) {
        this.logger?.warn('python_runtime_notification_handler_failed', { method: message.method, name: error?.name });
      }
      return;
    }
    if (!message || message.jsonrpc !== '2.0' || !Number.isInteger(message.id)) {
      this.#protocolFailure('invalid_response', 'Runtime returned an invalid JSON-RPC response.');
      return;
    }
    const pending = this.pending.get(message.id);
    if (!pending) return;
    clearTimeout(pending.timer);
    this.pending.delete(message.id);
    if (message.error) {
      this.logger?.warn('python_runtime_rpc_error', { method: pending.method, request_id: message.id, code: message.error.code, message: message.error.message });
      pending.reject(new RuntimeProtocolError(message.error.code, message.error.message || 'Runtime request failed.'));
    } else if (Object.hasOwn(message, 'result')) {
      pending.resolve(message.result);
    } else {
      pending.reject(new RuntimeProtocolError('invalid_response', 'Runtime response has no result.'));
    }
  }

  #protocolFailure(code, message) {
    this.logger?.error('python_runtime_protocol_failure', { code, message });
    const error = new RuntimeProtocolError(code, message);
    this.#rejectPending(error);
    this.terminate();
  }

  #handleExit(error) {
    this.#rejectPending(error);
    if (!this.stopping) this.child = undefined;
  }

  #rejectPending(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}

module.exports = {
  DEFAULT_MAX_MESSAGE_BYTES,
  PythonRuntimeClient,
  RuntimeProtocolError,
  defaultRuntimeScript,
  packagedRuntimeExecutable,
  runtimeEnvironment,
  sanitizeRuntimeStderr,
  validateRuntimeNotification,
};
