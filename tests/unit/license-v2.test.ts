import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  FIXTURE_DEVICE_ID, FIXTURE_DISK_SERIAL, FIXTURE_DISK_SIZE, FIXTURE_HASH29,
  FIXTURE_LEGACY_KEY, FIXTURE_PHONE, FIXTURE_PHONE_LEGACY_KEY, FIXTURE_V2_KEY,
  fixtureEvidence,
} from '../fixtures/license-fixtures';
import { activeResponse, FakeLicenseServer } from '../helpers/fake-license-server';

const require = createRequire(import.meta.url);
const { createMiaV1Hash29, createMiaV1Key, buildMiaV1Candidates } = require('../../electron/license/legacy-formulas.cjs');
const { createLicenseApi } = require('../../electron/license/license-api.cjs');
const { LicenseManager, miaV2Key } = require('../../electron/license/license-manager.cjs');
const { createProtectedLicenseStore } = require('../../electron/license/protected-license-store.cjs');

const directories: string[] = [];
afterEach(() => {
  for (const directory of directories.splice(0)) fs.rmSync(directory, { recursive: true, force: true });
});

function memoryStore(savedLicense: any = null, savedProfile: any = null) {
  let profile = savedProfile ? structuredClone(savedProfile) : null;
  let license = savedLicense ? structuredClone(savedLicense) : null;
  let migration: any = null;
  return {
    loadProfile: () => structuredClone(profile),
    saveProfile: (value: any) => { profile = structuredClone(value); },
    loadLicense: () => structuredClone(license),
    saveLicense: (value: any) => { license = structuredClone(value); },
    loadMigrationState: () => structuredClone(migration),
    saveMigrationState: (value: any) => { migration = structuredClone(value); },
    inspect: () => ({ profile, license, migration }),
  };
}

function createHarness(options: Record<string, any> = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-license-v2-'));
  directories.push(directory);
  const store = options.store || memoryStore();
  const server = options.server || new FakeLicenseServer((request) => ({
    ...request, valid: false, expired: false, migrated: false, reason: 'key_not_activated',
  }));
  const instance = new LicenseManager({
    enabled: true,
    store,
    api: server,
    securityDirectory: path.join(directory, 'security'),
    ensureIdentity: () => ({ publicKeyPem: 'TEST-PUBLIC', fingerprint: 'f'.repeat(64) }),
    collectEvidence: async () => options.evidence || fixtureEvidence(),
    createDeviceId: options.createDeviceId || (() => FIXTURE_DEVICE_ID),
    legacyPhonePaths: options.legacyPhonePaths || [],
    logger: options.logger || { info: vi.fn() },
    now: () => new Date('2026-08-30T00:00:00.000Z'),
  });
  return { directory, instance, server, store };
}

function profile(deviceId = FIXTURE_DEVICE_ID, phone = FIXTURE_PHONE, evidence = fixtureEvidence()) {
  return { version: 3, device_id: deviceId, phone, hardware: evidence.hardware };
}

function savedLicense(deviceId = FIXTURE_DEVICE_ID, phone = FIXTURE_PHONE) {
  return { version: 2, canonical_key: miaV2Key(deviceId, phone), device_id: deviceId, phone };
}

