const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const PRIVATE_KEY_FILE = 'device-private-key.bin';
const PUBLIC_KEY_FILE = 'device-public-key.pem';

function fingerprintPublicKey(publicKeyPem) {
  return crypto
    .createHash('sha256')
    .update(publicKeyPem, 'utf8')
    .digest('hex');
}

function ensureDeviceIdentity(directory, protector) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const privatePath = path.join(directory, PRIVATE_KEY_FILE);
  const publicPath = path.join(directory, PUBLIC_KEY_FILE);

  if (!fs.existsSync(privatePath) || !fs.existsSync(publicPath)) {
    const { privateKey, publicKey } = crypto.generateKeyPairSync('ed25519');
    const privatePem = privateKey.export({ type: 'pkcs8', format: 'pem' });
    const publicPem = publicKey.export({ type: 'spki', format: 'pem' });
    fs.writeFileSync(privatePath, protector.encrypt(String(privatePem)), { mode: 0o600 });
    fs.writeFileSync(publicPath, publicPem, { encoding: 'utf8', mode: 0o644 });
  }

  const publicKeyPem = fs.readFileSync(publicPath, 'utf8');
  return {
    algorithm: 'Ed25519',
    publicKeyPem,
    fingerprint: fingerprintPublicKey(publicKeyPem),
  };
}

function signChallenge(directory, protector, challenge) {
  if (typeof challenge !== 'string' || challenge.length < 16 || challenge.length > 4096) {
    throw new TypeError('challenge must contain 16 to 4096 characters');
  }
  ensureDeviceIdentity(directory, protector);
  const encrypted = fs.readFileSync(path.join(directory, PRIVATE_KEY_FILE));
  const privateKeyPem = protector.decrypt(encrypted);
  return crypto.sign(null, Buffer.from(challenge, 'utf8'), privateKeyPem).toString('base64');
}

module.exports = {
  ensureDeviceIdentity,
  fingerprintPublicKey,
  signChallenge,
};
