import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { parseMkvnProxyText } = require('../../electron/proxy-import-store.cjs');

describe('MKVN proxy import', () => {
  it('ignores the first row and converts data rows to authenticated HTTP proxy URLs', () => {
    const result = parseMkvnProxyText([
      'DỊCH VỤ: đơn hàng mẫu',
      'sp01-vn103.proxy.mkvn.net:10976:userA:passA',
      'sp01-vn101.proxy.mkvn.net:10778:userB:passB',
    ].join('\n'));

    expect(result).toEqual([
      'http://userA:passA@sp01-vn103.proxy.mkvn.net:10976',
      'http://userB:passB@sp01-vn101.proxy.mkvn.net:10778',
    ]);
  });

  it('rejects malformed rows instead of silently importing partial credentials', () => {
    expect(() => parseMkvnProxyText('header\nhost:8080:user')).toThrow('Invalid MKVN proxy row.');
  });
});
