import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const {
  buildMiaV1Candidates,
  createMiaV1Hash29,
  createMiaV1Key,
  normalizeLegacyDiskSize,
} = require('../../electron/license/legacy-formulas.cjs') as {
  buildMiaV1Candidates(records: Array<{ SerialNumber?: unknown; Size?: unknown }>): Array<{
    schema: string;
    disk_index: number;
    hash29: string;
    exact_key: string;
  }>;
  createMiaV1Hash29(serialNumber: unknown, sizeBytes: unknown): string;
  createMiaV1Key(serialNumber: unknown, sizeBytes: unknown): string;
  normalizeLegacyDiskSize(value: unknown): string;
};

describe('MIA V1 legacy license formula', () => {
  it('matches the confirmed V1.4 serial-plus-size golden fixture byte for byte', () => {
    const serial = 'WD-WCC4E1234567';
    const size = '1000204886016';

    expect(createMiaV1Hash29(serial, size)).toBe('dd495fba95bcee1b7fccbee3d58fe');
    expect(createMiaV1Key(serial, size)).toBe('keydd495fba95bcee1b7fccbee3d58fe');
  });

  it('trims the serial, canonicalizes integer size, and keeps the lowercase key prefix', () => {
    expect(createMiaV1Key('  SERIAL-01  ', 1_000_000)).toBe(
      createMiaV1Key('SERIAL-01', '0001000000'),
    );
    expect(createMiaV1Key('SERIAL-01', 1_000_000)).toMatch(/^key[a-f0-9]{29}$/);
  });

  it('uses the exact legacy N/A fallback for a missing serial', () => {
    expect(createMiaV1Key('', '500107862016')).toBe(createMiaV1Key('N/A', 500_107_862_016n));
  });

  it('preserves provider order so the first valid physical disk is the exact legacy candidate', () => {
    const records = [
      { SerialNumber: 'IGNORED-ZERO', Size: '0' },
      { SerialNumber: ' FIRST ', Size: '1000' },
      { SerialNumber: 'SECOND', Size: '2000' },
    ];

    const candidates = buildMiaV1Candidates(records);

    expect(candidates).toHaveLength(2);
    expect(candidates[0]).toEqual({
      schema: 'mia_v1_disk_hash29',
      disk_index: 1,
      hash29: createMiaV1Hash29('FIRST', '1000'),
      exact_key: createMiaV1Key('FIRST', '1000'),
    });
    expect(candidates[1].disk_index).toBe(2);
  });

  it('rebuilds ordered candidates when Windows disk enumeration order changes', () => {
    const firstOrder = buildMiaV1Candidates([
      { SerialNumber: 'DISK-A', Size: '1000' },
      { SerialNumber: 'DISK-B', Size: '2000' },
    ]);
    const secondOrder = buildMiaV1Candidates([
      { SerialNumber: 'DISK-B', Size: '2000' },
      { SerialNumber: 'DISK-A', Size: '1000' },
    ]);

    expect(firstOrder.map((item) => item.exact_key)).toEqual([
      createMiaV1Key('DISK-A', '1000'),
      createMiaV1Key('DISK-B', '2000'),
    ]);
    expect(secondOrder.map((item) => item.exact_key)).toEqual([
      createMiaV1Key('DISK-B', '2000'),
      createMiaV1Key('DISK-A', '1000'),
    ]);
  });

  it('skips invalid disks and deduplicates equivalent candidates without inventing a license', () => {
    const candidates = buildMiaV1Candidates([
      { SerialNumber: 'DISK-A', Size: null },
      { SerialNumber: 'DISK-A', Size: '-1' },
      { SerialNumber: 'DISK-A', Size: '1000' },
      { SerialNumber: ' DISK-A ', Size: 1000 },
    ]);

    expect(candidates).toEqual([{
      schema: 'mia_v1_disk_hash29',
      disk_index: 2,
      hash29: createMiaV1Hash29('DISK-A', '1000'),
      exact_key: createMiaV1Key('DISK-A', '1000'),
    }]);
  });

  it('rejects unsafe numeric disk sizes rather than hashing a rounded value', () => {
    expect(() => normalizeLegacyDiskSize(Number.MAX_SAFE_INTEGER + 1)).toThrow(/safe integer/);
    expect(normalizeLegacyDiskSize('1000204886016')).toBe('1000204886016');
  });
});
