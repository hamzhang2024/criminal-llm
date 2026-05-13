import { test, expect } from '@playwright/test';
test('page renders', async ({ page }) => {
  page.on('pageerror', e => console.log('PAGE ERROR:', e.message));
  page.on('console', m => { if (m.type() === 'error') console.log('JS ERROR:', m.text()) });
  await page.goto('http://localhost:8080');
  await expect(page.locator('#root')).toBeVisible();
  const title = await page.title();
  console.log('Page title:', title);
  await expect(page.locator('h2')).toContainText('我的案件');
});
