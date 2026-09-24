import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('initial persisted account loading', () => {
  it('waits for the local runtime before serving the first account list', () => {
    const main = readFileSync('electron/main.cjs', 'utf8');
    const handler = main.slice(
      main.indexOf("ipcMain.handle('mia:account-connections:list'"),
      main.indexOf("ipcMain.handle('mia:account-connections:get'"),
    );

    expect(handler).toContain('async (event)');
    expect(handler).toContain('await ensureOfflineRuntimeStarted()');
    expect(handler).toContain('return localAccounts().list()');
  });

  it('retries the first renderer load and refreshes when the app becomes visible', () => {
    const app = readFileSync('src/App.tsx', 'utf8');

    expect(app).toContain('loadInitialAccounts');
    expect(app).toContain('attempt < 9');
    expect(app).toContain("document.addEventListener('visibilitychange', refreshWhenVisible)");
    expect(app).toContain("window.addEventListener('focus', refreshWhenVisible)");
  });
});
