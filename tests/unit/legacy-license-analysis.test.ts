import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { analyzeLegacyLicenseText, classifyLegacyKey } = require('../../scripts/lib/mia-legacy-license-analysis.cjs') as {
  analyzeLegacyLicenseText(text: string, options: { asOf: string }): Record<string, any>;
  classifyLegacyKey(key: string): string;
};

describe('MIA legacy license aggregate analyzer', () => {
  it('classifies only source-observed schemas with strict casing and shapes', () => {
    expect(classifyLegacyKey(`key${'a'.repeat(29)}`)).toBe('v1_key29');
    expect(classifyLegacyKey(`KEY${'b'.repeat(29)}0981234567`)).toBe('v2_hash29_phone');
    expect(classifyLegacyKey(`key${'c'.repeat(64)}`)).toBe('v3_full_hash');
    expect(classifyLegacyKey('MAK-AAAA-BBBB-CCCC-DDDD-EEEE')).toBe('mak');
    expect(classifyLegacyKey(`Key${'a'.repeat(29)}`)).toBe('custom');
  });

  it('reports only aggregate data and detects current ambiguous hash mappings', () => {
    const hashA = 'a'.repeat(29);
    const hashB = 'b'.repeat(29);
    const phone = '0981234567';
    const text = [
      `key${hashA}|VIP|31/12/2026|${phone}`,
      `KEY${hashA}${phone}|VIP|31/12/2026|${phone}`,
      `KEY${hashB}0977654321|VIP|31/12/2026|0977654321`,
      `key${'c'.repeat(64)}|VIP|31/12/2025|note`,
      'not-a-supported-key|VIP|bad-date|note',
    ].join('\n');

    const report = analyzeLegacyLicenseText(text, { asOf: '2026-08-30' });
    const serialized = JSON.stringify(report);

    expect(report.total_nonempty_records).toBe(5);
    expect(report.schema_counts).toMatchObject({
      v1_key29: 1,
      v2_hash29_phone: 2,
      v3_full_hash: 1,
      custom: 1,
    });
    expect(report.expiry_counts).toMatchObject({ current: 3, expired: 1, malformed: 1 });
    expect(report.current_hash29_mapping).toMatchObject({
      distinct_hash29: 2,
      unique_hash29: 1,
      ambiguous_hash29: 1,
      rows_in_ambiguous_hash29: 2,
    });
    expect(serialized).not.toContain(phone);
    expect(serialized).not.toContain(`key${hashA}`);
    expect(serialized).not.toContain('not-a-supported-key');
  });

  it('separates duplicate keys from exact duplicate raw rows', () => {
    const key = `key${'d'.repeat(29)}`;
    const report = analyzeLegacyLicenseText([
      `${key}|VIP|31/12/2026|first`,
      `${key}|VIP|31/12/2026|second`,
      `${key}|VIP|31/12/2026|second`,
    ].join('\n'), { asOf: '2026-08-30' });

    expect(report.unique_keys).toBe(1);
    expect(report.duplicate_key_groups).toBe(1);
    expect(report.duplicate_key_rows).toBe(2);
    expect(report.exact_duplicate_raw_rows).toBe(1);
  });
});
