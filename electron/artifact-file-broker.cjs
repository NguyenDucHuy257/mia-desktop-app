'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
const EXTENSIONS = new Set(['.xml', '.html', '.pdf', '.xlsx']);
const KINDS = new Set(['xml', 'html', 'pdf', 'excel']);
const CONNECTION_ID = /^[A-Za-z0-9_-]{1,160}$/;

function validateArtifactName(value) {
  if (typeof value !== 'string' || value.length < 1 || value.length > 180) throw new TypeError('invalid_artifact_name');
  if (value !== path.basename(value) || value.includes('..') || /[<>:"/\\|?*\x00-\x1f]/.test(value) || RESERVED.test(value)) throw new TypeError('invalid_artifact_name');
  if (!EXTENSIONS.has(path.extname(value).toLowerCase())) throw new TypeError('invalid_artifact_extension');
  return value;
}

function resolveInside(directory, filename) {
  if (typeof directory !== 'string' || !path.isAbsolute(directory)) throw new TypeError('invalid_artifact_directory');
  const root = path.resolve(directory);
  const target = path.resolve(root, validateArtifactName(filename));
  if (path.dirname(target).toLowerCase() !== root.toLowerCase()) throw new TypeError('artifact_path_escape');
  return target;
}

async function atomicWrite(directory, filename, content) {
  if (!Buffer.isBuffer(content)) throw new TypeError('invalid_artifact_content');
  await fs.mkdir(directory, { recursive: true });
  const requested = resolveInside(directory, filename);
  const extension = path.extname(requested);
  const stem = requested.slice(0, -extension.length);
  let target = requested;
  for (let copy = 1; copy <= 999; copy += 1) {
    try { await fs.access(target); target = `${stem} (${copy})${extension}`; } catch { break; }
  }
  const temporary = path.join(path.dirname(target), `.${path.basename(target)}.${crypto.randomUUID()}.tmp`);
  try {
    await fs.writeFile(temporary, content, { flag: 'wx' });
    await fs.rename(temporary, target);
    return target;
  } catch (error) {
    await fs.rm(temporary, { force: true }).catch(() => undefined);
    throw error;
  }
}

function validateExportRequest(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid_artifact_request');
  if (Object.keys(value).some((key) => !['destination', 'connection_ids', 'kinds'].includes(key))) throw new TypeError('invalid_artifact_request');
  if (typeof value.destination !== 'string' || !path.isAbsolute(value.destination) || value.destination.length > 1024) throw new TypeError('invalid_artifact_directory');
  if (!Array.isArray(value.connection_ids) || value.connection_ids.length < 1 || value.connection_ids.length > 50 || new Set(value.connection_ids).size !== value.connection_ids.length || value.connection_ids.some((id) => typeof id !== 'string' || !CONNECTION_ID.test(id))) throw new TypeError('invalid_artifact_accounts');
  if (!Array.isArray(value.kinds) || value.kinds.length < 1 || value.kinds.length > 4 || new Set(value.kinds).size !== value.kinds.length || value.kinds.some((kind) => !KINDS.has(kind))) throw new TypeError('invalid_artifact_kind');
  return { destination: path.resolve(value.destination), connection_ids: [...value.connection_ids], kinds: [...value.kinds] };
}

function createArtifactBroker(getRuntime) {
  return Object.freeze({ export: (value) => getRuntime().invoke('artifacts.export', validateExportRequest(value), { timeoutMs: 30 * 60 * 1000 }) });
}

module.exports = { atomicWrite, createArtifactBroker, resolveInside, validateArtifactName, validateExportRequest };
