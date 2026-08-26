import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { validateExternalUrl } = require('../../electron/external-url-policy.cjs');

describe('external URL IPC policy', () => {
  it('allows public HTTP(S) lookup URLs', () => {
    expect(validateExternalUrl('https://example.com/lookup?a=1')).toBe('https://example.com/lookup?a=1');
    expect(validateExternalUrl('http://example.com')).toBe('http://example.com/');
  });

  it.each(['javascript:alert(1)', 'file:///C:/secret.txt', 'data:text/html,test', 'http://localhost/test', 'http://127.0.0.1/test', 'https://user:pass@example.com'])('rejects unsafe URL %s', (url) => {
    expect(() => validateExternalUrl(url)).toThrow();
  });
});
