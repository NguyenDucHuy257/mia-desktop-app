import { chromium, type FullConfig } from '@playwright/test';

export default async function warmVisualServer(config: FullConfig) {
  const baseURL = String(config.projects[0]?.use?.baseURL ?? 'http://127.0.0.1:4173');
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    // The dev server URL probe only loads index.html. Render the application
    // once so Vite's module graph is ready before the first timed visual test.
    await page.goto(baseURL, { waitUntil: 'domcontentloaded', timeout: 120_000 });
    // A browser without the Electron bridge must now render the fail-closed
    // license gate, so warming cannot depend on a protected workspace control.
    await page.locator('#root > *').waitFor({ timeout: 120_000 });
  } finally {
    await browser.close();
  }
}
