import { describe, expect, it } from 'vitest';
import { ipcErrorCode, ipcErrorMessage } from '../../src/lib/ipc-error';
import { licenseActionError } from '../../src/features/licensing/LicenseGate';

// Electron's contextBridge drops custom Error properties, so the renderer
// only ever sees the `[code] message` form produced by preload.
function bridgeError(code: string, message = code) {
  return new Error(`[${code}] ${message}`);
}

describe('ipc error helpers', () => {
  it('prefers an explicit code and otherwise parses the preload prefix', () => {
    expect(ipcErrorCode(Object.assign(new Error('x'), { code: 'explicit' }))).toBe('explicit');
    expect(ipcErrorCode(bridgeError('recovery_code_expired'))).toBe('recovery_code_expired');
    expect(ipcErrorCode(new Error('plain failure'))).toBe('internal_error');
    expect(ipcErrorCode(undefined, 'fallback')).toBe('fallback');
  });

  it('strips the prefix from messages', () => {
    expect(ipcErrorMessage(bridgeError('some_code', 'Readable text'))).toBe('Readable text');
    expect(ipcErrorMessage(new Error('Readable text'))).toBe('Readable text');
  });

  it('shows the Vietnamese recovery copy instead of the raw code after crossing the bridge', () => {
    expect(licenseActionError(bridgeError('recovery_code_expired'), 'fallback')).toContain('hết hạn');
    expect(licenseActionError(bridgeError('recovery_code_expired'), 'fallback')).not.toContain('recovery_code_expired');
    expect(licenseActionError(bridgeError('recovery_code_locked'), 'fallback')).toContain('khóa');
    expect(licenseActionError(bridgeError('unmapped_code'), 'Không thể xác nhận mã.')).toBe('Không thể xác nhận mã.');
    expect(licenseActionError(new Error('Server text'), 'fallback')).toBe('Server text');
  });
});
