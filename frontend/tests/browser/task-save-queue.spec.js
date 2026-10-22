import { test, expect } from '@playwright/test';

async function openApp(page, { firstStatus = 200 } = {}) {
  let version = 1, reads = 0, release;
  const gate = new Promise(resolve => { release = resolve; });
  let tasks = ['a', 'b', 'c', 'd'].map(id => ({
    id, title: `合成任务 ${id}`, status: 'not_started', due: null, plans: [], subtasks: [],
    sourceUrl: 'https://example.test/course', recognitionHistory: [{ candidateId: id }],
  }));
  const writes = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let value = {}, status = 200;
    if (path === '/api/auth/me') value = { user: { id: 'synthetic-queue', name: '合成账号', emailVerified: true } };
    else if (path === '/api/workspace') { reads++; value = { tasks: { version, value: tasks } }; }
    else if (path === '/api/data/tasks') {
      const body = route.request().postDataJSON(); writes.push(body);
      if (writes.length === 1) { await gate; status = firstStatus; }
      if (status === 200) {
        expect(body.version).toBe(version);
        tasks = body.value; value = { version: ++version };
      } else value = { error: status === 409 ? '数据已在其他窗口修改' : '合成暂时保存失败' };
    } else if (path === '/api/recognition/batches') value = [];
    else if (path === '/api/recognition/task-evidence') value = { evidence: [] };
    else if (path === '/api/usage/guest-visit') value = {};
    else if (path === '/api/usage/visit') value = { offerRating: false };
    else if (path === '/api/version') value = { environment: 'test', revision: 'synthetic' };
    else throw new Error('Unexpected API ' + path);
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
  });
  await page.goto('/');
  await expect(page.locator('[data-task-id="a"]')).toBeVisible();
  return { writes, release, get tasks() { return tasks; }, get reads() { return reads; } };
}
const row = (page, id) => page.locator(`[data-task-id="${id}"]`);
const drawer = page => page.locator('#task-drawer');
async function edit(page, id, title) {
  await row(page, id).locator('.task-open').click();
  await drawer(page).getByRole('button', { name: '编辑', exact: true }).click();
  await drawer(page).getByLabel('任务名称', { exact: true }).fill(title);
  await drawer(page).getByRole('button', { name: '保存任务', exact: true }).click();
  await expect(drawer(page)).not.toBeVisible();
}
async function remove(page, id) {
  await row(page, id).locator('.task-open').click();
  await drawer(page).getByRole('button', { name: '移入回收站', exact: true }).click();
  await expect(drawer(page)).not.toBeVisible();
  await expect(row(page, id)).toHaveCount(0);
}
async function queuedEdits(page, f) {
  await row(page, 'a').locator('[data-task-complete]').click();
  await expect.poll(() => f.writes.length).toBe(1);
  await expect(row(page, 'b').locator('[data-task-complete]')).toBeEnabled();
  await edit(page, 'b', '编辑后仍可继续操作');
  await remove(page, 'c');
  expect(f.writes).toHaveLength(1);
}

