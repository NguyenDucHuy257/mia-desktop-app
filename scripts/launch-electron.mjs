import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);

export function sanitizeElectronEnvironment(source = process.env) {
  const env = { ...source };
  delete env.ELECTRON_RUN_AS_NODE;
  return env;
}

export function launchElectron(args = ['.'], options = {}) {
  const electronExecutable = require('electron');
  const child = spawn(electronExecutable, args, {
    cwd: options.cwd ?? process.cwd(),
    env: sanitizeElectronEnvironment(options.env ?? process.env),
    stdio: 'inherit',
    windowsHide: false,
  });

  const forwardSignal = (signal) => {
    if (!child.killed) child.kill(signal);
  };
  const onSigint = () => forwardSignal('SIGINT');
  const onSigterm = () => forwardSignal('SIGTERM');

  process.once('SIGINT', onSigint);
  process.once('SIGTERM', onSigterm);

  child.once('error', (error) => {
    console.error(`Failed to launch Electron: ${error.message}`);
    process.exitCode = 1;
  });

  child.once('exit', (code, signal) => {
    process.removeListener('SIGINT', onSigint);
    process.removeListener('SIGTERM', onSigterm);

    if (signal) {
      try {
        process.kill(process.pid, signal);
      } catch {
        process.exitCode = 1;
      }
      return;
    }

    process.exitCode = code ?? 1;
  });

  return child;
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
const modulePath = fileURLToPath(import.meta.url);
if (invokedPath === modulePath) {
  const args = process.argv.slice(2);
  launchElectron(args.length ? args : ['.']);
}
