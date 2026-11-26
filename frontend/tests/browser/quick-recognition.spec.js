import { test, expect } from '@playwright/test';

test.use({ timezoneId: 'Asia/Shanghai' });

const original = '明天整理一下实验数据';
async function quickWorkspace(page) {
  const pending = { id: 'legacy', title: '整理一下实验数据', status: 'not_started', course: null, due: null, description: '',
    quickRecognition: { text: original, status: 'pending' } };
  let tasks = [pending], version = 1, missNextWrite = false;
  const owner = 'quick-test', writes = [], polls = [];
  const canonical = (task, status) => {
    const quick = task.quickRecognition;
    if (status === 'failed') return { ...task, quickRecognition: { text: quick.text, status } };
    return { ...task, title: '整理实验数据', course: '物理实验', quickRecognition: {
      text: quick.text, status: 'done', appliedFrom: { title: task.title, course: task.course }, applied: { title: '整理实验数据', course: '物理实验' },
    } };
  };
  const finish = (status = 'done') => {
    tasks = tasks.map(task => task.quickRecognition?.status === 'pending' ? canonical(task, status) : task);
    version++;
  };
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let json, status = 200;
    if (path === '/api/auth/me') json = { user: { id: owner, name: '测试账号', emailVerified: true } };
    else if (path === '/api/workspace') json = { tasks: { value: tasks, version }, taskWriteOwner: owner, taskWriteGeneration: 0 };
    else if (path === '/api/tasks/quick-recognition') {
      polls.push(version);
      json = { owner, generation: 0, version, value: tasks };
    } else if (path === '/api/data/tasks') {
      const body = route.request().postDataJSON();
      writes.push(body);
      if (missNextWrite) { missNextWrite = false; finish(); }
      if (body.version !== version) {
        status = 409;
        json = { code: 'quick_recognition_changed', error: '后台已更新任务', quickVersion: version };
      } else {
        tasks = body.value;
        json = { version: ++version, value: tasks, operationId: body.operation.id };
      }
    } else if (path === '/api/recognition/batches') json = [];
    else if (path === '/api/usage/visit') json = { offerRating: false };
    else if (path === '/api/version') json = { environment: 'test', revision: 'quick-recognition' };
    else json = {};
    await route.fulfill({ status, json });
  });
  await page.goto('/');
  await expect(page.locator('.add-task')).toBeVisible();
  return { writes, polls, finish, finishDuringWrite() { missNextWrite = true; }, get tasks() { return tasks; } };
}
test('an existing pending task still updates in its open detail and list row', async ({ page }) => {
  const api = await quickWorkspace(page);
  const row = page.locator('.task-row[data-task-id="legacy"]');
  await row.locator('.task-open').click();
  const drawer = page.locator('#task-drawer');
  await expect(drawer.getByRole('status')).toContainText('AI 正在识别');
  await expect(drawer.locator('.quick-original')).toHaveText(original);
  await expect(drawer.getByRole('button', { name: '编辑', exact: true })).toBeEnabled();
  api.finish();
  await expect(drawer.locator('#drawer-heading')).toHaveText('整理实验数据');
  await expect(drawer.locator('.course-name')).toHaveText('物理实验');
  await expect(row.locator('.task-title')).toHaveText('整理实验数据');
  await expect(row).not.toContainText('AI 识别中');
  expect(api.tasks).toHaveLength(1);
});

test('refresh discovers an unfinished job; failure keeps the original editable task', async ({ page }) => {
  const api = await quickWorkspace(page);
  await expect.poll(() => api.tasks.length).toBe(1);
  await page.reload();
  const row = page.locator('.task-row');
  await expect(row).toContainText('AI 识别中');
  api.finish('failed');
  await row.locator('.task-open').click();
  const drawer = page.locator('#task-drawer');
  await expect(drawer.getByRole('status')).toContainText('已保留原任务');
  await expect(drawer.locator('.quick-original')).toHaveText(original);
  await drawer.getByRole('button', { name: '编辑', exact: true }).click();
  await expect(drawer.locator('[name=title]')).toHaveValue('整理一下实验数据');
  expect(api.writes).toHaveLength(0);
});

for (const timing of ['while editing', 'during save']) {
  test('preserves user edits while applying untouched AI fields ' + timing, async ({ page }) => {
    const api = await quickWorkspace(page);
    const row = page.locator('.task-row[data-task-id="legacy"]');
    await row.locator('.task-open').click();
    const drawer = page.locator('#task-drawer');
    await drawer.getByRole('button', { name: '编辑', exact: true }).click();
    await drawer.locator('[name=title]').fill('我自己修改后的标题');
    if (timing === 'while editing') {
      api.finish();
      await expect(row.locator('.task-title')).toHaveText('整理实验数据');
      await expect(drawer.locator('[name=title]')).toHaveValue('我自己修改后的标题');
    } else api.finishDuringWrite();
    await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
    await expect(drawer).not.toBeVisible();
    await expect.poll(() => [api.tasks[0].title, api.tasks[0].course]).toEqual(['我自己修改后的标题', '物理实验']);
    await row.locator('.task-open').click();
    await expect(drawer.locator('#drawer-heading')).toHaveText('我自己修改后的标题');
    await expect(drawer.locator('.course-name')).toHaveText('物理实验');
    if (timing === 'during save') expect(api.writes.at(-1).operation.id).toEqual(api.writes.at(-2).operation.id);
  });
}
