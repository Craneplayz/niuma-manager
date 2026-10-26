import { test, expect } from '@playwright/test';

test.use({ timezoneId: 'Asia/Shanghai' });

async function openWorkspace(page, { failIgnoreOnce = false, failReadOnce = false, failSaveOnce = false } = {}) {
  await page.clock.setFixedTime(new Date('2026-10-20T04:00:00Z'));
  let version = 1, ignoredOnce = false, readOnce = false, savedOnce = false;
  const writes = [], resolutions = [];
  let tasks = [{ id: 'target', title: '数据结构实验报告', course: '数据结构', status: 'not_started', source: '教学网', due: { date: '2026-10-20', time: '18:00', precision: 'datetime' }, plans: [], subtasks: [] }];
  const make = (id, title, fields = {}) => ({ id, state: 'pending', version: 1, candidate: {
    input_id: 'source', title, description: '请核对任务要求', course_or_project: '数据结构', content_type: 'task', notice_type: 'create',
    deadline: { kind: 'deadline', status: 'explicit', precision: 'date', value: '2026-10-21' },
    sources: [{ input_id: 'source', platform: 'teaching_web', label: '教学网', url: null }],
    evidence: [{ input_id: 'source', quote: title }], review_reasons: ['请确认内容'], needs_review: true, ...fields,
  } });
  const batches = [{ request: { request_id: 'synthetic-review', contract_version: '0.2', records: [{ input_id: 'source', platform: 'teaching_web', text: '课程报告及时间变更通知。', source_label: '教学网' }] }, result: { status: 'success', errors: [] }, items: [
    make('new', '提交课程研究计划'),
    make('unknown', '准备课堂提纲', { deadline: { kind: 'deadline', status: 'inferred', precision: 'date', value: '2026-10-19' } }),
    make('change', '实验报告延期', { notice_type: 'postpone', target_reference: 'target' }),
    make('notice', '安全培训通知', { content_type: 'notice' }),
  ] }];
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value = {}, status = 200;
    if (path === '/api/auth/me') value = { user: { id: 'review-user', name: '合成账号' } };
    else if (path === '/api/workspace') value = { tasks: { version, value: tasks } };
    else if (path === '/api/data/tasks') { const body = request.postDataJSON(); writes.push(body); tasks = body.value; value = { version: ++version }; }
    else if (path === '/api/recognition/text-jobs') value = { contract_version: 'text-import-jobs/0.1', jobs: [] };
    else if (path === '/api/recognition/batches') {
      if (failReadOnce && !readOnce) { readOnce = true; status = 503; value = { error: 'Synthetic unavailable' }; }
      else value = batches;
    } else if (path.includes('/candidates/') && path.endsWith('/resolve')) {
      const id = decodeURIComponent(path.split('/')[4]), body = request.postDataJSON();
      resolutions.push({ id, ...body });
      if (failIgnoreOnce && body.action === 'ignore' && !ignoredOnce) { ignoredOnce = true; status = 503; value = { error: 'Synthetic unavailable' }; }
      else if (failSaveOnce && body.action === 'create' && !savedOnce) { savedOnce = true; status = 503; value = { error: 'Synthetic unavailable' }; }
      else {
        const item = batches[0].items.find(item => item.id === id);
        expect(body.version).toBe(item.version);
        item.state = body.action === 'ignore' ? 'ignored' : body.action === 'reopen' ? 'pending' : 'applied';
        item.version++;
        if (body.action === 'create') {
          tasks.push({ id: 'confirmed-' + id, ...body.draft, status: body.complete ? 'completed' : 'not_started', completedAt: body.complete ? '2026-10-20' : '', source: '教学网', plans: [], subtasks: [], recognitionHistory: [{ storageId: id, action: 'create' }] });
          version++;
        } else if (body.action === 'postpone') { tasks.find(task => task.id === body.target_id).due = body.draft.due; version++; }
        value = { batches };
      }
    } else if (path === '/api/usage/visit') value = { offerRating: false };
    else if (path === '/api/usage/guest-visit') value = { saved: true };
    else if (path === '/api/version') value = { environment: 'test', revision: 'synthetic' };
    else if (path === '/api/recognition/task-evidence') value = { evidence: [] };
    else if (path === '/api/sources/credentials') value = { configured: false };
    else if (path === '/api/sources/sync-runs') value = { contract_version: 'source-sync/0.1', runs: [] };
    else throw new Error('Unmocked API: ' + path);
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
  });
  await page.goto('/');
  return { writes, resolutions };
}

