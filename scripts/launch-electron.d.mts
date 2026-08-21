import type { ChildProcess } from 'node:child_process';

export interface ElectronLaunchOptions {
  cwd?: string;
  env?: NodeJS.ProcessEnv;
}

export function sanitizeElectronEnvironment(source?: NodeJS.ProcessEnv): NodeJS.ProcessEnv;

export function launchElectron(
  args?: string[],
  options?: ElectronLaunchOptions,
): ChildProcess;
