import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { describe, expect, it, vi } from 'vitest';
import { licenseAllowsWorkspace } from '../../src/features/licensing/LicenseGate';
import type { LicenseStateResponse } from '../../src/lib/runtime-bridge';

const require = createRequire(import.meta.url);
const { isLicenseAccessGranted, licenseRequiredError } = require('../../electron/license/access-control.cjs');
const { LicenseManager } = require('../../electron/license/license-manager.cjs');

const accepted: LicenseStateResponse = { state: 'active', active: true, valid: true, expired: false, reason: 'ok' };

describe('license access control', () => {
  it('accepts only the complete active KEYV2 result', () => {
    expect(isLicenseAccessGranted(accepted)).toBe(true);
    expect(licenseAllowsWorkspace(accepted)).toBe(true);
    for (const changed of [
      { valid: false }, { expired: true }, { reason: 'key_not_activated' },
      { reason: 'legacy_migrated' }, { active: false }, { state: 'error' },
      { valid: undefined }, { expired: undefined }, { reason: undefined },
    ]) {
      const state = { ...accepted, ...changed } as LicenseStateResponse;
      expect(isLicenseAccessGranted(state)).toBe(false);
      expect(licenseAllowsWorkspace(state)).toBe(false);
    }
  });

  it('returns the recognizable business IPC denial', () => {
    expect(licenseRequiredError()).toMatchObject({ name: 'LicenseRequiredError', code: 'LICENSE_REQUIRED', message: 'LICENSE_REQUIRED' });
  });

  it('never treats a disabled license manager as active', async () => {
    const manager = new LicenseManager({
      enabled: false,
      store: {}, api: null, securityDirectory: 'unused',
      ensureIdentity: vi.fn(), collectEvidence: vi.fn(), logger: { info: vi.fn() },
    });
    await expect(manager.initialize()).resolves.toMatchObject({ state: 'error', active: false, reason: 'license_disabled' });
  });

  it('wires packaged startup and every non-license IPC through a fail-closed gate', () => {
    const main = fs.readFileSync(path.resolve('electron/main.cjs'), 'utf8');
    const preload = fs.readFileSync(path.resolve('electron/preload.cjs'), 'utf8');
    expect(main).toContain("const enabled = app.isPackaged || process.env.MIA_LICENSE_V2_ENABLED !== 'false';");
    expect(main).toMatch(/app\.isPackaged\s*\? 'https:\/\/gotax\.vn'/);
    expect(main).toContain("allowInsecureLocalhost: !app.isPackaged && process.env.MIA_LICENSE_ALLOW_INSECURE_LOCALHOST === 'true'");
    expect(main).toContain('if (licenseRequired && !licenseSessionActive) throw licenseRequiredError();');
    expect(main).toContain("assertTrustedSender(event, { licenseRequired: false });");
    expect(main).toContain("await handleLicense('initialize');");
    expect(preload).toContain("error.code = 'LICENSE_REQUIRED'");
  });
});
