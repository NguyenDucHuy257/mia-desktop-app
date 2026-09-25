'use strict';

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const AUTH_FILE = 'offline-auth.bin';
const MIN_PASSWORD_LENGTH = 8;
const MAX_PASSWORD_LENGTH = 128;
const SCRYPT_OPTIONS = Object.freeze({ N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 });

class OfflineAuthError extends Error {
  constructor(code) {
    super(code);
    this.name = 'OfflineAuthError';
    this.code = code;
  }
}

function normalizePassword(value) {
  if (typeof value !== 'string') throw new OfflineAuthError('offline_password_invalid');
  const password = value.normalize('NFC');
  if (password.length < MIN_PASSWORD_LENGTH || password.length > MAX_PASSWORD_LENGTH || !password.trim()) {
    throw new OfflineAuthError('offline_password_invalid');
  }
  return password;
}

function derive(password, salt, options = SCRYPT_OPTIONS) {
  return new Promise((resolve, reject) => crypto.scrypt(password, salt, 64, options, (error, key) => {
    if (error) reject(error); else resolve(key);
  }));
}

function atomicWrite(filename, content) {
  fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
  const temporary = `${filename}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`;
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, 'wx', 0o600);
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

function createOfflineAuthManager({ directory, protector, logger, now = () => Date.now(), randomBytes = crypto.randomBytes }) {
  if (!path.isAbsolute(directory) || !protector || typeof protector.encrypt !== 'function' || typeof protector.decrypt !== 'function') {
    throw new TypeError('invalid offline auth dependencies');
  }
  const filename = path.join(directory, AUTH_FILE);
  let unlocked = false;
  let failedAttempts = 0;
  let blockedUntil = 0;

  function load() {
    if (!fs.existsSync(filename)) return null;
    try {
      const value = JSON.parse(protector.decrypt(fs.readFileSync(filename)));
      if (!value || value.version !== 1 || value.kdf !== 'scrypt'
        || typeof value.salt !== 'string' || typeof value.hash !== 'string'
        || value.N !== SCRYPT_OPTIONS.N || value.r !== SCRYPT_OPTIONS.r || value.p !== SCRYPT_OPTIONS.p) throw new Error();
      const salt = Buffer.from(value.salt, 'base64');
      const hash = Buffer.from(value.hash, 'base64');
      if (salt.length !== 32 || hash.length !== 64) throw new Error();
      return value;
    } catch {
      throw new OfflineAuthError('offline_auth_state_corrupt');
    }
  }

  function status() {
    const configured = Boolean(load());
    return Object.freeze({
      state: !configured ? 'setup_required' : unlocked ? 'unlocked' : 'locked',
      configured,
      unlocked: configured && unlocked,
      retry_after_seconds: Math.max(0, Math.ceil((blockedUntil - now()) / 1000)),
    });
  }

  async function verify(value) {
    const record = load();
    if (!record) throw new OfflineAuthError('offline_password_not_configured');
    if (now() < blockedUntil) throw new OfflineAuthError('offline_auth_rate_limited');
    const password = normalizePassword(value);
    const candidate = await derive(password, Buffer.from(record.salt, 'base64'), {
      N: record.N, r: record.r, p: record.p, maxmem: SCRYPT_OPTIONS.maxmem,
    });
    const expected = Buffer.from(record.hash, 'base64');
    if (!crypto.timingSafeEqual(candidate, expected)) {
      failedAttempts += 1;
      if (failedAttempts >= 5) blockedUntil = now() + Math.min(300, 30 * (2 ** (failedAttempts - 5))) * 1000;
      logger?.warn?.('offline_auth_failed', { failed_attempts: failedAttempts, rate_limited: blockedUntil > now() });
      throw new OfflineAuthError('offline_password_incorrect');
    }
    failedAttempts = 0;
    blockedUntil = 0;
    return record;
  }

  async function savePassword(value, createdAt = new Date(now()).toISOString()) {
    const password = normalizePassword(value);
    const salt = randomBytes(32);
    const hash = await derive(password, salt);
    const timestamp = new Date(now()).toISOString();
    atomicWrite(filename, protector.encrypt(JSON.stringify({
      version: 1, kdf: 'scrypt', N: SCRYPT_OPTIONS.N, r: SCRYPT_OPTIONS.r, p: SCRYPT_OPTIONS.p,
      salt: salt.toString('base64'), hash: hash.toString('base64'), created_at: createdAt, updated_at: timestamp,
    })));
  }

  return Object.freeze({
    status,
    async create(password, confirmation) {
      if (load()) throw new OfflineAuthError('offline_password_already_configured');
      if (password !== confirmation) throw new OfflineAuthError('offline_password_confirmation_mismatch');
      await savePassword(password);
      unlocked = true;
      logger?.info?.('offline_password_created');
      return status();
    },
    async unlock(password) {
      await verify(password);
      unlocked = true;
      logger?.info?.('offline_auth_unlocked');
      return status();
    },
    async change(currentPassword, newPassword, confirmation) {
      if (!unlocked) throw new OfflineAuthError('offline_auth_required');
      const record = await verify(currentPassword);
      if (newPassword !== confirmation) throw new OfflineAuthError('offline_password_confirmation_mismatch');
      if (currentPassword === newPassword) throw new OfflineAuthError('offline_password_unchanged');
      await savePassword(newPassword, record.created_at);
      logger?.info?.('offline_password_changed');
      return status();
    },
    async recover(newPassword, confirmation) {
      if (!load()) throw new OfflineAuthError('offline_password_not_configured');
      if (newPassword !== confirmation) throw new OfflineAuthError('offline_password_confirmation_mismatch');
      const existing = load();
      await savePassword(newPassword, existing.created_at);
      failedAttempts = 0;
      blockedUntil = 0;
      unlocked = true;
      logger?.info?.('offline_password_recovered');
      return status();
    },
    lock() {
      unlocked = false;
      return status();
    },
  });
}

module.exports = { AUTH_FILE, MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, OfflineAuthError, createOfflineAuthManager, normalizePassword };
