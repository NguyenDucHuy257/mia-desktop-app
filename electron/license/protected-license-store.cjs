const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const PROFILE_FILE = 'device-profile.bin';
const LICENSE_FILE = 'license-state.bin';
const MIGRATION_FILE = 'migration-state.json';
const FIRST_USE_FILE = 'first-use.txt';

function atomicWrite(filename, content, mode = 0o600) {
  fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
  const temporary = `${filename}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`;
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, 'wx', mode);
    fs.writeFileSync(descriptor, content);
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor);
    descriptor = undefined;
    fs.renameSync(temporary, filename);
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function parseJson(text, code) {
  try {
    const value = JSON.parse(text);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    return value;
  } catch {
    const error = new Error(code);
    error.code = code;
    throw error;
  }
}

function createProtectedLicenseStore(directory, protector) {
  if (!protector || typeof protector.encrypt !== 'function' || typeof protector.decrypt !== 'function') {
    throw new TypeError('secure protector is required');
  }
  const encryptedPath = (name) => path.join(directory, name);
  const readEncrypted = (name, code) => {
    const filename = encryptedPath(name);
    if (!fs.existsSync(filename)) return null;
    return parseJson(protector.decrypt(fs.readFileSync(filename)), code);
  };
  const writeEncrypted = (name, value) => {
    atomicWrite(encryptedPath(name), protector.encrypt(JSON.stringify(value)));
  };
  return Object.freeze({
    loadProfile: () => readEncrypted(PROFILE_FILE, 'device_profile_corrupt'),
    saveProfile: (profile) => writeEncrypted(PROFILE_FILE, profile),
    loadLicense: () => readEncrypted(LICENSE_FILE, 'license_state_corrupt'),
    saveLicense: (license) => writeEncrypted(LICENSE_FILE, license),
    loadFirstUseDate() {
      const filename = encryptedPath(FIRST_USE_FILE);
      if (!fs.existsSync(filename)) return null;
      const value = fs.readFileSync(filename, 'utf8').trim();
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
        const error = new Error('first_use_date_corrupt');
        error.code = 'first_use_date_corrupt';
        throw error;
      }
      return value;
    },
    saveFirstUseDate(value) {
      const normalized = String(value || '').slice(0, 10);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(normalized)) {
        throw new TypeError('invalid_first_use_date');
      }
      const existing = this.loadFirstUseDate();
      if (existing) return existing;
      atomicWrite(encryptedPath(FIRST_USE_FILE), Buffer.from(`${normalized}\n`, 'utf8'));
      return normalized;
    },
    loadMigrationState() {
      const filename = encryptedPath(MIGRATION_FILE);
      if (!fs.existsSync(filename)) return null;
      return parseJson(fs.readFileSync(filename, 'utf8'), 'migration_state_corrupt');
    },
    saveMigrationState(state) {
      const safe = {
        version: 1,
        status: String(state?.status || ''),
        reason: String(state?.reason || ''),
        updated_at: String(state?.updated_at || ''),
      };
      atomicWrite(encryptedPath(MIGRATION_FILE), Buffer.from(JSON.stringify(safe), 'utf8'));
    },
  });
}

module.exports = { atomicWrite, createProtectedLicenseStore };
