import { describe, expect, it } from 'vitest';
import {
  MAX_BULK_ACCOUNTS,
  parseBulkAccounts,
  validateCredentials,
} from '../../src/features/accounts/account-form';

describe('account form validation', () => {
  it('accepts Vietnamese 10-digit and branch tax codes', () => {
    expect(validateCredentials({ username: '0101234567', password: 'secret' })).toEqual({});
    expect(validateCredentials({ username: '001234567890', password: 'secret' })).toEqual({});
    expect(parseBulkAccounts('001234567890|secret').errors).toEqual([]);
    expect(validateCredentials({ username: '0101234567-001', password: 'secret' })).toEqual({});
  });

  it('rejects malformed credentials without returning the password', () => {
    const errors = validateCredentials({ username: 'abc', password: '' });
    expect(errors.username).toContain('10 số');
    expect(errors.password).toContain('mật khẩu');
    expect(JSON.stringify(errors)).not.toContain('abc|');
  });

  it('parses the first separator and preserves separators inside passwords', () => {
    const result = parseBulkAccounts('0101234567|pa|ss\n0309876543|next');
    expect(result.errors).toEqual([]);
    expect(result.entries).toEqual([
      { lineNumber: 1, username: '0101234567', password: 'pa|ss' },
      { lineNumber: 2, username: '0309876543', password: 'next' },
    ]);
  });

  it('reports line numbers and duplicate tax codes', () => {
    const result = parseBulkAccounts('0101234567|one\ninvalid\n0101234567|two');
    expect(result.entries).toHaveLength(1);
    expect(result.errors).toEqual([
      { lineNumber: 2, message: 'Thiếu dấu phân cách |.' },
      { lineNumber: 3, message: 'Mã số thuế bị trùng trong danh sách.' },
    ]);
  });

  it('caps a bulk submission at the documented limit', () => {
    const value = Array.from({ length: MAX_BULK_ACCOUNTS + 1 }, (_, index) => (
      `${String(10_000_000_00 + index).padStart(10, '0')}|password`
    )).join('\n');
    const result = parseBulkAccounts(value);
    expect(result.entries).toHaveLength(MAX_BULK_ACCOUNTS);
    expect(result.errors.at(-1)?.message).toContain(String(MAX_BULK_ACCOUNTS));
  });
});
