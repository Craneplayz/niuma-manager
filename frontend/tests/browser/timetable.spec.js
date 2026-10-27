import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';

const password = 'Synthetic-only-2026!';
test.use({ locale: 'zh-CN', timezoneId: 'Asia/Shanghai' });
let child, folder, fixture, backend;
test.beforeEach(async ({ page, baseURL }) => {
  folder = await fs.mkdtemp(path.join(os.tmpdir(), 'niuma-timetable-browser-'));
  const root = path.resolve('..');
  const env = { ...process.env, PYTHONPATH: `${root}:${root}/server${process.env.PYTHONPATH ? ':' + process.env.PYTHONPATH : ''}`, NIUMA_DB: `${folder}/data/niuma.sqlite3`,
    NIUMA_ALLOWED_ORIGINS: baseURL, NIUMA_ENABLE_WECHAT: '0', NIUMA_ENABLE_SCHEDULED_SYNC: '0', NIUMA_BACKGROUND_RECOVERY: '0' };
  delete env.NIUMA_ENVIRONMENT;
  child = spawn(process.env.NIUMA_TEST_PYTHON || 'python', ['-u', '-c', `
import json
import app
from auth_test_support import seed_legacy_account
from test_timetable import xls
app.initialize(recover=False)
for name in ('owner', 'other'):
    seed_legacy_account(app.DB, dict(email=name+'@timetable.test',name='课表测试账号' if name=='owner' else '另一测试账号',password='${password}'))
server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
print(json.dumps(dict(port=server.server_port,file=xls())),flush=True)
server.serve_forever()
`], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '', errors = '';
  child.stderr.on('data', data => { errors += data; });
  fixture = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Timetable API startup: ' + errors)), 15000);
    child.on('error', reject);
    child.on('exit', code => { clearTimeout(timer); reject(new Error(`API exit ${code}: ${errors}`)); });
    child.stdout.on('data', data => {
      output += data;
      if (output.includes('\n')) {
        try { const result = JSON.parse(output.split('\n')[0]); clearTimeout(timer); resolve(result); }
        catch (e) { reject(e); }
      }
    });
  });
  backend = `http://127.0.0.1:${fixture.port}`;
  // Forward browser requests to a real isolated API, retaining its receipts/cookies.
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    const response = await route.fetch({ url: backend + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await page.goto('/?view=calendar&anchor=2026-09-07&date=2026-09-07');
  await expect(page.locator('.loading-state')).toHaveCount(0);
  await login(page, 'owner');
});
test.afterEach(async () => {
  if (child && child.exitCode === null) { const exited = once(child, 'exit'); child.kill('SIGTERM'); await exited; }
  if (folder) await fs.rm(folder, { recursive: true, force: true });
});
async function login(page, name) {
  const status = await page.evaluate(async ({ name, password }) => (await fetch('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: name + '@timetable.test', password }),
  })).status, { name, password });
  expect(status).toBe(200);
  await page.reload();
  await expect(page.locator('.loading-state')).toHaveCount(0);
}
async function preview(page) {
  await page.getByRole('button', { name: '导入课表', exact: true }).click();
  await expect(page.locator('[data-timetable-file]')).toBeEnabled();
  await page.locator('[data-timetable-file]').setInputFiles({ name: 'schedule.xls', mimeType: 'application/vnd.ms-excel', buffer: Buffer.from(fixture.file, 'base64') });
  await expect(page.locator('.timetable-meeting')).toHaveCount(2);
  await expect(page.getByLabel('第一周的星期一')).toHaveValue('2026-09-07');
  await expect(page.getByLabel('最后上课日期')).toHaveValue('2026-12-25');
  await page.getByLabel('第一周的星期一').fill('2026-09-07');
}
test('real XLS upload, editable clocks, receipt, calendar, reload and deletion', async ({ page }, info) => {
  await preview(page);
  await expect(page.getByText('示例原理(Ⅰ)', { exact: true })).toBeVisible();
  await expect(page.locator('.timetable-preview')).toContainText('双周');
  await page.locator('[name^="start:"]').first().fill('08:10');
  await page.screenshot({ path: info.outputPath('timetable-preview.png'), fullPage: true });
  await page.getByRole('button', { name: '确认导入 2 个上课时段' }).click();
  await expect(page.locator('.timetable-saved article')).toHaveCount(1);
  await page.getByRole('button', { name: '关闭课表' }).click();
  await expect(page.locator('.timetable-day')).toContainText('08:10–09:50');
  await expect(page.locator('.timetable-day')).toContainText('示例楼101');
  await page.reload();
  await expect(page.locator('.timetable-day')).toContainText('示例原理(Ⅰ)');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('timetable-calendar.png'), fullPage: true });
  await page.getByRole('button', { name: '导入课表', exact: true }).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '删除课表' }).click();
  await expect(page.locator('.timetable-saved article')).toHaveCount(0);
  await page.getByRole('button', { name: '关闭课表' }).click();
  await expect(page.locator('.timetable-day')).toHaveCount(0);
});
test('invalid dates preserve preview, repeat imports do not duplicate', async ({ page }) => {
  await preview(page);
  await page.getByLabel('第一周的星期一').fill('2026-09-08');
  await page.getByRole('button', { name: '确认导入 2 个上课时段' }).click();
  await expect(page.locator('[data-timetable-panel] [role="alert"]')).toBeVisible();
  await expect(page.locator('.timetable-meeting')).toHaveCount(2);
  await page.getByLabel('第一周的星期一').fill('2026-09-07');
  await page.getByRole('button', { name: '确认导入 2 个上课时段' }).click();
  await expect(page.locator('.timetable-saved article')).toHaveCount(1);
  await page.locator('[data-timetable-file]').setInputFiles({ name: 'schedule.xls', mimeType: 'application/vnd.ms-excel', buffer: Buffer.from(fixture.file, 'base64') });
  await expect(page.locator('.timetable-meeting')).toHaveCount(2);
  await page.getByRole('button', { name: '确认导入 2 个上课时段' }).click();
  await expect(page.locator('.timetable-saved article')).toHaveCount(1);
  await expect(page.locator('#toast')).toContainText('无需重复添加');
});
test('saved classes are isolated from another account and return after re-login', async ({ page }) => {
  await preview(page);
  await page.getByRole('button', { name: '确认导入 2 个上课时段' }).click();
  await expect(page.locator('.timetable-saved article')).toHaveCount(1);
  await page.getByRole('button', { name: '关闭课表' }).click();
  await login(page, 'other');
  await expect(page.locator('.calendar-class')).toHaveCount(0);
  await page.getByRole('button', { name: '导入课表', exact: true }).click();
  await expect(page.locator('.timetable-saved article')).toHaveCount(0);
  await page.getByRole('button', { name: '关闭课表' }).click();
  await login(page, 'owner');
  await expect(page.locator('.timetable-day')).toContainText('示例原理(Ⅰ)');
});
test('exam remarks become a date-precision calendar event after classes end', async ({ page }, info) => {
  await preview(page);
  await expect(page.locator('.timetable-exam-note')).toContainText('2026-12-28 上午');
  await page.getByRole('button', { name: '确认导入 2 个上课时段' }).click();
  await expect(page.locator('.timetable-saved article')).toHaveCount(1);
  await page.getByRole('button', { name: '关闭课表' }).click();
  await page.getByLabel('跳转日期', { exact: true }).fill('2026-12-28');
  await expect(page.locator('.timetable-day')).toContainText('考试 · 上午');
  await expect(page.locator('.timetable-day')).not.toContainText('08:00');
  await expect(page.locator('.timetable-day')).not.toContainText('示例楼101');
  await page.screenshot({ path: info.outputPath('timetable-exam.png'), fullPage: true });
});
test('late upload preview never crosses account changes or reopens a closed panel', async ({ page }) => {
  let started, release;
  const requested = new Promise(resolve => { started = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/timetable/preview', async route => {
    const response = await route.fetch({ url: backend + '/api/timetable/preview' });
    started(); await gate;
    await route.fulfill({ response }).catch(() => {});
  });
  try {
    await page.getByRole('button', { name: '导入课表', exact: true }).click();
    await expect(page.locator('[data-timetable-file]')).toBeEnabled();
    await page.locator('[data-timetable-file]').setInputFiles({ name: 'schedule.xls', mimeType: 'application/vnd.ms-excel', buffer: Buffer.from(fixture.file, 'base64') });
    await requested;
    await page.evaluate(async password => {
      const store = await import('/src/team-store.js');
      await store.authenticate('login', { email: 'other@timetable.test', password });
    }, password);
    await expect(page.locator('#dialog')).not.toBeVisible();
    await page.getByRole('button', { name: '导入课表', exact: true }).click();
    await expect(page.locator('[data-timetable-file]')).toBeEnabled();
    release();
    await expect(page.locator('.timetable-meeting')).toHaveCount(0);
    await expect(page.locator('.timetable-saved article')).toHaveCount(0);
  } finally { release(); }
});
