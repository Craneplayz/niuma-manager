import { test, expect } from '@playwright/test';

test.use({ timezoneId: 'Asia/Shanghai' });
test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-11-18T04:00:00Z'));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/version') return route.fulfill({ json: { environment: 'test', revision: 'quick-entry' } });
    if (path === '/api/usage/guest-visit') return route.fulfill({ json: {} });
    throw new Error('Unexpected API in local quick entry: ' + path);
  });
  await page.goto('/?demo=1');
});

test('typed time is highlighted in place, saved once, and editable in the main list', async ({ page }, info) => {
  await page.locator('.add-task').click();
  const form = page.locator('#quick-task-form');
  const input = form.locator('#quick-text');
  await expect(input).toBeFocused();
  await input.fill('明天早上8点上数算课');
  await expect(form.locator('.quick-mirror mark')).toHaveText('明天早上8点');
  await expect(form.locator('#quick-date-button')).toContainText('明天，08:00');
  await expect(page.locator('.task-title').filter({ hasText: '上数算课' })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('quick-entry.png') });
  await form.locator('#quick-course-button').click();
  await form.locator('#quick-course').fill('数算');
  await form.getByRole('button', { name: '添加', exact: true }).click();
  const row = page.locator('.task-row[data-just-added]');
  await expect(row.locator('.task-title')).toHaveText('上数算课');
  await expect(row.locator('.task-deadline')).toHaveText('明天 08:00');
  await expect(row.locator('.course-name')).toHaveText('数算');
  await expect(row.locator("xpath=..")).toContainText("活动安排");
  await row.locator('.task-open').click();
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  await expect(page.locator('#task-edit-form [name=due]')).toHaveValue('2026-11-19');
  await page.locator('#task-edit-form [name=title]').fill('数算讨论课');
  await page.getByRole('button', { name: '保存任务', exact: true }).click();
  await expect(row.locator('.task-title')).toHaveText('数算讨论课');
});

test('calendar defaults and manual date edits match what is saved', async ({ page }) => {
  await page.locator('[data-view=calendar]').click();
  await page.locator('.calendar-date-button[data-calendar-day="2026-11-22"]').click();
  const inAgenda = page.locator('#agenda-add-task');
  await (await inAgenda.isVisible() ? inAgenda : page.locator('.add-task')).click();
  await page.locator('#quick-text').fill('准备材料');
  await expect(page.locator('#quick-date-button')).toHaveAttribute('title','2026-11-22');
  await page.locator('#quick-date-button').click();
  await page.locator('#quick-date').fill('2026-11-23');
  await page.locator('#quick-time').fill('15:45');
  await page.locator('#quick-text').press('Enter');
  await expect(page.locator('.day-panel [data-just-added]')).toContainText('15:45');
  await expect(page).toHaveURL(/date=2026-11-23/);
});

test('quick entry preserves filters and keeps an out-of-filter result accessible', async ({ page }) => {
  await page.locator('#task-search').fill('概率论');
  await page.locator('.add-task').click();
  await page.locator('#quick-text').fill('周三或周五交资料');
  await expect(page.locator('.quick-mirror mark')).toHaveCount(0);
  await page.locator('#quick-text').press('Enter');
  await expect(page.locator('#task-search')).toHaveValue('概率论');
  await expect(page.locator('.quick-outside-result .task-title')).toHaveText('周三或周五交资料');
  await expect(page.locator('.quick-outside-result .task-deadline')).toHaveText('无截止日期');
  await page.locator('.add-task').click();
  await page.locator('#quick-more').click();
  await page.locator('[data-quick-import="files"]').click();
  await expect(page).toHaveURL(/importSource=files/);
});

test('a composer left open across midnight resolves tomorrow from the current day', async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-11-18T15:59:00Z'));
  await page.locator('.add-task').click();
  await page.locator('#quick-text').fill('明早8点交报告');
  await expect(page.locator('#quick-date-button')).toHaveAttribute('title', '2026-11-19 08:00');
  await page.clock.setFixedTime(new Date('2026-11-18T16:01:00Z'));
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.locator('#quick-date-button')).toHaveAttribute('title','2026-11-20 08:00');
  await page.locator('#quick-text').press('Enter');
  await expect(page.locator('[data-just-added]')).toContainText('明天 08:00');
  await page.locator('[data-just-added] .task-open').click();
  await page.getByRole('button', { name: '编辑', exact: true }).click();
  await expect(page.locator('#task-edit-form [name=due]')).toHaveValue('2026-11-20');
});