async function navigate(page, name) {
  if (await page.locator('#sidebar-show').isVisible()) await page.locator('#sidebar-show').click();
  await page.getByRole('button', { name, exact: true }).click();
}

test('task rows show pending drafts on first visit, with unconfirmed dates and inline changes', async ({ page }, info) => {
  const f = await openWorkspace(page);
  const row = page.locator('[data-review-id="new"]');
  await expect(row.getByText('待核对', { exact: true })).toBeVisible();
  await expect(row.getByRole('button', { name: '完成任务：提交课程研究计划' })).toBeEnabled();
  await expect(row.getByRole('button', { name: '忽略：提交课程研究计划' })).toBeVisible();
  const original = page.locator('[data-task-id="target"]');
  await expect(original.getByText('待核对变更', { exact: true })).toBeVisible();
  await expect(original.locator('.task-deadline')).toHaveText('今天 18:00');
  await expect(page.locator('[data-review-id="change"]')).toHaveCount(0);
  const uncertain = page.locator('[data-review-id="unknown"]');
  await expect(uncertain).not.toHaveClass(/is-overdue/);
  await expect(uncertain).toContainText('日期待核对');
  await expect(page.locator('.task-group').filter({ has: uncertain }).locator('h2')).toContainText('无截止日期');
  expect(f.writes).toEqual([]);
  expect(f.resolutions).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('pending-task-list.png'), fullPage: true });
});

test('ignore is one click, undo restores the same draft, and the import list stays synchronized', async ({ page }) => {
  const f = await openWorkspace(page);
  const row = page.locator('[data-review-id="new"]');
  await row.getByRole('button', { name: '忽略：提交课程研究计划' }).click();
  await expect(row).toHaveCount(0);
  await expect(page.locator('#dialog')).not.toBeVisible();
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(row).toBeVisible();
  expect(f.resolutions.map(item => [item.action, item.version])).toEqual([['ignore', 1], ['reopen', 2]]);
  expect(f.writes).toEqual([]);
  await navigate(page, '导入内容');
  await expect(page.locator('.candidate-row').filter({ hasText: '提交课程研究计划' })).toBeVisible();
});

test('ordinary date editing accepts a pending draft and later saves exclude other review rows', async ({ page }) => {
  const f = await openWorkspace(page);
  await page.locator('[data-review-id="new"] .task-open').click();
  const dialog = page.locator('#task-drawer');
  await expect(dialog.getByRole('heading', { name: /提交课程研究计划/ })).toBeVisible();
  expect(f.resolutions).toEqual([]);
  await dialog.getByRole('button', { name: '编辑', exact: true }).click();
  await dialog.getByLabel('截止日期', { exact: true }).fill('2026-10-25');
  await dialog.getByRole('button', { name: '保存任务', exact: true }).click();
  await expect(page.locator('[data-review-id="new"]')).toHaveCount(0);
  const confirmed = page.locator('[data-task-id="confirmed-new"]');
  await expect(confirmed).toBeVisible();
  await expect(confirmed).not.toContainText('待核对');
  await expect(confirmed.locator('.task-deadline')).toHaveText('10 月 25 日');
  await confirmed.getByRole('button', { name: '完成任务：提交课程研究计划' }).click();
  await expect.poll(() => f.writes.length).toBe(1);
  expect(f.writes[0].value.map(task => task.id).sort()).toEqual(['confirmed-new', 'target']);
  expect(f.resolutions[0].reviewed).toBe(true);
});

test('completing a pending task removes its badge and moves it to completed without a second write', async ({ page }) => {
  const f = await openWorkspace(page);
  const pending = page.locator('[data-review-id="new"]');
  await pending.getByRole('button', { name: '完成任务：提交课程研究计划' }).click();
  await expect(pending).toHaveCount(0);
  expect(f.writes).toEqual([]);
  expect(f.resolutions).toHaveLength(1);
  expect(f.resolutions[0].complete).toBe(true);
  await navigate(page, '已完成');
  const done = page.locator('[data-task-id="confirmed-new"]');
  await expect(done).toHaveClass(/is-completed/);
  await expect(done).not.toContainText('待核对');
  await done.getByRole('button', { name: '恢复任务：提交课程研究计划' }).click();
  await navigate(page, '全部任务');
  await expect(done).toBeVisible();
  await expect(done).not.toContainText('待核对');
});

