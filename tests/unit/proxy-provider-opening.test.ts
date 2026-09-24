import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const root = path.resolve(import.meta.dirname, '../..');

describe('proxy provider purchase link', () => {
  it('opens the centralized affiliate URL in the operating-system default browser', () => {
    const main = readFileSync(path.join(root, 'electron/main.cjs'), 'utf8');
    const preload = readFileSync(path.join(root, 'electron/preload.cjs'), 'utf8');
    const config = readFileSync(path.join(root, 'electron/proxy-provider-config.cjs'), 'utf8');

    expect(config).toContain('https://proxy.mkvn.net/aff/contactdh257');
    expect(main).toContain("shell.openExternal(validateExternalUrl(PROXY_PROVIDER.purchaseUrl))");
    expect(main).not.toContain('BrowserProfileService');
    expect(preload).toContain("invokeIpc('mia:proxy-provider:open-purchase-page')");
    expect(preload).not.toContain('browserProfile');
  });
});
