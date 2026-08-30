import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { afterEach, describe, expect, it, vi } from 'vitest';

const require = createRequire(import.meta.url);
const { buildDeviceEvidence, hashHardwareSignal } = require('../../electron/license/hardware-profile.cjs');
const { buildLegacyDetection, normalizePhone } = require('../../electron/license/legacy-detector.cjs');
const { createLicenseApi, validateServerUrl } = require('../../electron/license/license-api.cjs');
const { LicenseManager, miaV2Key } = require('../../electron/license/license-manager.cjs');
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

function memoryStore(savedLicense: any = null, savedProfile: any = null) {
  let profile: any = savedProfile;
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
    verifyKeyV2: vi.fn(async (payload: any) => ({
      valid: false,
      key: payload.key,
      device_id: payload.device_id,
      phone: payload.phone,
      expired: false,
      migrated: false,
      reason: payload.phone ? 'key_not_activated' : 'phone_required',
    })),
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
      collectEvidence: async () => options.evidence || evidence(),
      logger: { info: vi.fn() },
      now: () => new Date('2026-08-30T00:00:00.000Z'),
    }),
  };
}

function activeResponse(deviceId: string, overrides: Record<string, any> = {}) {
  return {
    valid: true,
    key: miaV2Key(deviceId),
    device_id: deviceId,
    phone: '',
    phone_status: 'pending',
    expires_at: '31/12/2027',
    expired: false,
    migrated: true,
    recovered: false,
    hardware_profile: evidence().hardware,
    reason: 'legacy_migrated',
    ...overrides,
  };
}