test('slow saving permits completion, editing and deletion of different tasks without losing an open draft', async ({ page }, info) => {
  const f = await openApp(page);
  try {
    await queuedEdits(page, f);
    await row(page, 'b').locator('.task-open').click();
    await expect(drawer(page).getByRole('button', { name: '编辑', exact: true })).toBeDisabled();
    await expect(drawer(page).getByRole('button', { name: '关闭任务详情' })).toBeEnabled();
    await drawer(page).getByRole('button', { name: '关闭任务详情' }).click();
    await row(page, 'd').locator('.task-open').click();
    await drawer(page).getByRole('button', { name: '编辑', exact: true }).click();
    await drawer(page).getByLabel('任务名称', { exact: true }).fill('仍未提交的另一份草稿');
    await page.screenshot({ path: info.outputPath('queued-task-edits.png'), fullPage: true });
    const untouchedRow = await row(page, 'd').elementHandle();
    f.release();
    await expect.poll(() => f.writes.length).toBe(3);
    await expect(page.locator('#toast')).toContainText('已移入回收站');
    await expect(drawer(page).getByLabel('任务名称', { exact: true })).toHaveValue('仍未提交的另一份草稿');
    await expect(drawer(page)).toBeVisible();
    expect(await untouchedRow.evaluate(node => node.isConnected)).toBe(true);
    expect(f.reads).toBe(1);
    expect(f.writes.map(write => write.version)).toEqual([1, 2, 3]);
    expect(f.tasks.find(task => task.id === 'a').status).toBe('completed');
    expect(f.tasks.find(task => task.id === 'b').title).toBe('编辑后仍可继续操作');
    expect(f.tasks.find(task => task.id === 'c').recycleBin).toBeTruthy();
    expect(f.tasks.every(task => task.sourceUrl === 'https://example.test/course')).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { f.release(); }
});

test('failed save keeps all queued changes and explicit retry saves all of them', async ({ page }) => {
  const f = await openApp(page, { firstStatus: 503 });
  try {
    await queuedEdits(page, f); f.release();
    await expect(page.getByRole('button', { name: '重试保存', exact: true })).toBeVisible();
    expect(f.writes).toHaveLength(1);
    await expect(row(page, 'a')).toHaveCount(0);
    await expect(row(page, 'b')).toContainText('编辑后仍可继续操作');
    await expect(page.locator('#toast')).not.toContainText('任务已保存');
    await page.getByRole('button', { name: '重试保存', exact: true }).click();
    await expect(page.locator('#toast')).toContainText('已保存');
    await expect(page.locator('#sync-alert')).toBeEmpty();
    expect(f.writes).toHaveLength(4);
    expect(f.tasks.find(task => task.id === 'a').status).toBe('completed');
    expect(f.tasks.find(task => task.id === 'b').title).toBe('编辑后仍可继续操作');
    expect(f.tasks.find(task => task.id === 'c').recycleBin).toBeTruthy();
  } finally { f.release(); }
});

test('version conflicts require rereading and discard queued changes without overwriting remote data', async ({ page }) => {
  const f = await openApp(page, { firstStatus: 409 });
  try {
    await queuedEdits(page, f); f.release();
    await page.getByRole('button', { name: '重新读取', exact: true }).click();
    await expect(row(page, 'a')).toBeVisible();
    await expect(row(page, 'b')).toContainText('合成任务 b');
    await expect(row(page, 'c')).toBeVisible();
    expect(f.writes).toHaveLength(1);
    await expect(row(page, 'd').locator('[data-task-complete]')).toBeEnabled();
  } finally { f.release(); }
});

test('deletion undo restores the task without reloading the workspace or reopening a closed dialog', async ({ page }) => {
  const f = await openApp(page); f.release();
  await remove(page, 'a');
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(row(page, 'a')).toBeVisible();
  await expect.poll(() => f.writes.length).toBe(2);
  expect(f.tasks.find(task => task.id === 'a').recycleBin).toBeUndefined();
  expect(f.reads).toBe(1);
  await expect(page.locator('#dialog')).not.toBeVisible();
});

test('trash recovery becomes available as soon as the pending deletion is acknowledged', async ({ page }) => {
  const f = await openApp(page);
  try {
    await remove(page, 'a');
    await page.locator('[data-action="open-trash"]').click();
    const recovery = page.locator('[data-restore-task="a"]');
    await expect(recovery).toBeDisabled();
    f.release();
    await expect(recovery).toBeEnabled();
    await recovery.click();
    await page.locator('[data-action="dialog-close"]').click();
    await expect(row(page, 'a')).toBeVisible();
    await expect(page.locator('#toast')).toContainText('任务已恢复');
    await expect(page.locator('#dialog')).not.toBeVisible();
    expect(f.tasks.find(task => task.id === 'a').recycleBin).toBeUndefined();
  } finally { f.release(); }
});

test('new tasks get stable unique identities while earlier operations are still saving', async ({ page }) => {
  const f = await openApp(page);
  try {
    await row(page, 'a').locator('[data-task-complete]').click();
    await expect.poll(() => f.writes.length).toBe(1);
    for (const title of ['排队添加一', '排队添加二']) {
      await page.locator('[data-action="task-new"]').click();
      await drawer(page).getByLabel('任务名称', { exact: true }).fill(title);
      await drawer(page).getByRole('button', { name: '保存任务', exact: true }).click();
      await expect(drawer(page)).not.toBeVisible();
      await expect(page.locator('.task-title').filter({ hasText: title })).toBeVisible();
    }
    expect(f.writes).toHaveLength(1); f.release();
    await expect.poll(() => f.tasks.length).toBe(6);
    expect(new Set(f.tasks.map(task => task.id)).size).toBe(6);
    expect(f.tasks.slice(-2).map(task => task.title)).toEqual(['排队添加一', '排队添加二']);
    expect(f.tasks.slice(-2).every(task => Array.isArray(task.plans) && Array.isArray(task.subtasks))).toBe(true);
  } finally { f.release(); }
});
