import { describe, expect, it, vi } from 'vitest';

// The production implementation is CommonJS because it runs in Electron's
// main process.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { LicenseManager } = require('../../electron/license/license-manager.cjs');
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { createLicenseApi } = require('../../electron/license/license-api.cjs');

function fixture() {
  let profile: Record<string, unknown> = {
    version: 3,
    device_id: 'device-1',
    phone: '0981234567',
    email: null,
    email_verified: false,
    hardware: { cpu: 'a', board: 'b', disk: 'c' },
  };
  const api = {
    requestContactVerification: vi.fn().mockResolvedValue({
      challenge_id: 'challenge-1', masked_email: 'n***@example.com', expires_in: 600,
    }),
    confirmContact: vi.fn().mockResolvedValue({ verified: true, masked_email: 'n***@example.com' }),
    verifyKeyV2: vi.fn().mockResolvedValue({
      valid: true, expired: false, reason: 'ok', key: 'KEYV2-server', device_id: 'device-1',
      phone: '0981234567', hardware_profile: { cpu: 'a', board: 'b', disk: 'c' },
      entitlements: {
        version: 1, plan: 'VIP', trial: false, max_tax_codes: null,
        allowed_tax_codes: [], date_from: null, date_to: null,
      },
    }),
  };
  const store = {
    loadProfile: vi.fn(() => profile),
    saveProfile: vi.fn((value: Record<string, unknown>) => { profile = structuredClone(value); }),
    loadLicense: vi.fn(() => ({ canonical_key: 'KEYV2-existing', phone: '0981234567' })),
    saveLicense: vi.fn(),
    loadFirstUseDate: vi.fn(() => '2026-09-23'),
    saveMigrationState: vi.fn(),
  };
  const manager = new LicenseManager({
    enabled: true,
    api,
    store,
    currentVersion: '4.1.1',
    securityDirectory: 'C:\\MIA\\security',
    ensureIdentity: () => ({ public_key: 'unused' }),
    collectEvidence: async () => ({ hardware: { cpu: 'a', board: 'b', disk: 'c' } }),
    requireRecoveryEmail: true,
  });
  return { api, manager, profile: () => profile };
}

describe('license contact recovery binding', () => {
  it('sends every recovery action through the existing verify-key-v2 URL', async () => {
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    const fetchImpl = vi.fn(async (url: URL, init: RequestInit) => {
      calls.push({ url: String(url), body: JSON.parse(String(init.body)) });
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    });
    const api = createLicenseApi({ baseUrl: 'https://gotax.vn', fetchImpl });
    const payload = { tool: 'MIA', key: 'key', device_id: 'device', phone: '0981234567', hardware: {} };

    await api.requestContactVerification({ ...payload, email: 'owner@example.com' });
    await api.confirmContact({ ...payload, challenge_id: 'challenge', code: '123456' });
    await api.requestPasswordReset(payload);
    await api.verifyPasswordReset({ ...payload, challenge_id: 'challenge', code: '123456' });

    expect(calls.map((call) => new URL(call.url).pathname)).toEqual(Array(4).fill('/verify-key-v2'));
    expect(calls.map((call) => call.body.action)).toEqual([
      'contact_request', 'contact_confirm', 'password_reset_request', 'password_reset_verify',
    ]);
  });

  it('only promotes the email associated with the confirmed challenge', async () => {
    const subject = fixture();
    const requested = await subject.manager.requestContactVerification('0981234567', 'New@Example.com');
    expect(requested.challenge_id).toBe('challenge-1');
    expect(subject.profile()).toMatchObject({
      pending_email: 'new@example.com', pending_contact_challenge: 'challenge-1', email_verified: false,
    });

    await expect(subject.manager.confirmContact('wrong-challenge', '123456')).rejects.toMatchObject({
      code: 'recovery_code_invalid',
    });
    expect(subject.api.confirmContact).not.toHaveBeenCalled();

    const state = await subject.manager.confirmContact('challenge-1', '123456');
    expect(state.state).toBe('active');
    expect(subject.profile()).toMatchObject({ email: 'new@example.com', email_verified: true });
    expect(subject.profile()).not.toHaveProperty('pending_contact_challenge');
  });

  it('does not replace a pending email when sending the new challenge fails', async () => {
    const subject = fixture();
    await subject.manager.requestContactVerification('0981234567', 'first@example.com');
    subject.api.requestContactVerification.mockRejectedValueOnce(Object.assign(new Error('SMTP down'), {
      code: 'recovery_email_unavailable',
    }));

    await expect(subject.manager.requestContactVerification('0981234567', 'second@example.com')).rejects.toMatchObject({
      code: 'recovery_email_unavailable',
    });
    expect(subject.profile()).toMatchObject({
      pending_email: 'first@example.com', pending_contact_challenge: 'challenge-1',
    });
  });
});
