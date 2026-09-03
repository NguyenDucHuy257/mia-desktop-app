'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { randomBytes } = require('node:crypto');

const RUNTIME_KEY_FILE = 'runtime-session-key.bin';

function isValidRuntimeSessionKey(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(value)) return false;
  try {
    return Buffer.from(value, 'base64url').length === 32;
  } catch {
    return false;
  }
}

function atomicWrite(filename, contents) {
  const temporary = `${filename}.${process.pid}.${randomBytes(6).toString('hex')}.tmp`;
  try {
    fs.writeFileSync(temporary, contents, { mode: 0o600, flag: 'wx' });
    fs.renameSync(temporary, filename);
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

function loadOrCreateRuntimeSessionKey({ directory, protector, logger, generateKey } = {}) {
  if (!directory || !protector || typeof protector.encrypt !== 'function' || typeof protector.decrypt !== 'function') {
    throw new TypeError('Invalid runtime session key dependency.');
  }
  const filename = path.join(directory, RUNTIME_KEY_FILE);
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });

  if (fs.existsSync(filename)) {
    try {
      const existing = protector.decrypt(fs.readFileSync(filename));
      if (!isValidRuntimeSessionKey(existing)) throw new Error('Invalid runtime session key payload.');
      return existing;
    } catch (error) {
      // This key only protects short-lived crawler credentials. If Windows
      // DPAPI can no longer decrypt it, retain all SQLite/invoice data and
      // replace only this unusable key so the local runtime can start again.
      logger?.warn('runtime_session_key_recovered', { error_type: error?.name });
    }
  }

  const key = generateKey ? generateKey() : randomBytes(32).toString('base64url');
  if (!isValidRuntimeSessionKey(key)) throw new Error('Generated runtime session key is invalid.');
  atomicWrite(filename, protector.encrypt(key));
  return key;
}

module.exports = { RUNTIME_KEY_FILE, isValidRuntimeSessionKey, loadOrCreateRuntimeSessionKey };
