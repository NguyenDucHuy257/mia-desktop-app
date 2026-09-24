'use strict';

const fs = require('node:fs');
const path = require('node:path');

const STORE_FILE = 'invoice-proxies.bin';
const MAX_FILE_BYTES = 256 * 1024;
const MAX_PROXIES = 200;

function parseMkvnProxyText(text) {
  if (typeof text !== 'string') throw Object.assign(new Error('Proxy file is invalid.'), { code: 'proxy_file_invalid' });
  const rows = text.replace(/^\uFEFF/, '').split(/\r?\n/).slice(1);
  const proxies = [];
  const seen = new Set();
  for (const raw of rows) {
    const line = raw.trim();
    if (!line) continue;
    const parts = line.split(':');
    if (parts.length !== 4) throw Object.assign(new Error('Invalid MKVN proxy row.'), { code: 'proxy_row_invalid' });
    const [host, portText, username, password] = parts.map((value) => value.trim());
    const port = Number(portText);
    if (!/^(?=.{1,253}$)[a-z0-9.-]+$/i.test(host) || host.startsWith('.') || host.endsWith('.')
      || !Number.isInteger(port) || port < 1 || port > 65535
      || !username || username.length > 128 || !password || password.length > 256) {
      throw Object.assign(new Error('Invalid MKVN proxy row.'), { code: 'proxy_row_invalid' });
    }
    const url = `http://${encodeURIComponent(username)}:${encodeURIComponent(password)}@${host}:${port}`;
    if (!seen.has(url)) { seen.add(url); proxies.push(url); }
    if (proxies.length > MAX_PROXIES) throw Object.assign(new Error('Too many proxies.'), { code: 'proxy_file_too_many_rows' });
  }
  if (!proxies.length) throw Object.assign(new Error('Proxy file has no data rows.'), { code: 'proxy_file_empty' });
  return proxies;
}

function createProxyImportStore(userDataDirectory, safeStorage) {
  const filename = path.join(userDataDirectory, STORE_FILE);
  function save(proxies, sourceName) {
    if (!safeStorage.isEncryptionAvailable()) throw Object.assign(new Error('Secure storage unavailable.'), { code: 'proxy_secure_storage_unavailable' });
    const payload = JSON.stringify({ version: 1, proxies, source_name: path.basename(sourceName), imported_at: new Date().toISOString() });
    fs.mkdirSync(userDataDirectory, { recursive: true });
    fs.writeFileSync(filename, safeStorage.encryptString(payload), { mode: 0o600 });
    return { count: proxies.length, source_name: path.basename(sourceName), imported_at: JSON.parse(payload).imported_at };
  }
  function load() {
    if (!fs.existsSync(filename)) return { proxies: [], count: 0, source_name: '', imported_at: '' };
    if (!safeStorage.isEncryptionAvailable()) throw Object.assign(new Error('Secure storage unavailable.'), { code: 'proxy_secure_storage_unavailable' });
    const value = JSON.parse(safeStorage.decryptString(fs.readFileSync(filename)));
    if (value?.version !== 1 || !Array.isArray(value.proxies)) throw Object.assign(new Error('Proxy state is corrupt.'), { code: 'proxy_state_corrupt' });
    return { ...value, count: value.proxies.length };
  }
  function importFile(filePath) {
    const stat = fs.statSync(filePath);
    if (!stat.isFile() || stat.size > MAX_FILE_BYTES) throw Object.assign(new Error('Proxy file is too large.'), { code: 'proxy_file_too_large' });
    return save(parseMkvnProxyText(fs.readFileSync(filePath, 'utf8')), filePath);
  }
  return { importFile, load };
}

module.exports = { MAX_FILE_BYTES, MAX_PROXIES, createProxyImportStore, parseMkvnProxyText };