test('viewing, cancelling and saving unchanged fields preserve pending status', async ({ page }) => {
  const f = await openWorkspace(page);
  const pending = page.locator('[data-review-id="new"]');
  await pending.locator('.task-open').click();
  const drawer = page.locator('#task-drawer');
  await drawer.getByRole('button', { name: '编辑', exact: true }).click();
  await drawer.getByLabel('任务名称', { exact: true }).fill('临时改名');
  await drawer.getByRole('button', { name: '取消', exact: true }).click();
  await drawer.getByRole('button', { name: '编辑', exact: true }).click();
  await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
  await expect(drawer.getByText('待核对', { exact: true })).toBeVisible();
  expect(f.resolutions).toEqual([]);
});

test('failed pending edits retain the badge and entered fields until retry succeeds', async ({ page }) => {
  const f = await openWorkspace(page, { failSaveOnce: true });
  const pending = page.locator('[data-review-id="new"]');
  await pending.locator('.task-open').click();
  const drawer = page.locator('#task-drawer');
  await drawer.getByRole('button', { name: '编辑', exact: true }).click();
  await drawer.getByLabel('任务名称', { exact: true }).fill('修改后的研究计划');
  await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
  await expect(drawer.locator('#task-edit-error .user-error')).toBeVisible();
  await expect(drawer.getByLabel('任务名称', { exact: true })).toHaveValue('修改后的研究计划');
  await expect(pending).toHaveCount(1);
  await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
  await expect(pending).toHaveCount(0);
  await expect(page.locator('[data-task-id="confirmed-new"]')).toContainText('修改后的研究计划');
  expect(f.resolutions).toHaveLength(2);
});

test('failed pending completion remains pending and can be retried', async ({ page }) => {
  await openWorkspace(page, { failSaveOnce: true });
  const pending = page.locator('[data-review-id="new"]');
  await pending.getByRole('button', { name: '完成任务：提交课程研究计划' }).click();
  await expect(page.locator('#toast .user-error')).toBeVisible();
  await expect(pending).toBeVisible();
  await pending.getByRole('button', { name: '完成任务：提交课程研究计划' }).click();
  await expect(pending).toHaveCount(0);
});

test('existing task date changes only after the attached change is explicitly confirmed', async ({ page }) => {
  await openWorkspace(page);
  const original = page.locator('[data-task-id="target"]');
  await original.locator('[data-import-review="change"]').click();
  const dialog = page.locator('#dialog');
  await expect(dialog.getByLabel('目标任务')).toHaveValue('target');
  await expect(original.locator('.task-deadline')).toHaveText('今天 18:00');
  await dialog.getByRole('button', { name: '确认保存', exact: true }).click();
  await expect(original.getByText('待核对变更')).toHaveCount(0);
  await expect(original.locator('.task-deadline')).toHaveText('明天');
});

test('ignore failures keep the row and offer a retry without claiming success', async ({ page }) => {
  await openWorkspace(page, { failIgnoreOnce: true });
  const row = page.locator('[data-review-id="new"]');
  await row.getByRole('button', { name: '忽略：提交课程研究计划' }).click();
  await expect(page.locator('#toast .user-error')).toBeVisible();
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: '忽略：提交课程研究计划' }).click();
  await expect(row).toHaveCount(0);
});

test('failed candidate reads stay visible and can retry without affecting saved tasks', async ({ page }) => {
  await openWorkspace(page, { failReadOnce: true });
  await expect(page.locator('[data-task-id="target"]')).toBeVisible();
  await page.getByRole('button', { name: '重试读取待核对' }).click();
  await expect(page.locator('[data-review-id="new"]')).toBeVisible();
  await expect(page.getByRole('button', { name: '重试读取待核对' })).toHaveCount(0);
});

test('pending drafts share search and course filters while notices stay in their own list', async ({ page }) => {
  await openWorkspace(page);
  await expect(page.locator('[data-review-id="new"]')).toBeVisible();
  await page.getByRole('searchbox', { name: '搜索任务' }).fill('研究计划');
  await expect(page.locator('[data-review-id]')).toHaveCount(1);
  await page.getByRole('searchbox', { name: '搜索任务' }).fill('');
  await page.getByLabel('课程筛选', { exact: true }).selectOption('数据结构');
  await expect(page.locator('[data-review-id="new"]')).toBeVisible();
  await expect(page.locator('[data-review-id="notice"]')).toHaveCount(0);
  await navigate(page, '通知');
  await expect(page.locator('[data-review-id="notice"]')).toBeVisible();
  await expect(page.locator('[data-review-id="new"]')).toHaveCount(0);
});
