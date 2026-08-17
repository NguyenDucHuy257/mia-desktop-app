import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { afterEach, describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const { ensureDeviceIdentity, signChallenge } = require('../../electron/device-identity.cjs') as {
  ensureDeviceIdentity(directory: string, protector: { encrypt(value: string): Buffer; decrypt(value: Buffer): string }): { publicKeyPem: string; fingerprint: string };
  signChallenge(directory: string, protector: { encrypt(value: string): Buffer; decrypt(value: Buffer): string }, challenge: string): string;
};

const directories: string[] = [];
const protector = {
  encrypt: (value: string) => Buffer.from(value, 'utf8'),
  decrypt: (value: Buffer) => value.toString('utf8'),
};

afterEach(() => {
  for (const directory of directories.splice(0)) fs.rmSync(directory, { recursive: true, force: true });
});

describe('device identity', () => {
  it('creates a stable public identity without returning the private key', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-device-'));
    directories.push(directory);
    const first = ensureDeviceIdentity(directory, protector);
    const second = ensureDeviceIdentity(directory, protector);
    expect(second).toEqual(first);
    expect(first.fingerprint).toMatch(/^[a-f0-9]{64}$/);
    expect(JSON.stringify(first)).not.toContain('PRIVATE KEY');
  });

  it('signs a server challenge that verifies with the device public key', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-device-'));
    directories.push(directory);
    const identity = ensureDeviceIdentity(directory, protector);
    const challenge = 'server-challenge-00000001';
    const signature = signChallenge(directory, protector, challenge);
    expect(crypto.verify(null, Buffer.from(challenge), identity.publicKeyPem, Buffer.from(signature, 'base64'))).toBe(true);
  });
});
