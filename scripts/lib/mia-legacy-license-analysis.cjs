const crypto = require('node:crypto');

const SCHEMAS = Object.freeze([
  'v1_key29',
  'v2_hash29_phone',
  'v3_full_hash',
  'mak',
  'custom',
]);

function classifyLegacyKey(key) {
  if (/^key[0-9a-f]{29}$/.test(key)) return 'v1_key29';
  if (/^KEY[0-9a-f]{29}0[0-9]{9}$/.test(key)) return 'v2_hash29_phone';
  if (/^key[0-9a-f]{64}$/.test(key)) return 'v3_full_hash';
  if (/^MAK-(?:[A-Za-z0-9]{4}-){4}[A-Za-z0-9]{4}$/.test(key)) return 'mak';
  return 'custom';
}

function parseIsoDate(value) {
  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate()));
  }
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new TypeError('analysis date must use YYYY-MM-DD');
  }
  const [year, month, day] = value.split('-').map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) {
    throw new TypeError('analysis date is invalid');
  }
  return parsed;
}

function expiryState(value, asOf) {
  if (!value) return 'missing';
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
  if (!match) return 'malformed';
  const day = Number(match[1]);
  const month = Number(match[2]);
  const year = Number(match[3]);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  if (parsed.getUTCFullYear() !== year || parsed.getUTCMonth() !== month - 1 || parsed.getUTCDate() !== day) {
    return 'malformed';
  }
  return parsed < asOf ? 'expired' : 'current';
}

function increment(target, key, amount = 1) {
  target[key] = (target[key] || 0) + amount;
}

function percent(numerator, denominator) {
  return denominator > 0 ? Number(((numerator * 100) / denominator).toFixed(2)) : 0;
}

function analyzeLegacyLicenseText(text, options = {}) {
  if (typeof text !== 'string') throw new TypeError('legacy license input must be text');
  const asOf = parseIsoDate(options.asOf || new Date());
  const records = [];
  const schemaCounts = Object.fromEntries(SCHEMAS.map((schema) => [schema, 0]));
  const expiryCounts = { current: 0, expired: 0, malformed: 0, missing: 0 };
  const fieldCountDistribution = {};
  const metadataPhoneBySchema = Object.fromEntries(SCHEMAS.map((schema) => [schema, 0]));

  for (const rawLine of text.split(/\r?\n/)) {
    if (!rawLine.trim()) continue;
    const fields = rawLine.split('|');
    const key = fields[0].trim();
    const schema = classifyLegacyKey(key);
    const state = expiryState(fields.length > 2 ? fields[2].trim() : '', asOf);
    const metadataHasPhone = fields.slice(1).some((field) => /^0[0-9]{9}$/.test(field.trim()));
    const hash29 = schema === 'v1_key29'
      ? key.slice(3).toLowerCase()
      : schema === 'v2_hash29_phone'
        ? key.slice(3, 32).toLowerCase()
        : null;

    increment(schemaCounts, schema);
    increment(expiryCounts, state);
    increment(fieldCountDistribution, String(fields.length));
    if (metadataHasPhone) increment(metadataPhoneBySchema, schema);
    records.push({ rawLine, key, schema, state, hash29 });
  }

  const keyCounts = new Map();
  const rawLines = new Set();
  for (const record of records) {
    keyCounts.set(record.key, (keyCounts.get(record.key) || 0) + 1);
    rawLines.add(record.rawLine);
  }

  const currentHashGroups = new Map();
  for (const record of records) {
    if (!record.hash29 || record.state !== 'current') continue;
    const group = currentHashGroups.get(record.hash29) || [];
    group.push(record);
    currentHashGroups.set(record.hash29, group);
  }

  let uniqueHashGroups = 0;
  let ambiguousHashGroups = 0;
  let rowsInAmbiguousHashGroups = 0;
  const composition = {};
  for (const group of currentHashGroups.values()) {
    if (group.length === 1) uniqueHashGroups += 1;
    else {
      ambiguousHashGroups += 1;
      rowsInAmbiguousHashGroups += group.length;
    }
    const label = [...new Set(group.map((record) => record.schema))].sort().join('+');
    if (!composition[label]) composition[label] = { hash_groups: 0, rows: 0 };
    composition[label].hash_groups += 1;
    composition[label].rows += group.length;
  }

  const total = records.length;
  const currentRecords = expiryCounts.current;
  const commonSchemas = schemaCounts.v1_key29 + schemaCounts.v2_hash29_phone;
  const currentCommonSchemas = records.filter((record) => (
    record.state === 'current'
    && (record.schema === 'v1_key29' || record.schema === 'v2_hash29_phone')
  )).length;

  return {
    source_sha256: crypto.createHash('sha256').update(text, 'utf8').digest('hex'),
    as_of: asOf.toISOString().slice(0, 10),
    total_nonempty_records: total,
    unique_keys: keyCounts.size,
    duplicate_key_groups: [...keyCounts.values()].filter((count) => count > 1).length,
    duplicate_key_rows: total - keyCounts.size,
    exact_duplicate_raw_rows: total - rawLines.size,
    schema_counts: schemaCounts,
    schema_percent: Object.fromEntries(SCHEMAS.map((schema) => [schema, percent(schemaCounts[schema], total)])),
    expiry_counts: expiryCounts,
    field_count_distribution: fieldCountDistribution,
    valid_phone_metadata_by_schema: metadataPhoneBySchema,
    common_v1_v2_schema_count: commonSchemas,
    common_v1_v2_schema_percent: percent(commonSchemas, total),
    current_record_count: currentRecords,
    current_common_v1_v2_count: currentCommonSchemas,
    current_common_v1_v2_percent: percent(currentCommonSchemas, currentRecords),
    current_hash29_mapping: {
      distinct_hash29: currentHashGroups.size,
      unique_hash29: uniqueHashGroups,
      ambiguous_hash29: ambiguousHashGroups,
      rows_in_ambiguous_hash29: rowsInAmbiguousHashGroups,
      conservative_unique_hash_percent_of_current_records: percent(uniqueHashGroups, currentRecords),
      composition,
    },
  };
}

module.exports = {
  analyzeLegacyLicenseText,
  classifyLegacyKey,
  expiryState,
  parseIsoDate,
};
