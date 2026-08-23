import { chromium, type FullConfig } from '@playwright/test';

export default async function warmVisualServer(config: FullConfig) {
  const baseURL = String(config.projects[0]?.use?.baseURL ?? 'http://127.0.0.1:4173');
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    // The dev server URL probe only loads index.html. Render the application
    // once so Vite's module graph is ready before the first timed visual test.
    await page.goto(baseURL, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    await page.getByRole('button', { name: 'Quản lý HDDT', exact: true }).waitFor({ timeout: 120_000 });
  } finally {
    await browser.close();
  }
}
