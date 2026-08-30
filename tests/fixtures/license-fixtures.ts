import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { buildDeviceEvidence } = require('../../electron/license/hardware-profile.cjs');

export const FIXTURE_PHONE = '0987654321';
export const FIXTURE_DEVICE_ID = '11111111-2222-3333-4444-555555555555';
export const FIXTURE_DISK_SERIAL = 'TESTDISK-LEGACY-001';
export const FIXTURE_DISK_SIZE = '512110190592';
export const FIXTURE_HASH29 = 'cd9706d13cb09a723cfd576dcdc99';
export const FIXTURE_LEGACY_KEY = `key${FIXTURE_HASH29}`;
export const FIXTURE_PHONE_LEGACY_KEY = `KEY${FIXTURE_HASH29}${FIXTURE_PHONE}`;
export const FIXTURE_V2_KEY = 'KEYV2-20cd0a15bc1ab172b385707877c0f82b-0987654321';

export const FIXTURE_RAW_HARDWARE = Object.freeze({
  system_uuid: 'TEST-SYSTEM-UUID-001',
  bios_serial: 'TEST-BIOS-001',
  baseboard_serial: 'TEST-BOARD-001',
  machine_guid: 'TEST-MACHINE-GUID-001',
  cpu_id: 'TEST-CPU-001',
  disk_serial: FIXTURE_DISK_SERIAL,
});

export function fixtureEvidence({ disks = [{ SerialNumber: FIXTURE_DISK_SERIAL, Size: FIXTURE_DISK_SIZE }], changed = [] as string[] } = {}) {
  const payload: Record<string, unknown> = { ...FIXTURE_RAW_HARDWARE, disks };
  for (const name of changed) payload[name] = `CHANGED-${name}`;
  return buildDeviceEvidence(payload);
}
