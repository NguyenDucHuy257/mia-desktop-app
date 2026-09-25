import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it } from 'vitest';
import { AccountErrorDownloadButton } from '../../src/components/AccountErrorDownloadButton';
const require = createRequire(import.meta.url);
const { buildAccountDiagnostics } = require('../../electron/support-diagnostics.cjs');
const dirs: string[] = [];
afterEach(() => { for (const dir of dirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true }); });
describe('account error export', () => {
  it('exports only selected job structured events and reports missing history', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-account-log-')); dirs.push(dir);
    const logs = path.join(dir, 'offline-runtime', 'logs'); fs.mkdirSync(logs, { recursive: true });
    const line = (record: object) => `2026-09-23 10:00:00 INFO app.crawl_diagnostics crawl_diagnostic ${JSON.stringify(record)}`;
    fs.writeFileSync(path.join(logs, 'crawl-diagnostics.log'), [
      line({ job_id: 'job-a', event: 'http_finished', http_status: 400, params: { size: '50' }, upstream_error: { message: 'Invalid date' }, password: 'private-secret' }),
      line({ job_id: 'job-b', event: 'http_finished', upstream_error: { message: 'other-account-data' } }),
      '2026-09-23 INFO raw private-secret',
      '2026-09-23 INFO crawl_diagnostic {broken',
    ].join('\n'));
    const report = await buildAccountDiagnostics(dir, '4.1.1', { connection_id: 'conn-a', job_id: 'job-a', error_code: 'source_business_http_400', password: 'private-secret' });
    expect(report.events).toHaveLength(1);
    expect(report.events[0]).toMatchObject({ http_status: 400, upstream_error: { message: 'Invalid date' } });
    expect(report.malformed_records).toBe(1);
    expect(report.history_available).toBe(true);
    expect(JSON.stringify(report)).not.toContain('private-secret');
    expect(JSON.stringify(report)).not.toContain('other-account-data');
    const missing = await buildAccountDiagnostics(dir, '4.1.1', { connection_id: 'conn-c', job_id: 'old-job' });
    expect(missing.history_available).toBe(false);
    expect(missing.snapshot.job_id).toBe('old-job');
  });
  it('renders an error download action', () => {
    const markup = renderToStaticMarkup(createElement(AccountErrorDownloadButton, { snapshot: { connection_id: 'conn-a' } }));
    expect(markup).toContain('Tải mã lỗi');
    expect(markup).not.toContain('Xem kết quả');
  });
});
