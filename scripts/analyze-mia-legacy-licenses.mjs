import { createRequire } from 'node:module';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { analyzeLegacyLicenseText } = require('./lib/mia-legacy-license-analysis.cjs');

const args = process.argv.slice(2);
const input = args[0];
const asOfIndex = args.indexOf('--as-of');
const asOf = asOfIndex >= 0 ? args[asOfIndex + 1] : new Date();

if (!input || (asOfIndex >= 0 && !asOf)) {
  throw new Error('Usage: node scripts/analyze-mia-legacy-licenses.mjs <vip.txt> [--as-of YYYY-MM-DD]');
}

const filename = path.resolve(input);
const info = await stat(filename);
if (!info.isFile() || info.size > 50 * 1024 * 1024) {
  throw new Error('Legacy input must be a file no larger than 50 MiB');
}

const text = await readFile(filename, 'utf8');
const report = analyzeLegacyLicenseText(text, { asOf });
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
