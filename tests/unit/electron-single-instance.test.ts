import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('Electron desktop process ownership', () => {
  it('lets only the primary instance start the local runtime and focuses it on subsequent launches', () => {
    const source = readFileSync(new URL('../../electron/main.cjs', import.meta.url), 'utf8');

    expect(source).toContain('const hasSingleInstanceLock = app.requestSingleInstanceLock();');
    expect(source).toMatch(/if \(!hasSingleInstanceLock\) \{\s*app\.quit\(\);\s*\} else \{/);
    expect(source).toContain("app.on('second-instance'");
    expect(source).toMatch(/second-instance[\s\S]*window\.show\(\);[\s\S]*window\.focus\(\);/);
    expect(source).toMatch(/if \(!hasSingleInstanceLock\) \{\s*app\.quit\(\);\s*\} else \{[\s\S]*app\.whenReady\(\)/);
    expect(source).toMatch(/if \(data\?\.unlocked\) \{\s*await ensureOfflineRuntimeStarted\(\);\s*offlineAuthSessionActive = true;/);
    expect(source).not.toMatch(/if \(verifiesAccess && isLicenseAccessGranted\(data\)\) \{\s*await ensureOfflineRuntimeStarted\(\)/);
  });
});
