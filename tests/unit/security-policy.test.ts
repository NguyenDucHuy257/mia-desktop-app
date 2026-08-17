import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { isTrustedAppUrl } = require('../../electron/security-policy.cjs') as {
  isTrustedAppUrl(
    candidateUrl: string,
    options: { devServerUrl?: string; productionEntryUrl: string },
  ): boolean;
};

describe('Electron URL trust policy', () => {
  const productionEntryUrl = 'file:///opt/mia/dist/index.html';

  it('allows only the exact development origin', () => {
    const options = { devServerUrl: 'http://127.0.0.1:5173', productionEntryUrl };
    expect(isTrustedAppUrl('http://127.0.0.1:5173/settings', options)).toBe(true);
    expect(isTrustedAppUrl('http://127.0.0.1:5173.evil.example/', options)).toBe(false);
    expect(isTrustedAppUrl('https://127.0.0.1:5173/', options)).toBe(false);
  });

  it('allows only the packaged entry file in production', () => {
    const options = { productionEntryUrl };
    expect(isTrustedAppUrl('file:///opt/mia/dist/index.html#settings', options)).toBe(true);
    expect(isTrustedAppUrl('file:///opt/mia/dist/other.html', options)).toBe(false);
    expect(isTrustedAppUrl('https://example.com/', options)).toBe(false);
  });
});
