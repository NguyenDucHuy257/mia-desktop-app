const { PythonRuntimeClient, packagedRuntimeExecutable } = require('./python-runtime-client.cjs');

class OfflineRuntimeManager {
  constructor(options) {
    this.options = options;
    this.logger = options.logger;
    this.client = undefined;
    this.startPromise = undefined;
    this.stopped = false;
    this.restartCount = 0;
    this.maxRestarts = options.maxRestarts ?? 2;
  }

  start() {
    if (this.startPromise) return this.startPromise;
    this.stopped = false;
    this.logger?.info('runtime_start_requested', { restart_count: this.restartCount });
    this.startPromise = this.#startClient().catch((error) => {
      this.logger?.error('runtime_start_failed', { code: error?.code, name: error?.name, message: error?.message, stack: error?.stack });
      this.startPromise = undefined;
      throw error;
    });
    return this.startPromise;
  }

  async invoke(method, params = {}, callOptions = {}) {
    await this.start();
    const started = Date.now();
    this.logger?.info('runtime_rpc_start', { method });
    try {
      const result = await this.client.call(method, params, callOptions);
      this.logger?.info('runtime_rpc_end', { method, duration_ms: Date.now() - started, outcome: 'ok' });
      return result;
    } catch (error) {
      this.logger?.warn('runtime_rpc_end', { method, duration_ms: Date.now() - started, outcome: 'error', code: error?.code, message: error?.message });
      if (this.stopped || !['runtime_exited', 'runtime_not_running', 'runtime_write_failed'].includes(error?.code)) throw error;
      await this.#restart();
      const retryStarted = Date.now();
      try {
        const result = await this.client.call(method, params, callOptions);
        this.logger?.info('runtime_rpc_retry_end', { method, duration_ms: Date.now() - retryStarted, outcome: 'ok' });
        return result;
      } catch (retryError) {
        this.logger?.error('runtime_rpc_retry_end', { method, duration_ms: Date.now() - retryStarted, outcome: 'error', code: retryError?.code, message: retryError?.message });
        throw retryError;
      }
    }
  }

  async stop() {
    this.stopped = true;
    this.logger?.info('runtime_stop_requested');
    const client = this.client;
    this.client = undefined;
    this.startPromise = undefined;
    if (client) await client.stop();
  }

  terminateForRecoveryTest() {
    this.client?.terminate();
  }

  async #startClient() {
    const browserPath = this.options.isPackaged
      ? require('node:path').join(this.options.resourcesPath, 'runtime-browsers')
      : require('node:path').join(__dirname, '..', 'runtime', 'browsers');
    const env = { ...this.options.env, PLAYWRIGHT_BROWSERS_PATH: browserPath };
    const clientOptions = this.options.isPackaged
      ? { runtimeExecutable: packagedRuntimeExecutable(this.options.resourcesPath), env, logger: this.logger }
      : { pythonExecutable: this.options.pythonExecutable, runtimeScript: this.options.runtimeScript, env, logger: this.logger };
    const client = new PythonRuntimeClient(clientOptions);
    await client.start();
    const health = await client.call('system.health');
    this.logger?.info('runtime_health', { protocol_version: health.protocol_version, runtime_version: health.runtime_version, pid: health.pid });
    if (health.protocol_version !== '1.0') {
      await client.stop();
      throw new Error('Unsupported offline runtime protocol.');
    }
    await client.call('storage.initialize', { data_dir: this.options.dataDirectory });
    this.client = client;
    return health;
  }

  async #restart() {
    if (this.restartCount >= this.maxRestarts) {
      const error = new Error('Offline runtime restart limit reached.');
      error.code = 'runtime_restart_exhausted';
      this.logger?.error('runtime_restart_exhausted', { restart_count: this.restartCount });
      throw error;
    }
    this.restartCount += 1;
    this.logger?.warn('runtime_restart', { restart_count: this.restartCount });
    await this.client?.stop().catch(() => undefined);
    this.client = undefined;
    this.startPromise = undefined;
    await this.start();
  }
}

module.exports = { OfflineRuntimeManager };
