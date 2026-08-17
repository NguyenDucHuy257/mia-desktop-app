import { expect, test } from '@playwright/test';

test('main invoice screen follows the 1500x1024 Figma reference', async ({ page }) => {
  await page.goto('/');
  await page.evaluate(() => document.fonts.ready);
  const screenshot = await page.screenshot({ animations: 'disabled' });
  await expect(screenshot).toMatchSnapshot('figma-main-1500x1024.png', {
    maxDiffPixelRatio: Number(process.env.MIA_VISUAL_MAX_DIFF_RATIO ?? 0.03),
    threshold: 0.25,
  });
});
