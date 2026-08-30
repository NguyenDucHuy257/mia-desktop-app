const crypto = require('node:crypto');

const MIA_V1_HASH_LENGTH = 29;
const MIA_V1_KEY_PREFIX = 'key';

function normalizeLegacyDiskSerial(value) {
  const serial = value == null ? '' : String(value).trim();
  return serial || 'N/A';
}

function normalizeLegacyDiskSize(value) {
  if (typeof value === 'bigint') {
    if (value <= 0n) throw new TypeError('legacy disk size must be a positive integer');
    return value.toString(10);
  }

  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || value <= 0) {
      throw new TypeError('legacy disk size number must be a positive safe integer');
    }
    return String(value);
  }

  if (typeof value === 'string') {
    const normalized = value.trim();
    if (!/^[0-9]+$/.test(normalized) || BigInt(normalized) <= 0n) {
      throw new TypeError('legacy disk size string must contain a positive integer');
    }
    return BigInt(normalized).toString(10);
  }

  throw new TypeError('legacy disk size must be an integer-compatible value');
}

function createMiaV1Hash29(serialNumber, sizeBytes) {
  const serial = normalizeLegacyDiskSerial(serialNumber);
  const size = normalizeLegacyDiskSize(sizeBytes);
  return crypto
    .createHash('sha256')
    .update(serial + size, 'utf8')
    .digest('hex')
    .slice(0, MIA_V1_HASH_LENGTH);
}

function createMiaV1Key(serialNumber, sizeBytes) {
  return MIA_V1_KEY_PREFIX + createMiaV1Hash29(serialNumber, sizeBytes);
}

function buildMiaV1Candidates(diskRecords) {
  if (!Array.isArray(diskRecords)) throw new TypeError('legacy disk records must be an array');

  const candidates = [];
  const seenKeys = new Set();

  for (let diskIndex = 0; diskIndex < diskRecords.length; diskIndex += 1) {
    const disk = diskRecords[diskIndex];
    if (!disk || typeof disk !== 'object' || Array.isArray(disk)) continue;

    let hash29;
    try {
      hash29 = createMiaV1Hash29(disk.SerialNumber, disk.Size);
    } catch {
      continue;
    }

    const exactKey = MIA_V1_KEY_PREFIX + hash29;
    if (seenKeys.has(exactKey)) continue;
    seenKeys.add(exactKey);
    candidates.push(Object.freeze({
      schema: 'mia_v1_disk_hash29',
      disk_index: diskIndex,
      hash29,
      exact_key: exactKey,
    }));
  }

  return Object.freeze(candidates);
}

module.exports = {
  MIA_V1_HASH_LENGTH,
  MIA_V1_KEY_PREFIX,
  buildMiaV1Candidates,
  createMiaV1Hash29,
  createMiaV1Key,
  normalizeLegacyDiskSerial,
  normalizeLegacyDiskSize,
};
