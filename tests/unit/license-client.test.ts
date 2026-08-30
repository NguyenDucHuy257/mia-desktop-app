import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { afterEach, describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { buildDeviceEvidence, hashHardwareSignal } = require('../../electron/license/hardware-profile.cjs');
const { buildLegacyDetection, normalizePhone } = require('../../electron/license/legacy-detector.cjs');
const { validateBaseUrl } = require('../../electron/license/license-api.cjs');
const { LicenseManager } = require('../../electron/license/license-manager.cjs');
const { createProtectedLicenseStore } = require('../../electron/license/protected-license-store.cjs');

const directories: string[] = [];
afterEach(() => {
  for (const directory of directories.splice(0)) fs.rmSync(directory, { recursive: true, force: true });
});

function evidence(withLegacy = true) {
  const hardware = Object.fromEntries([
    'system_uuid', 'bios_serial', 'baseboard_serial', 'machine_guid', 'cpu_id', 'disk_serial',
  ].map((name) => [name, 'a'.repeat(64)]));
  return {
    hardware,
    valid_fields: Object.keys(hardware),
    legacy: {
      exact_key_candidates: withLegacy ? [`key${'b'.repeat(29)}`] : [],
      hash29_candidates: withLegacy ? ['b'.repeat(29)] : [],
      detected_schema: withLegacy ? ['mia_v1_disk_hash29'] : [],
    },
  };
}

function memoryStore(savedLicense: any = null) {
  let profile: any = null;
  let license = savedLicense;
  let migration: any = null;
  return {
    loadProfile: () => profile,
    saveProfile: (value: any) => { profile = structuredClone(value); },
    loadLicense: () => license,
    saveLicense: (value: any) => { license = structuredClone(value); },
    loadMigrationState: () => migration,
    saveMigrationState: (value: any) => { migration = structuredClone(value); },
    inspect: () => ({ profile, license, migration }),
  };
}

function api(overrides: Record<string, any> = {}) {
  return {
    challenge: vi.fn(async ({ action }: { action: string }) => ({ challenge_id: `${action}-challenge-id`, challenge: `${action}-challenge-value-000000000000` })),
    verify: vi.fn(),
    migrate: vi.fn(async () => ({ valid: false, migrated: false, reason: 'no_legacy_match' })),
    activate: vi.fn(async () => ({ valid: false, reason: 'key_not_activated', canonical_key: 'MIAV2-PENDING' })),
    recover: vi.fn(),
    updatePhone: vi.fn(),
    ...overrides,
  };
}

function manager(options: Record<string, any> = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-license-manager-'));
  directories.push(directory);
  const client = options.api || api();
  const store = options.store || memoryStore();
  return {
    client,
    store,
    instance: new LicenseManager({
      enabled: true,
      store,
      api: client,
      securityDirectory: path.join(directory, 'security'),
      ensureIdentity: () => ({ publicKeyPem: 'PUBLIC-KEY', fingerprint: 'f'.repeat(64) }),
      signChallenge: (challenge: string) => `signature:${challenge}`,
      collectEvidence: async () => options.evidence || evidence(),
      logger: { info: vi.fn() },
      now: () => new Date('2026-08-30T00:00:00.000Z'),
    }),
  };
}

describe('MIA license client foundations', () => {
  it('hashes six normalized signals and reconstructs the exact ordered V1 candidate', () => {
    const result = buildDeviceEvidence({
      system_uuid: ' uuid ', bios_serial: 'bios', baseboard_serial: 'board',
      machine_guid: 'guid', cpu_id: 'cpu', disk_serial: 'disk',
      disks: [{ SerialNumber: ' SERIAL ', Size: '1000' }],
    });
    expect(result.hardware.system_uuid).toBe(hashHardwareSignal('system_uuid', 'UUID'));
    expect(Object.keys(result.hardware)).toHaveLength(6);
    expect(result.legacy.exact_key_candidates[0]).toMatch(/^key[a-f0-9]{29}$/);
  });

  it('creates observed phone candidates only from a real normalized phone', () => {
    const base = evidence();
    expect(normalizePhone('0000000000')).toBeNull();
    expect(normalizePhone('098 123 4567')).toBe('0981234567');
    const detection = buildLegacyDetection(base, ['0000000000', '0981234567']);
    expect(detection.exact_key_candidates).toContain(`KEY${'b'.repeat(29)}0981234567`);
    expect(JSON.stringify(detection)).not.toContain('0000000000');
  });

  it('requires HTTPS except an explicitly enabled localhost development endpoint', () => {
    expect(() => validateBaseUrl('http://gotax.vn/license/v2')).toThrow(/HTTPS/);
    expect(validateBaseUrl('https://gotax.vn/license/v2').protocol).toBe('https:');
    expect(validateBaseUrl('http://127.0.0.1:8765/license/v2', true).hostname).toBe('127.0.0.1');
  });

  it('stores encrypted profile/license JSON and fails closed on corruption', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-license-store-'));
    directories.push(directory);
    const protector = {
      encrypt: (value: string) => Buffer.from([...value].reverse().join('')),
      decrypt: (value: Buffer) => [...value.toString()].reverse().join(''),
    };
    const store = createProtectedLicenseStore(directory, protector);
    store.saveProfile({ version: 1, device_id: 'stable' });
    store.saveLicense({ version: 1, license_token: 'opaque' });
    expect(store.loadProfile().device_id).toBe('stable');
    expect(fs.readFileSync(path.join(directory, 'license-token.bin'), 'utf8')).not.toContain('"opaque"');
    fs.writeFileSync(path.join(directory, 'license-token.bin'), 'corrupt');
    expect(() => store.loadLicense()).toThrow(/license_state_corrupt/);
  });

  it('runs legacy migration before Phone Form and admits a V1 user without phone', async () => {
    const active = {
      valid: true, migrated: true, migration_source: 'mia_v1_disk_hash29',
      license_token: 'token', license_id: 'license', device_id: 'device',
      canonical_key: 'MIAV2-STABLE', phone: null, phone_status: 'pending',
      expires_at: '2027-12-31', offline_valid_until: '2026-09-02T00:00:00Z',
    };
    const setup = manager({ api: api({ migrate: vi.fn(async () => active) }) });
    const state = await setup.instance.initialize();
    expect(state.state).toBe('active');
    expect(setup.client.migrate).toHaveBeenCalledOnce();
    expect(setup.client.activate).not.toHaveBeenCalled();
    expect(setup.store.inspect().license.phone_status).toBe('pending');
  });

  it('shows Phone Form only after definitive no-match and keeps activation identity stable', async () => {
    const setup = manager({ evidence: evidence(false) });
    expect((await setup.instance.initialize()).state).toBe('phone_required');
    const pending = await setup.instance.submitPhone('0981234567');
    expect(pending.state).toBe('activation_required');
    const deviceId = setup.store.inspect().profile.device_id;
    await setup.instance.retry();
    expect(setup.store.inspect().profile.device_id).toBe(deviceId);
    expect(setup.client.activate).toHaveBeenCalledTimes(2);
  });

  it('recovers an existing license before creating a new activation', async () => {
    const recovered = {
      valid: true, recovered: true,
      license_token: 'recovered-token', license_id: 'stable-license', device_id: 'canonical-device',
      canonical_key: 'MIAV2-STABLE', phone: '0981234567', phone_status: 'verified',
      expires_at: '2027-12-31', offline_valid_until: '2026-09-02T00:00:00Z',
    };
    const setup = manager({ evidence: evidence(false), api: api({ recover: vi.fn(async () => recovered) }) });
    expect((await setup.instance.initialize()).state).toBe('phone_required');
    const state = await setup.instance.submitPhone('0981234567');
    expect(state.state).toBe('active');
    expect(setup.client.recover).toHaveBeenCalledOnce();
    expect(setup.client.activate).not.toHaveBeenCalled();
    expect(setup.store.inspect().profile.device_id).toBe('canonical-device');
  });

  it('verifies a protected V2 token before any legacy migration and uses a valid offline lease on network failure', async () => {
    const saved = {
      license_token: 'saved-token', license_id: 'license', device_id: 'device',
      canonical_key: 'MIAV2-STABLE', phone: '0981234567', phone_status: 'verified',
      expires_at: '2027-12-31', offline_valid_until: '2026-09-01T00:00:00.000Z',
    };
    const networkError = Object.assign(new Error('offline'), { code: 'license_network_error', transient: true });
    const setup = manager({ store: memoryStore(saved), api: api({ verify: vi.fn(async () => { throw networkError; }) }) });
    const state = await setup.instance.initialize();
    expect(state.state).toBe('offline');
    expect(setup.client.verify).toHaveBeenCalledOnce();
    expect(setup.client.migrate).not.toHaveBeenCalled();
  });

  it('never exposes raw private identity or token methods through preload', () => {
    const preload = fs.readFileSync(path.resolve('electron/preload.cjs'), 'utf8');
    expect(preload).not.toContain('getDeviceIdentity:');
    expect(preload).not.toContain('signDeviceChallenge:');
    expect(preload).not.toContain('storeLicenseToken:');
    expect(preload).toContain('license: Object.freeze');
  });
});
