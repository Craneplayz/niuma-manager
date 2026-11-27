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

test('starting another recognition clears the previous completion until the new run finishes', async ({ page }, info) => {
  await mockTeaching(page);
  let sequence = 0, running = null, lastCheck = null;
  const finish = () => {
    lastCheck = { run_id: running.run_id, state: 'succeeded', updated_at: '2026-10-16T01:30:00Z', trigger: 'manual', changed: false };
    running = null;
  };
  await page.route('**/api/sources/sync-runs', async route => {
    if (route.request().method() === 'POST') {
      running = syncRun({ id: 'manual-' + ++sequence, state: 'crawling', states: [] });
      await route.fulfill({ status: 202, json: running });
    } else {
      await route.fulfill({ json: { contract_version: 'source-sync/0.1', runs: running ? [running] : [], last_check: lastCheck } });
    }
  });
  let releaseSchedule;
  const saving = new Promise(resolve => { releaseSchedule = resolve; });
  await page.route('**/api/sources/sync-schedule', async route => {
    if (route.request().method() === 'GET') return route.fallback();
    await saving;
    await route.fulfill({ json: { ...route.request().postDataJSON(), available: true, revision: 1 } });
  });
  await page.clock.install({ time: new Date('2026-10-16T01:30:00Z') });
  await page.clock.pauseAt(new Date('2026-10-16T01:30:01Z'));
  await page.goto('/?page=import');
  await page.getByRole('button', { name: '开始识别' }).click();
  await expect(page.getByRole('button', { name: '停止导入' })).toBeEnabled();
  finish();
  await page.clock.fastForward(2100);
  const toast = page.locator('#toast');
  await expect(toast).toHaveClass(/visible/);
  await expect(toast).toHaveText('已检查，没有新内容');

  await page.getByLabel('执行频率').selectOption('15');
  await page.getByRole('checkbox', { name: /允许定期读取/ }).check();
  await page.getByRole('button', { name: '启动并识别' }).click();
  try {
    await expect(page.getByRole('button', { name: '正在启动…' })).toBeDisabled();
    await expect(toast).not.toHaveClass(/visible/);
    await expect(page.locator('[data-source-sync-last-check]')).toHaveCount(0);
  } finally {
    releaseSchedule();
  }
  const stop = page.getByRole('button', { name: '停止同步' });
  await expect(stop).toBeEnabled();
  await expect(stop).toHaveAttribute('title', '正在采集');
  await page.clock.fastForward(2100);
  await expect(stop).toBeEnabled();
  await expect(toast).not.toHaveClass(/visible/);
  await page.screenshot({ path: info.outputPath('second-run-in-progress.png') });
  finish();
  await page.clock.fastForward(2100);
  await expect(toast).toHaveClass(/visible/);
  await expect(toast).toHaveText('已检查，没有新内容');
  expect(sequence).toBe(2);
});
