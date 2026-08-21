import { describe, expect, it } from 'vitest';
import { sanitizeElectronEnvironment } from '../../scripts/launch-electron.mjs';

describe('Electron development launcher environment', () => {
  it('removes ELECTRON_RUN_AS_NODE when inherited with a value', () => {
    const source = { ELECTRON_RUN_AS_NODE: '1', VITE_DEV_SERVER_URL: 'http://127.0.0.1:5173' };
    const env = sanitizeElectronEnvironment(source);

    expect(env).not.toHaveProperty('ELECTRON_RUN_AS_NODE');
    expect(env.VITE_DEV_SERVER_URL).toBe('http://127.0.0.1:5173');
    expect(source.ELECTRON_RUN_AS_NODE).toBe('1');
  });

  it('removes ELECTRON_RUN_AS_NODE even when inherited as an empty string', () => {
    const env = sanitizeElectronEnvironment({ ELECTRON_RUN_AS_NODE: '', PATH: 'test-path' });

    expect(env).not.toHaveProperty('ELECTRON_RUN_AS_NODE');
    expect(env.PATH).toBe('test-path');
  });
});
