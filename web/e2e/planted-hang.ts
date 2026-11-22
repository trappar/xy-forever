// Planted: a page whose script never returns. Only hang-bound.e2e.ts runs it, in a nested run (XYF_E2E_PLANTED=1).
import { test } from '@playwright/test';

test('planted: a page that never returns', async ({ page }) => {
  await page.setContent('<p>stuck</p>');
  await page.evaluate(() => {
    for (;;);
  });
});
