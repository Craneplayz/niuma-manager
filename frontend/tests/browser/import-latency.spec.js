import { test, expect } from '@playwright/test';
import { syncRun } from '../sync-fixtures.js';

async function mockTeaching(page) {
  const requests = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    requests.push(path);
    const data = {
      '/api/auth/me': { user: { id: 'student', name: '学生', emailVerified: true } },
      '/api/workspace': { tasks: { version: 1, value: [] } },
      '/api/recognition/batches': [],
      '/api/sources/credentials': { configured: true, username: 'synthetic-campus' },
      '/api/sources/sync-runs': { contract_version: 'source-sync/0.1', runs: [] },
      '/api/sources/sync-schedule': { contract_version: 'source-sync/0.1', enabled: false, available: true, revision: 0, interval_minutes: 60 },
    };
    await route.fulfill({ json: data[path] || {} });
  });
  return requests;
}

for (const source of ['text', 'files']) {
  test(`legacy ${source} bookmark opens teaching without loading manual imports`, async ({ page }) => {
    const requests = await mockTeaching(page);
    await page.goto('/?page=import&importSource=' + source);
    await expect(page.locator('.teaching-connection')).toContainText('synthetic-campus');
    await expect(page.locator('#import-text, #material-files')).toHaveCount(0);
    expect(requests.filter(p => p.startsWith('/api/materials/') || p.endsWith('/text-jobs'))).toEqual([]);
    await expect(page).not.toHaveURL(/importSource=/);
  });
}

test('teaching recognition keeps its primary colors when the button becomes Stop', async ({ page }) => {
  await mockTeaching(page);
  let active = false;
  const run = syncRun({ id: 'color-test', state: 'crawling', states: [] });
  await page.route('**/api/sources/sync-runs', async route => {
    if (route.request().method() === 'POST') {
      active = true;
      await route.fulfill({ json: run });
    } else {
      await route.fulfill({ json: { contract_version: 'source-sync/0.1', runs: active ? [run] : [] } });
    }
  });
  await page.goto('/?page=import');
  const start = page.getByRole('button', { name: '开始识别' });
  await expect(start).toBeEnabled();
  await page.mouse.move(0, 0);
  const colors = await start.evaluate(el => {
    const style = getComputedStyle(el);
    return [style.backgroundColor, style.color, style.borderTopColor];
  });
  await start.click();
  await page.mouse.move(0, 0);
  const stop = page.getByRole('button', { name: '停止导入' });
  await expect(stop).toBeEnabled();
  await expect(stop.locator('.teaching-stop-icon')).toBeVisible();
  await expect(stop).toHaveCSS('background-color', colors[0]);
  await expect(stop).toHaveCSS('color', colors[1]);
  await expect(stop).toHaveCSS('border-top-color', colors[2]);
});
