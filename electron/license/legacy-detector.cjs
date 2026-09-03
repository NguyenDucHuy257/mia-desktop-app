const fs = require('node:fs');
const path = require('node:path');

const PHONE_RE = /^0[0-9]{9}$/;
const SUPPORT_PHONES = new Set(['0865219286', '0383466992']);

function normalizePhone(value) {
  const normalized = String(value ?? '').trim().replace(/[\s.()-]+/g, '');
  return PHONE_RE.test(normalized) && normalized !== '0000000000' && !SUPPORT_PHONES.has(normalized)
    ? normalized
    : null;
}

function readLegacyPhones(userDataDirectory, additionalPaths = []) {
  const candidates = [
    path.join(userDataDirectory, 'phone.txt'),
    path.join(userDataDirectory, 'data', 'phone.txt'),
    ...additionalPaths,
  ];
  const phones = [];
  const seen = new Set();
  for (const filename of candidates) {
    try {
      if (!path.isAbsolute(filename) || !fs.existsSync(filename) || fs.statSync(filename).size > 128) continue;
      const phone = normalizePhone(fs.readFileSync(filename, 'utf8'));
      if (phone && !SUPPORT_PHONES.has(phone) && !seen.has(phone)) {
        seen.add(phone);
        phones.push(phone);
      }
    } catch {
      // Missing/unreadable legacy phone evidence is not a migration failure.
    }
  }
  return phones;
}

function buildLegacyDetection(evidence, phones = []) {
  const baseKeys = Array.from(evidence?.legacy?.exact_key_candidates || []);
  const hashes = Array.from(evidence?.legacy?.hash29_candidates || []);
  const validPhones = [...new Set(phones.map(normalizePhone).filter(Boolean))];
  const keys = [...baseKeys];
  for (const hash29 of hashes) {
    for (const phone of validPhones) keys.push(`KEY${hash29}${phone}`);
  }
  return Object.freeze({
    has_any_candidate: hashes.length > 0,
    exact_key_candidates: Object.freeze([...new Set(keys)]),
    hash29_candidates: Object.freeze([...new Set(hashes)]),
    phones: Object.freeze(validPhones),
    detected_schema: Object.freeze([
      ...(hashes.length ? ['mia_v1_disk_hash29'] : []),
      ...(validPhones.length && hashes.length ? ['mia_v2_hash29_phone_observed'] : []),
    ]),
  });
}

module.exports = { SUPPORT_PHONES, buildLegacyDetection, normalizePhone, readLegacyPhones };
