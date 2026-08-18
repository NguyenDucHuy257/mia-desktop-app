const fs = require('node:fs/promises');
const path = require('node:path');

const DEFAULTS = Object.freeze({ concurrency: 2, retries: 5 });

function validatePreferences(value) {
  if (!value || typeof value !== 'object') throw new TypeError('invalid_preferences');
  const concurrency = Number(value.concurrency);
  const retries = Number(value.retries);
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 4) throw new TypeError('invalid_concurrency');
  if (!Number.isInteger(retries) || retries < 0 || retries > 5) throw new TypeError('invalid_retries');
  return { concurrency, retries };
}

async function readPreferences(userDataDirectory) {
  try {
    return validatePreferences(JSON.parse(await fs.readFile(path.join(userDataDirectory, 'preferences.json'), 'utf8')));
  } catch (error) {
    if (error?.code === 'ENOENT' || error instanceof SyntaxError || error instanceof TypeError) return { ...DEFAULTS };
    throw error;
  }
}

async function writePreferences(userDataDirectory, value) {
  const preferences = validatePreferences(value);
  await fs.mkdir(userDataDirectory, { recursive: true });
  const target = path.join(userDataDirectory, 'preferences.json');
  const temporary = `${target}.tmp`;
  await fs.writeFile(temporary, JSON.stringify(preferences), { encoding: 'utf8', mode: 0o600 });
  await fs.rename(temporary, target);
  return preferences;
}

async function readSanitizedLogs(userDataDirectory) {
  const filename = path.join(userDataDirectory, 'offline-runtime', 'logs', 'runtime.log');
  let content;
  try { content = await fs.readFile(filename, 'utf8'); } catch (error) { if (error?.code === 'ENOENT') return []; throw error; }
  return content.split(/\r?\n/).filter(Boolean).slice(-200).map((line) => line
    .replace(/\b\d{10,14}\b/g, '[redacted-id]')
    .replace(/(password|token|authorization|cookie)\s*[=:]\s*\S+/gi, '$1=[redacted]')
    .slice(0, 500));
}

module.exports = { DEFAULTS, readPreferences, readSanitizedLogs, validatePreferences, writePreferences };