describe('MIA shared-key-server client', () => {
  it('hashes six normalized signals and reconstructs exact MIA V1 candidates', () => {
    const result = buildDeviceEvidence({
      system_uuid: ' uuid ', bios_serial: 'bios', baseboard_serial: 'board',
      machine_guid: 'guid', cpu_id: 'cpu', disk_serial: 'disk',
      disks: [{ SerialNumber: ' SERIAL ', Size: '1000' }],
    });
    expect(result.hardware.system_uuid).toBe(hashHardwareSignal('system_uuid', 'UUID'));
    expect(Object.keys(result.hardware)).toHaveLength(6);
    expect(result.legacy.exact_key_candidates[0]).toMatch(/^key[a-f0-9]{29}$/);
  });

  it('builds phone legacy candidates only from a real phone', () => {
    const base = evidence();
    expect(normalizePhone('0000000000')).toBeNull();
    expect(normalizePhone('098 123 4567')).toBe('0981234567');
    const detection = buildLegacyDetection(base, ['0000000000', '0981234567']);
    expect(detection.exact_key_candidates).toContain(`KEY${'b'.repeat(29)}0981234567`);
    expect(JSON.stringify(detection)).not.toContain('0000000000');
  });

  it('posts the Taxsoft-compatible payload only to /verify-key-v2 over HTTPS', async () => {
    expect(() => validateServerUrl('http://gotax.vn')).toThrow(/HTTPS/);
    expect(validateServerUrl('https://gotax.vn').protocol).toBe('https:');
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ valid: false, reason: 'phone_required' }), { status: 200 }));
    const client = createLicenseApi({ baseUrl: 'https://gotax.vn', fetchImpl });
    const payload = { tool: 'MIA', key: 'MIAV2-X', device_id: 'device', phone: '', hardware: {}, legacy_keys: [] };
    await client.verifyKeyV2(payload);
    const call = (fetchImpl as any).mock.calls[0];
    expect(call[0].toString()).toBe('https://gotax.vn/verify-key-v2');
    expect(JSON.parse(call[1].body)).toEqual(payload);
  });

  it('preserves FastAPI string details and identifies a shared server without MIA V2', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      detail: 'verify-key-v2 currently supports GSOFT only',
    }), { status: 400, headers: { 'content-type': 'application/json' } }));
    const client = createLicenseApi({ baseUrl: 'https://gotax.vn', fetchImpl });
    await expect(client.verifyKeyV2({ tool: 'MIA' })).rejects.toMatchObject({
      code: 'mia_v2_not_deployed',
      status: 400,
      transient: false,
      message: 'Shared key server does not support tool=MIA yet',
    });
  });

  it('preserves structured error codes returned by the shared server', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      detail: { code: 'invalid_license_payload', message: 'Invalid payload' },
    }), { status: 422, headers: { 'content-type': 'application/json' } }));
    const client = createLicenseApi({ baseUrl: 'https://gotax.vn', fetchImpl });
    await expect(client.verifyKeyV2({ tool: 'MIA' })).rejects.toMatchObject({
      code: 'invalid_license_payload',
      status: 422,
      message: 'Invalid payload',
    });
  });

  it('logs a sanitized terminal event when license initialization fails', async () => {
    const logger = { info: vi.fn() };
    const setup = manager({
      api: api({ verifyKeyV2: vi.fn(async () => {
        throw Object.assign(new Error('sensitive upstream detail'), {
          name: 'LicenseApiError', code: 'mia_v2_not_deployed', status: 400, transient: false,
        });
      }) }),
    });
    (setup.instance as any).logger = logger;
    expect((await setup.instance.initialize()).reason).toBe('mia_v2_not_deployed');
    expect(logger.info).toHaveBeenCalledWith('license_init_failed', {
      code: 'mia_v2_not_deployed', error_type: 'LicenseApiError', status: 400, transient: false,
    });
    expect(JSON.stringify(logger.info.mock.calls)).not.toContain('sensitive upstream detail');
  });

  it('stores profile and verification state encrypted and fails closed on corruption', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-license-store-'));
    directories.push(directory);
    const protector = {
      encrypt: (value: string) => Buffer.from([...value].reverse().join('')),
      decrypt: (value: Buffer) => [...value.toString()].reverse().join(''),
    };
    const store = createProtectedLicenseStore(directory, protector);
    store.saveProfile({ version: 3, device_id: 'stable' });
    store.saveLicense({ version: 2, canonical_key: 'MIAV2-STABLE' });
    expect(store.loadProfile().device_id).toBe('stable');
    expect(fs.readFileSync(path.join(directory, 'license-state.bin'), 'utf8')).not.toContain('MIAV2-STABLE');
    fs.writeFileSync(path.join(directory, 'license-state.bin'), 'corrupt');
    expect(() => store.loadLicense()).toThrow(/license_state_corrupt/);
  });

  it('migrates an exact V1 license without phone before showing Phone Form', async () => {
    const setup = manager({ api: api({ verifyKeyV2: vi.fn(async (payload: any) => activeResponse(payload.device_id)) }) });
    const state = await setup.instance.initialize();
    expect(state.state).toBe('active');
    const payload = (setup.client.verifyKeyV2 as any).mock.calls[0][0];
    expect(payload.tool).toBe('MIA');
    expect(payload.phone).toBe('');
    expect(payload.legacy_keys).toContain(`key${'b'.repeat(29)}`);
    expect(setup.store.inspect().license.phone_status).toBe('pending');
  });

  it('opens Phone Form only after no exact candidate and keeps a stable activation key', async () => {
    const setup = manager({ evidence: evidence(false) });
    expect((await setup.instance.initialize()).state).toBe('phone_required');
    const pending = await setup.instance.submitPhone('0981234567');
    expect(pending.state).toBe('activation_required');
    const deviceId = setup.store.inspect().profile.device_id;
    expect(pending.activation_key).toBe(miaV2Key(deviceId));
    await setup.instance.retry();
    expect(setup.store.inspect().profile.device_id).toBe(deviceId);
    expect(setup.client.verifyKeyV2).toHaveBeenCalledTimes(2);
  });

  it('uses an existing real profile phone before opening Phone Form', async () => {
    const profile = { version: 3, device_id: 'phone-profile-device', phone: '0981234567', hardware: evidence(false).hardware };
    const setup = manager({ evidence: evidence(false), store: memoryStore(null, profile) });
    const state = await setup.instance.initialize();
    expect(state.state).toBe('activation_required');
    expect((setup.client.verifyKeyV2 as any).mock.calls[0][0].phone).toBe('0981234567');
  });

  it('verifies an existing shared-server profile before attempting migration', async () => {
    const deviceId = '8a6414d6-9298-437e-a568-04e546f134d4';
    const saved = { canonical_key: miaV2Key(deviceId), device_id: deviceId, phone: '0981234567' };
    const profile = { version: 3, device_id: deviceId, phone: '0981234567', hardware: evidence().hardware };
    const verifyKeyV2 = vi.fn(async () => activeResponse(deviceId, { phone: '0981234567', phone_status: 'verified', migrated: false, reason: 'ok' }));
    const setup = manager({ store: memoryStore(saved, profile), api: api({ verifyKeyV2 }) });
    expect((await setup.instance.initialize()).state).toBe('active');
    expect(verifyKeyV2).toHaveBeenCalledOnce();
    expect((verifyKeyV2 as any).mock.calls[0][0].key).toBe(saved.canonical_key);
  });

  it('syncs the canonical device id returned by shared-server hardware recovery', async () => {
    const temporaryId = 'e2fe4915-ed2e-443d-9316-ef58e0bbed5a';
    const canonicalId = 'ec655b34-dd87-4271-ac69-7c46c197e7df';
    const saved = { canonical_key: miaV2Key(canonicalId), device_id: canonicalId, phone: '0981234567' };
    const profile = { version: 3, device_id: temporaryId, phone: '0981234567', hardware: evidence().hardware };
    const setup = manager({
      store: memoryStore(saved, profile),
      api: api({ verifyKeyV2: vi.fn(async () => activeResponse(canonicalId, { phone: '0981234567', recovered: true })) }),
    });
    expect((await setup.instance.initialize()).state).toBe('active');
    expect(setup.store.inspect().profile.device_id).toBe(canonicalId);
  });

  it('never exposes raw Ed25519 or verification state through preload', () => {
    const preload = fs.readFileSync(path.resolve('electron/preload.cjs'), 'utf8');
    expect(preload).not.toContain('getDeviceIdentity:');
    expect(preload).not.toContain('signDeviceChallenge:');
    expect(preload).not.toContain('storeLicenseToken:');
    expect(preload).toContain('license: Object.freeze');
  });
});
