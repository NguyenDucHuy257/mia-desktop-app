import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LicenseGate } from '../../src/features/licensing/LicenseGate';
import { NoticeDialog } from '../../src/components/NoticeDialog';
const require = createRequire(import.meta.url);
const { buildSupportDiagnostics } = require('../../electron/support-diagnostics.cjs');
const dirs: string[] = [];
afterEach(() => { vi.unstubAllGlobals(); for (const dir of dirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true }); });
describe('support diagnostics', () => {
  it('includes all structured application events but excludes sensitive fields and raw malformed text', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-support-')); dirs.push(dir);
    fs.mkdirSync(path.join(dir, 'logs'));
    const event = (name: string, fields: object) => `2026-09-17T10:00:00.000Z INFO ${name} ${JSON.stringify(fields)}`;
    fs.writeFileSync(path.join(dir, 'logs', 'electron.log.1'), event('license_operation_verify_response', {
      reason: 'legacy_migration_record_incomplete', valid: false, password: 'private-password', key: 'KEYV2-private-key', hardware: { disk_serial: 'private-hardware' }, phone: '0900001001',
    }));
    fs.writeFileSync(path.join(dir, 'logs', 'renderer.log'), [
      event('account_login_failed', { mode: 'bulk', account_count: 7, successful: 0, failed: 7 }),
      event('unrelated_event', { reason: 'private-event' }),
      '2026-09-17T10:00:00.000Z ERROR license_bad private-malformed',
    ].join('\n'));
    const report = await buildSupportDiagnostics(dir, '4.1.0', { state: 'error', reason: 'license_policy_missing', details: { key: 'private-snapshot' } });
    expect(report.events).toHaveLength(3);
    expect(report.events[0].fields.reason).toBe('legacy_migration_record_incomplete');
    expect(report.events[1].fields).toMatchObject({ mode: 'bulk', account_count: 7, successful: 0, failed: 7 });
    expect(report.events[2].event).toBe('unrelated_event');
    expect(JSON.stringify(report)).not.toContain('private-password');
    expect(JSON.stringify(report)).not.toContain('KEYV2-private-key');
    expect(JSON.stringify(report)).not.toContain('private-hardware');
    expect(JSON.stringify(report)).not.toContain('private-snapshot');
    expect(JSON.stringify(report)).not.toContain('0900001001');
    expect(report.version).toBe('4.1.0');
    expect(report.scope).toBe('application_diagnostics');
  });
  it('exports current error when no historical logs exist', async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mia-support-')); dirs.push(dir);
    const report = await buildSupportDiagnostics(dir, '4.1.0', { state: 'expired', reason: 'legacy_key_expired' });
    expect(report.events).toEqual([]);
    expect(report.state.reason).toBe('legacy_key_expired');
  });
  it('shows download on blocked license screen and opted-in account error dialogs', () => {
    vi.stubGlobal('window', { miaRuntime: { logs: { exportSupport: vi.fn() } } });
    expect(renderToStaticMarkup(createElement(LicenseGate))).toContain('Tải log lỗi');
    expect(renderToStaticMarkup(createElement(NoticeDialog, { kind: 'error', message: 'failed', supportLog: true, onClose() {} }))).toContain('Tải log lỗi');
    expect(renderToStaticMarkup(createElement(NoticeDialog, { kind: 'warning', message: 'warning', onClose() {} }))).toContain('Tải log lỗi');
    expect(renderToStaticMarkup(createElement(NoticeDialog, { kind: 'success', message: 'ok', onClose() {} }))).not.toContain('Tải log lỗi');
  });
  it('allows trusted pre-activation export without granting data access', () => {
    const main = fs.readFileSync('electron/main.cjs', 'utf8');
    expect(main).toMatch(/mia:logs:exportSupport', async \(event\) => \{\s*assertTrustedSender\(event, \{ licenseRequired: false, offlineAuthRequired: false \}\)/);
    expect(main).toContain('if (chosen.canceled || !chosen.filePath) return { saved: false };');
  });
});
