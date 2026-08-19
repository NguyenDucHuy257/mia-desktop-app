const { PythonRuntimeClient, packagedRuntimeExecutable } = require('./python-runtime-client.cjs');

class OfflineRuntimeManager {
  constructor(options) {
    this.options = options;
    this.client = undefined;
    this.startPromise = undefined;
    this.stopped = false;
    this.restartCount = 0;
    this.maxRestarts = options.maxRestarts ?? 2;
  }

  start() {
    if (this.startPromise) return this.startPromise;
    this.stopped = false;
    this.startPromise = this.#startClient().catch((error) => {
      this.startPromise = undefined;
      throw error;
    });
    return this.startPromise;
  }

  async invoke(method, params = {}, callOptions = {}) {
    await this.start();
    try {
      return await this.client.call(method, params, callOptions);
    } catch (error) {
      if (this.stopped || !['runtime_exited', 'runtime_not_running', 'runtime_write_failed'].includes(error?.code)) throw error;
      await this.#restart();
      return this.client.call(method, params, callOptions);
    }
  }

  async stop() {
    this.stopped = true;
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
      ? { runtimeExecutable: packagedRuntimeExecutable(this.options.resourcesPath), env }
      : { pythonExecutable: this.options.pythonExecutable, runtimeScript: this.options.runtimeScript, env };
    const client = new PythonRuntimeClient(clientOptions);
    await client.start();
    const health = await client.call('system.health');
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
      throw error;
    }
    this.restartCount += 1;
    await this.client?.stop().catch(() => undefined);
    this.client = undefined;
    this.startPromise = undefined;
    await this.start();
  }
}

module.exports = { OfflineRuntimeManager };
