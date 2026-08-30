import { _electron as electron } from 'playwright';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

if (process.env.MIA_REAL_PORTAL !== '1') {
  throw new Error('Set MIA_REAL_PORTAL=1 to authorize real portal traffic.');
}

const today = new Date();
const defaultTo = today.toISOString().slice(0, 10);
const dateFrom = process.env.MIA_REAL_DATE_FROM || defaultTo;
const dateTo = process.env.MIA_REAL_DATE_TO || defaultTo;
const terminal = new Set(['completed', 'completed_with_warning', 'failed', 'cancelled', 'abandoned']);
const launchEnvironment = { ...process.env };
delete launchEnvironment.ELECTRON_RUN_AS_NODE;
const application = await electron.launch({
  args: ['.'], cwd: process.cwd(), env: launchEnvironment,
});
const output = await mkdtemp(path.join(tmpdir(), 'mia-real-portal-'));

async function runJob(page, label, overrides) {
  const record = await page.evaluate(async ({ dateFrom, dateTo, overrides }) => {
    const accounts = await window.miaRuntime.accountConnections.list();
    const account = accounts.find((item) => item.status === 'connected') || accounts[0];
    if (!account) throw new Error('real_portal_account_missing');
    return window.miaRuntime.jobs.start({
      connection_id: account.connection_id,
      date_from: dateFrom,
      date_to: dateTo,
      directions: ['purchase', 'sold'],
      query_types: ['query', 'sco-query'],
      scopes: ['overview'],
      data_types: ['invoice'],
      ...overrides,
    });
  }, { dateFrom, dateTo, overrides });
  let status = record.record;
  const deadline = Date.now() + 30 * 60 * 1000;
  while (!terminal.has(status.status) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    status = await page.evaluate((jobId) => window.miaRuntime.jobs.status(jobId), status.job_id);
  }
  if (!terminal.has(status.status)) {
    await page.evaluate((jobId) => window.miaRuntime.jobs.cancel(jobId), status.job_id);
    throw new Error(`${label}:timeout`);
  }
  if (!['completed', 'completed_with_warning'].includes(status.status)) {
    throw new Error(`${label}:${status.error?.code || status.status}`);
  }
  process.stdout.write(`${label}: PASS (${status.status}, ${status.overall_percent}%)\n`);
}

try {
  const page = await application.firstWindow();
  await page.waitForFunction(() => Boolean(window.miaRuntime?.jobs));
  await runJob(page, 'overview-detail', { scopes: ['overview', 'detail'] });
  await runJob(page, 'html-package', { data_types: ['html'] });
  await runJob(page, 'xml-package', { data_types: ['xml'] });
  await runJob(page, 'pdf-source', { data_types: ['pdf'] });

  const evidence = await page.evaluate(async ({ output, dateFrom, dateTo }) => {
    const accounts = await window.miaRuntime.accountConnections.list();
    const account = accounts.find((item) => item.status === 'connected') || accounts[0];
    const connectionIds = [account.connection_id];
    const overview = await window.miaRuntime.results.overview({
      connection_id: account.connection_id, direction: null, search: '', cursor: null,
      limit: 50, date_from: dateFrom, date_to: dateTo,
    });
    const details = await window.miaRuntime.results.details({
      connection_id: account.connection_id, direction: null, search: '', cursor: null,
      limit: 50, date_from: dateFrom, date_to: dateTo,
    });
    const exported = {};
    for (const kind of ['excel', 'xml', 'html', 'pdf']) {
      exported[kind] = await window.miaRuntime.artifacts.export({
        destination: output, connection_ids: connectionIds, kinds: [kind],
      });
    }
    return {
      overviewRows: overview.items.length,
      detailRows: details.items.length,
      exported: Object.fromEntries(Object.entries(exported).map(([kind, value]) => [kind, value.count])),
    };
  }, { output, dateFrom, dateTo });
  process.stdout.write(`results: PASS (overview=${evidence.overviewRows}, detail=${evidence.detailRows})\n`);
  process.stdout.write(`artifacts: PASS (${JSON.stringify(evidence.exported)})\n`);
  process.stdout.write(`output: ${output}\n`);
} finally {
  await application.close();
}