describe('MIA License V2 full synthetic acceptance', () => {
  it('fails closed when an older server omits policy and preserves the existing key', async () => {
    const server = new FakeLicenseServer((request) => activeResponse(request, { entitlements: undefined }));
    const store = memoryStore(savedLicense(), profile());
    const harness = createHarness({ server, store });
    expect(await harness.instance.initialize()).toMatchObject({ state: 'error', active: false, reason: 'license_policy_missing' });
    expect(store.inspect().license.canonical_key).toBe(FIXTURE_V2_KEY);
  });

  it('persists trial scope on migration and re-verifies it on restart', async () => {
    const entitlements = { version: 1, plan: 'TEST1', trial: true, max_tax_codes: 1, allowed_tax_codes: ['0123456789'], date_from: '2026-08-01', date_to: '2026-08-31' };
    const server = new FakeLicenseServer((request) => activeResponse(request, { migrated: true, entitlements }));
    const store = memoryStore(savedLicense(), profile());
    expect((await createHarness({ server, store }).instance.initialize()).entitlements).toEqual(entitlements);
    expect(store.inspect().license.entitlements).toEqual(entitlements);
    expect((await createHarness({ server, store }).instance.initialize()).entitlements).toEqual(entitlements);
  });
  it('A: reconstructs the exact V1 fixture and waits locally for legacy phone', async () => {
    expect(createMiaV1Hash29(FIXTURE_DISK_SERIAL, FIXTURE_DISK_SIZE)).toBe(FIXTURE_HASH29);
    expect(createMiaV1Key(FIXTURE_DISK_SERIAL, FIXTURE_DISK_SIZE)).toBe(FIXTURE_LEGACY_KEY);
    const harness = createHarness();
    expect((await harness.instance.initialize()).state).toBe('legacy_phone_required');
    expect(harness.server.requests).toHaveLength(0);
  });

  it('B/D: submits exact KEYV2 plus both exact legacy schemas and persists migration', async () => {
    const server = new FakeLicenseServer((request) => activeResponse(request, { migrated: true, reason: 'legacy_migrated' }));
    const harness = createHarness({ server });
    await harness.instance.initialize();
    expect((await harness.instance.submitPhone(FIXTURE_PHONE)).state).toBe('active');
    const request = server.requests[0];
    expect(request.key).toBe(FIXTURE_V2_KEY);
    expect(request.legacy_keys).toContain(FIXTURE_LEGACY_KEY);
    expect(request.legacy_keys).toContain(FIXTURE_PHONE_LEGACY_KEY);
    expect(Object.keys(request.hardware)).toHaveLength(6);
    expect(harness.store.inspect().license).toMatchObject({
      canonical_key: FIXTURE_V2_KEY, device_id: FIXTURE_DEVICE_ID, phone: FIXTURE_PHONE,
      expires_at: '31/12/2028', source: 'legacy_migration',
    });
  });

  it('C: silently migrates legacy evidence when a real local phone exists', async () => {
    const phoneFile = path.join(createHarness().directory, 'phone.txt');
    fs.writeFileSync(phoneFile, FIXTURE_PHONE, 'utf8');
    const server = new FakeLicenseServer((request) => activeResponse(request, { migrated: true, reason: 'legacy_migrated' }));
    const harness = createHarness({ server, legacyPhonePaths: [phoneFile] });
    expect((await harness.instance.initialize()).state).toBe('active');
    expect(server.requests).toHaveLength(1);
    expect(server.requests[0].legacy_keys).toContain(FIXTURE_PHONE_LEGACY_KEY);
  });

  it('E/F: new customer receives stable pending key then becomes active after admin activation', async () => {
    const noLegacy = { ...fixtureEvidence(), legacy: { exact_key_candidates: [], hash29_candidates: [], detected_schema: [] } };
    const server = new FakeLicenseServer((request) => ({ ...request, valid: false, expired: false, migrated: false, reason: 'key_not_activated' }));
    const harness = createHarness({ server, evidence: noLegacy });
    expect((await harness.instance.initialize()).state).toBe('phone_required');
    const pending = await harness.instance.submitPhone(FIXTURE_PHONE);
    expect(pending).toMatchObject({ state: 'activation_required', activation_key: FIXTURE_V2_KEY });
    server.respondWith((request) => activeResponse(request));
    expect((await harness.instance.retry()).state).toBe('active');
    expect(server.requests[0].key).toBe(server.requests[1].key);
  });

  it('G/H: restart or app update preserves device, phone and canonical key', async () => {
    const server = new FakeLicenseServer((request) => activeResponse(request));
    const store = memoryStore(savedLicense(), profile());
    const first = createHarness({ server, store });
    expect((await first.instance.initialize()).state).toBe('active');
    const second = createHarness({ server, store, createDeviceId: () => 'must-not-be-used' });
    expect((await second.instance.initialize()).state).toBe('active');
    expect(server.requests.map((request) => request.key)).toEqual([FIXTURE_V2_KEY, FIXTURE_V2_KEY]);
    expect(store.inspect().profile.device_id).toBe(FIXTURE_DEVICE_ID);
  });

  it('M: 2/6 hardware rejection requires verification and does not overwrite canonical profile', async () => {
    const original = profile();
    const store = memoryStore(savedLicense(), original);
    const server = new FakeLicenseServer((request) => ({
      ...request, valid: false, expired: false, reason: 'hardware_mismatch_below_50_percent',
    }));
    const changed = ['system_uuid', 'bios_serial', 'baseboard_serial', 'machine_guid'];
    const harness = createHarness({ server, store, evidence: fixtureEvidence({ changed }) });
    expect((await harness.instance.initialize()).state).toBe('verification_required');
    expect(store.inspect().profile.device_id).toBe(FIXTURE_DEVICE_ID);
    expect(store.inspect().license.canonical_key).toBe(FIXTURE_V2_KEY);
  });

  it('N: lost profile recovery synchronizes the old canonical device and key', async () => {
    const canonicalDevice = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
    const canonicalKey = miaV2Key(canonicalDevice, FIXTURE_PHONE);
    const store = memoryStore({ ...savedLicense(), canonical_key: canonicalKey, device_id: canonicalDevice }, null);
    const server = new FakeLicenseServer((request) => activeResponse(request, {
      migrated: false, recovered: true, reason: 'recovered_existing_device', key: canonicalKey, device_id: canonicalDevice,
    }));
    const harness = createHarness({ server, store });
    expect((await harness.instance.initialize()).state).toBe('active');
    expect(server.requests[0].device_id).toBe(FIXTURE_DEVICE_ID);
    expect(store.inspect().profile.device_id).toBe(canonicalDevice);
    expect(store.inspect().license.canonical_key).toBe(canonicalKey);
    expect(store.inspect().license.source).toBe('device_recovery');
  });

  it('O/P: wrong phone or wrong machine cannot become active from phone knowledge alone', async () => {
    const noLegacy = { ...fixtureEvidence(), legacy: { exact_key_candidates: [], hash29_candidates: [], detected_schema: [] } };
    for (const phone of ['0911111111', FIXTURE_PHONE]) {
      const server = new FakeLicenseServer((request) => ({ ...request, valid: false, expired: false, reason: 'key_not_activated' }));
      const harness = createHarness({ server, evidence: noLegacy });
      await harness.instance.initialize();
      expect((await harness.instance.submitPhone(phone)).state).toBe('activation_required');
    }
  });

  it('Q/R: active and legacy expiry responses both fail closed', async () => {
    for (const reason of ['expired', 'legacy_key_expired']) {
      const server = new FakeLicenseServer((request) => ({
        ...request, valid: false, expired: true, expires_at: '01/01/2025', reason,
      }));
      const harness = createHarness({ server, store: memoryStore(null, profile()) });
      expect((await harness.instance.initialize()).state).toBe('expired');
      expect(harness.store.inspect().license).toBeNull();
    }
  });

  it('accepts only the documented successful shared-server reasons', async () => {
    for (const reason of [
      'ok', 'interim_v2_upgraded', 'recovered_existing_device',
      'legacy_already_migrated', 'legacy_migrated',
    ]) {
      const server = new FakeLicenseServer((request) => activeResponse(request, {
        migrated: reason.startsWith('legacy_') || reason === 'interim_v2_upgraded',
        recovered: reason === 'recovered_existing_device',
        reason,
      }));
      const harness = createHarness({ server, store: memoryStore(savedLicense(), profile()) });
      expect(await harness.instance.initialize()).toMatchObject({ state: 'active', active: true });
    }
  });

  it('rejects valid=true for unknown, expired, or incomplete success responses', async () => {
    for (const response of [
      { valid: true, expired: false, reason: 'key_not_activated' },
      { valid: true, expired: true, reason: 'ok' },
      { valid: true, reason: 'ok' },
    ]) {
      const server = new FakeLicenseServer((request) => ({ ...request, ...response }));
      const store = memoryStore(savedLicense(), profile());
      const harness = createHarness({ server, store });
      expect(await harness.instance.initialize()).toMatchObject({ state: 'error', active: false, reason: 'invalid_response' });
    }
  });

  it('S: invalid and dummy phones never reach the server', async () => {
    const noLegacy = { ...fixtureEvidence(), legacy: { exact_key_candidates: [], hash29_candidates: [], detected_schema: [] } };
    for (const value of ['', '123', 'abcdefghij', '0000000000', '09876']) {
      const harness = createHarness({ evidence: noLegacy });
      await harness.instance.initialize();
      await expect(harness.instance.submitPhone(value)).rejects.toMatchObject({ code: 'invalid_phone' });
      expect(harness.server.requests).toHaveLength(0);
    }
  });

  it('T/U/V/W: HTTP rejection, network, timeout and invalid JSON preserve local identity and fail closed', async () => {
    const cases = [
      new Response(JSON.stringify({ detail: 'Invalid v2 key for device_id/phone' }), { status: 400 }),
      new TypeError('connection refused'),
      Object.assign(new Error('aborted'), { name: 'AbortError' }),
      new Response('not-json', { status: 200 }),
    ];
    const expected = ['license_request_failed', 'license_network_error', 'license_timeout', 'invalid_response'];
    for (let index = 0; index < cases.length; index += 1) {
      const value = cases[index];
      const api = createLicenseApi({
        baseUrl: 'https://gotax.vn', timeoutMs: 10,
        fetchImpl: vi.fn(async () => { if (value instanceof Error) throw value; return value; }),
      });
      const store = memoryStore(savedLicense(), profile());
      const harness = createHarness({ server: api, store });
      const state = await harness.instance.initialize();
      expect(state).toMatchObject({ state: 'error', reason: expected[index] });
      expect(store.inspect().profile.device_id).toBe(FIXTURE_DEVICE_ID);
      expect(store.inspect().license.canonical_key).toBe(FIXTURE_V2_KEY);
    }
  });

  it('X/Y: corrupt protected profile and license state are rejected and managers fail closed', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-license-corrupt-'));
    directories.push(directory);
    const protector = { encrypt: (value: string) => Buffer.from(value), decrypt: (value: Buffer) => value.toString('utf8') };
    const store = createProtectedLicenseStore(directory, protector);
    fs.mkdirSync(directory, { recursive: true });
    fs.writeFileSync(path.join(directory, 'device-profile.bin'), '{bad-json');
    expect(() => store.loadProfile()).toThrowError(expect.objectContaining({ code: 'device_profile_corrupt' }));
    fs.writeFileSync(path.join(directory, 'license-state.bin'), '{bad-json');
    expect(() => store.loadLicense()).toThrowError(expect.objectContaining({ code: 'license_state_corrupt' }));

    const profileFailure = createHarness({
      store: { ...memoryStore(), loadProfile: () => { throw Object.assign(new Error('bad profile'), { code: 'device_profile_corrupt' }); } },
    });
    expect(await profileFailure.instance.initialize()).toMatchObject({ state: 'error', reason: 'device_profile_corrupt' });
    expect(profileFailure.server.requests).toHaveLength(0);

    const licenseFailure = createHarness({
      store: { ...memoryStore(null, profile()), loadLicense: () => { throw Object.assign(new Error('bad license'), { code: 'license_state_corrupt' }); } },
    });
    expect(await licenseFailure.instance.initialize()).toMatchObject({ state: 'error', reason: 'license_state_corrupt' });
    expect(licenseFailure.server.requests).toHaveLength(0);
  });

  it('Z: disk enumeration changes still include the exact legacy disk fixture', () => {
    const candidates = buildMiaV1Candidates([
      { SerialNumber: 'NEW-WRONG-DISK', Size: '1000000' },
      { SerialNumber: FIXTURE_DISK_SERIAL, Size: FIXTURE_DISK_SIZE },
    ]);
    expect(candidates.map((item: any) => item.exact_key)).toContain(FIXTURE_LEGACY_KEY);
  });

  it('generates one stable key 100 times and changes display key when phone changes', () => {
    expect(new Set(Array.from({ length: 100 }, () => miaV2Key(FIXTURE_DEVICE_ID, FIXTURE_PHONE)))).toEqual(new Set([FIXTURE_V2_KEY]));
    expect(miaV2Key(FIXTURE_DEVICE_ID, '0911111111')).not.toBe(FIXTURE_V2_KEY);
  });
});
