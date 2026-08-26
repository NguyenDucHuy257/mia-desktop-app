'use strict';

function validateExternalUrl(value) {
  if (typeof value !== 'string' || value.length < 1 || value.length > 2048) throw new TypeError('invalid_external_url');
  let parsed;
  try { parsed = new URL(value); } catch { throw new TypeError('invalid_external_url'); }
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new TypeError('invalid_external_url_protocol');
  if (parsed.username || parsed.password) throw new TypeError('invalid_external_url_credentials');
  const host = parsed.hostname.toLowerCase();
  if (!host || ['localhost', '127.0.0.1', '::1'].includes(host)) throw new TypeError('invalid_external_url_host');
  return parsed.href;
}

module.exports = { validateExternalUrl };
