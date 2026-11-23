import { test, expect } from '@playwright/test';

for (const source of ['text', 'files']) {
  test(`legacy ${source} bookmark opens teaching without loading manual imports`, async ({ page }) => {
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
    await page.goto('/?page=import&importSource=' + source);
    await expect(page.locator('.teaching-connection')).toContainText('synthetic-campus');
    await expect(page.locator('#import-text, #material-files')).toHaveCount(0);
    expect(requests.filter(p => p.startsWith('/api/materials/') || p.endsWith('/text-jobs'))).toEqual([]);
    await expect(page).not.toHaveURL(/importSource=/);
  });
}
