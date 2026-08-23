const fs = require('node:fs/promises');
const path = require('node:path');

const LOCAL_CONCURRENCY = 1;
const DEFAULT_EXPORT_FOLDER = 'C:\\MIACrawl\\Export\\PDF\\T10_2023';
const DEFAULTS = Object.freeze({
  concurrency: LOCAL_CONCURRENCY,
  retries: 5,
  exportFolder: DEFAULT_EXPORT_FOLDER,
  pdfConcurrency: 5,
});

function validatePreferences(value) {
  if (!value || typeof value !== 'object') throw new TypeError('invalid_preferences');
  // Keep accepting the legacy field so existing preferences.json files remain
  // readable, but MIA Desktop always executes one account/job at a time.
  const requestedConcurrency = Number(value.concurrency ?? LOCAL_CONCURRENCY);
  const retries = Number(value.retries);
  const exportFolder = value.exportFolder ?? DEFAULT_EXPORT_FOLDER;
  const pdfConcurrency = Number(value.pdfConcurrency ?? 5);
  if (!Number.isInteger(requestedConcurrency) || requestedConcurrency < 1 || requestedConcurrency > 4) throw new TypeError('invalid_concurrency');
  if (!Number.isInteger(retries) || retries < 0 || retries > 5) throw new TypeError('invalid_retries');
  if (typeof exportFolder !== 'string' || exportFolder.length > 1024 || /[\x00-\x1f]/.test(exportFolder)) throw new TypeError('invalid_export_folder');
  if (!Number.isInteger(pdfConcurrency) || pdfConcurrency < 1 || pdfConcurrency > 100) throw new TypeError('invalid_pdf_concurrency');
  return { concurrency: LOCAL_CONCURRENCY, retries, exportFolder, pdfConcurrency };
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

function sanitizeLogLine(line) {
  return line
    .replace(/\b\d{10,14}\b/g, '[redacted-id]')
    .replace(/(password|token|secret|authorization|cookie|session|credential|api[_-]?key)\s*[=:]\s*\S+/gi, '$1=[redacted]')
    .slice(0, 1000);
}

function timestampKey(line) {
  const match = line.match(/^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)/);
  return match ? match[1].replace(' ', 'T').replace(',', '.') : '';
}

async function readTail(filename, source, limit = 160) {
  try {
    const content = await fs.readFile(filename, 'utf8');
    return content.split(/\r?\n/).filter(Boolean).slice(-limit).map((line) => ({
      timestamp: timestampKey(line),
      line: `[${source}] ${sanitizeLogLine(line)}`,
    }));
  } catch (error) {
    if (error?.code === 'ENOENT') return [];
    throw error;
  }
}

async function readSanitizedLogs(userDataDirectory) {
  const roots = [
    ['electron', path.join(userDataDirectory, 'logs', 'electron.log'), 1],
    ['renderer', path.join(userDataDirectory, 'logs', 'renderer.log'), 1],
    ['runtime', path.join(userDataDirectory, 'offline-runtime', 'logs', 'runtime.log'), 2],
    ['crawler', path.join(userDataDirectory, 'offline-runtime', 'logs', 'crawler.log'), 2],
  ];
  const requests = [];
  for (const [source, filename, backups] of roots) {
    for (let copy = Number(backups); copy >= 1; copy -= 1) requests.push(readTail(`${filename}.${copy}`, source, 100));
    requests.push(readTail(filename, source));
  }
  const entries = (await Promise.all(requests)).flat();
  entries.sort((left, right) => left.timestamp.localeCompare(right.timestamp));
  return entries.slice(-500).map((item) => item.line);
}

function parseLogEntry(line, index) {
  const match = line.match(/^\[([^\]]+)\]\s+(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(INFO|WARN|WARNING|ERROR)\s+([^\s]+)\s*(.*)$/i);
  if (!match) {
    return { id: `legacy-${index}`, timestamp: '', level: 'info', source: 'system', event: 'legacy_log', details: line };
  }
  const [, source, timestamp, rawLevel, event, rawDetails] = match;
  const level = rawLevel.toLowerCase() === 'warning' ? 'warn' : rawLevel.toLowerCase();
  let details = rawDetails;
  try {
    const value = JSON.parse(rawDetails);
    details = Object.keys(value).length ? JSON.stringify(value, null, 2) : '';
  } catch {
    // Runtime/crawler logs are not guaranteed to use the renderer JSON format.
  }
  return {
    id: `${timestamp}-${source}-${event}-${index}`,
    timestamp,
    level,
    source,
    event,
    details: sanitizeLogLine(details),
  };
}

async function readSanitizedLogEntries(userDataDirectory) {
  const lines = await readSanitizedLogs(userDataDirectory);
  return lines.map(parseLogEntry).reverse();
}

async function clearDiagnosticLogs(userDataDirectory) {
  const files = [
    path.join(userDataDirectory, 'logs', 'electron.log'),
    path.join(userDataDirectory, 'logs', 'renderer.log'),
    path.join(userDataDirectory, 'offline-runtime', 'logs', 'runtime.log'),
    path.join(userDataDirectory, 'offline-runtime', 'logs', 'crawler.log'),
  ];
  await Promise.all(files.flatMap((filename) => [filename, `${filename}.1`, `${filename}.2`]).map(async (filename) => {
    try { await fs.writeFile(filename, '', { encoding: 'utf8', mode: 0o600 }); }
    catch (error) { if (error?.code !== 'ENOENT') throw error; }
  }));
  return true;
}

module.exports = {
  DEFAULTS, DEFAULT_EXPORT_FOLDER, LOCAL_CONCURRENCY,
  clearDiagnosticLogs, readPreferences, readSanitizedLogEntries, readSanitizedLogs,
  validatePreferences, writePreferences,
};
