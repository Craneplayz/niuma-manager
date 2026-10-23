import { test, expect } from '@playwright/test';

test.use({ timezoneId: 'Asia/Shanghai' });

async function openWorkspace(page, { failIgnoreOnce = false, failReadOnce = false, failSaveOnce = false, holdResolve = false, holdRead = false } = {}) {
  await page.clock.setFixedTime(new Date('2026-10-20T04:00:00Z'));
  let version = 1, ignoredOnce = false, readOnce = false, savedOnce = false;
  let releaseResolve;
  const resolveGate = new Promise(resolve => { releaseResolve = resolve; });
  let releaseRead, enterRead;
  const readGate = new Promise(resolve => { releaseRead = resolve; });
  const readStarted = new Promise(resolve => { enterRead = resolve; });
  const writes = [], resolutions = [], receipts = new Set();
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
    else if (path === '/api/data/tasks') {
      const body = request.postDataJSON(); writes.push(body);
      if (receipts.has(body.operation?.id)) value = { version, value: tasks, operationId: body.operation.id, replayed: true };
      else {
        expect(body.version).toBe(version);
        tasks = body.value; receipts.add(body.operation.id);
        value = { version: ++version, operationId: body.operation.id };
      }
    }
    else if (path === '/api/recognition/text-jobs') value = { jobs: [] };
    else if (path === '/api/recognition/batches') {
      enterRead();
      if (holdRead) await readGate;
      if (failReadOnce && !readOnce) { readOnce = true; status = 503; value = { error: 'Synthetic unavailable' }; }
      else value = batches;
    } else if (path.includes('/candidates/') && path.endsWith('/resolve')) {
      const id = decodeURIComponent(path.split('/')[4]), body = request.postDataJSON();
      resolutions.push({ id, ...body });
      if (holdResolve) await resolveGate;
      if (failIgnoreOnce && body.action === 'ignore' && !ignoredOnce) { ignoredOnce = true; status = 503; value = { error: 'Synthetic unavailable' }; }
      else if (failSaveOnce && body.action === 'create' && !savedOnce) { savedOnce = true; status = 503; value = { error: 'Synthetic unavailable' }; }
      else if (receipts.has(body.operation?.id)) value = { operationId: body.operation.id, replayed: true, generation: 0, tasks: { value: tasks, version }, candidates: batches[0].items.map(({ id, state, version, taskId }) => ({ id, state, version, ...(taskId ? { taskId } : {}) })) };
      else {
        const item = batches[0].items.find(item => item.id === id);
        expect(body.operation?.intent.kind).toBe('review');
        expect(body.operation.intent.candidateId).toBe(id);
        expect(body.version).toBe(item.version);
        item.state = body.action === 'ignore' ? 'ignored' : body.action === 'reopen' ? 'pending' : 'applied';
        item.version++;
        if (body.action === 'create') {
          tasks.push({ id: body.operation.intent.newTaskId, ...body.draft, status: body.complete ? 'completed' : 'not_started', completedAt: body.complete ? '2026-10-20' : '', source: '教学网', plans: [], subtasks: [], recognitionHistory: [{ storageId: id, action: 'create' }] });
          version++;
        } else if (body.action === 'postpone') { tasks.find(task => task.id === body.target_id).due = body.draft.due; version++; }
        const taskId = body.operation?.intent.newTaskId || body.target_id;
        if (taskId) item.taskId = taskId;
        receipts.add(body.operation.id);
        value = { batches, operationId: body.operation.id, generation: 0, tasks: { value: tasks, version },
          candidates: [{ id: item.id, state: item.state, version: item.version, ...(item.taskId ? { taskId: item.taskId } : {}) }] };
      }
    } else if (path === '/api/tasks/reconcile') value = { applied: request.postDataJSON().operations.filter(operation => receipts.has(operation.id)).map(operation => operation.id), tasks: { value: tasks, version }, generation: 0, candidates: batches[0].items.map(({ id, state, version, taskId }) => ({ id, state, version, ...(taskId ? { taskId } : {}) })) };
    else if (path === '/api/usage/guest-visit') value = {};
    else if (path === '/api/usage/visit') value = { offerRating: false };
    else if (path === '/api/version') value = { environment: 'test', revision: 'synthetic' };
    else if (path === '/api/recognition/task-evidence') value = { evidence: [] };
    else if (path === '/api/sources/credentials') value = { configured: false };
    else if (path === '/api/sources/sync-runs') value = { contract_version: 'source-sync/0.1', runs: [] };
    else throw new Error('Unmocked API: ' + path);
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
  });
  await page.goto('/');
  return { writes, resolutions, releaseResolve, releaseRead, readStarted };
}

async function createdTaskId(f, candidateId = 'new') {
  await expect.poll(() => f.resolutions.find(item => item.id === candidateId)?.operation?.intent.newTaskId).toBeTruthy();
  return f.resolutions.find(item => item.id === candidateId).operation.intent.newTaskId;
}

async function createdTaskRow(page, f, candidateId = 'new') {
  return page.locator(`[data-task-id="${await createdTaskId(f, candidateId)}"]`);
}

async function navigate(page, name) {
  if (await page.locator('#sidebar-show').isVisible()) await page.locator('#sidebar-show').click();
  await page.getByRole('button', { name, exact: true }).click();
}

test('first task list waits for pending rows and publishes the complete count together', async ({ page }, info) => {
  const f = await openWorkspace(page, { holdRead: true });
  try {
    await f.readStarted;
    await expect(page.locator('main [role="status"]')).toHaveText('正在读取任务…');
    await expect(page.locator('[data-task-id="target"]')).toHaveCount(0);
    await expect(page.locator('[data-scope="all"] .nav-count')).toHaveText('…');
    await expect(page.locator('#result-count')).toHaveCount(0);
    await page.screenshot({ path: info.outputPath('complete-list-loading.png'), fullPage: true });
    f.releaseRead();
    await expect(page.locator('[data-task-id="target"]')).toBeVisible();
    await expect(page.locator('[data-review-id="new"]')).toBeVisible();
    await expect(page.locator('#result-count')).toHaveText('3');
    await expect(page.locator('[data-scope="all"] .nav-count')).toHaveText('3');
    await page.screenshot({ path: info.outputPath('complete-list-ready.png'), fullPage: true });
    expect(f.writes).toEqual([]);
    expect(f.resolutions).toEqual([]);
  } finally { f.releaseRead(); }
});

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
  await expect.poll(() => f.resolutions.length).toBe(2);
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
  const confirmed = await createdTaskRow(page, f);
  await expect(confirmed).toBeVisible();
  await expect(confirmed).not.toContainText('待核对');
  await expect(confirmed.locator('.task-deadline')).toHaveText('10 月 25 日');
  await confirmed.getByRole('button', { name: '完成任务：提交课程研究计划' }).click();
  await expect.poll(() => f.writes.length).toBe(1);
  expect(f.writes[0].value.map(task => task.id).sort()).toEqual([f.resolutions.find(item => item.id === 'new')?.operation.intent.newTaskId, 'target']);
  expect(f.resolutions[0].reviewed).toBe(true);
});

test('completing a pending task removes its badge and moves it to completed without a second write', async ({ page }) => {
  const f = await openWorkspace(page);
  const pending = page.locator('[data-review-id="new"]');
  await pending.getByRole('button', { name: '完成任务：提交课程研究计划' }).click();
  await expect(pending).toHaveCount(0);
  expect(f.writes).toEqual([]);
  await expect.poll(() => f.resolutions.length).toBe(1);
  expect(f.resolutions[0].complete).toBe(true);
  await navigate(page, '已完成');
  const done = await createdTaskRow(page, f);
  await expect(done).toHaveClass(/is-completed/);
  await expect(done).not.toContainText('待核对');
  await done.getByRole('button', { name: '恢复任务：提交课程研究计划' }).click();
  await navigate(page, '全部任务');
  await expect(done).toBeVisible();
  await expect(done).not.toContainText('待核对');
});

test('undo after pending completion uses the shared ordinary status action', async ({ page }) => {
  const f = await openWorkspace(page);
  await page.locator('[data-review-id="new"] [data-task-complete]').click();
  await expect(page.locator('[data-review-id="new"]')).toHaveCount(0);
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(await createdTaskRow(page, f)).toBeVisible();
  expect(f.resolutions).toHaveLength(1);
  await expect.poll(() => f.writes.length).toBe(1);
  expect(f.writes[0].value.find(task => task.id === f.resolutions.find(item => item.id === 'new')?.operation.intent.newTaskId).status).toBe('not_started');
  expect(f.writes[0].value.some(task => task.id.startsWith('review:'))).toBe(false);
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

test('failed pending edits retain the staged task and retry the same identity', async ({ page }) => {
  const f = await openWorkspace(page, { failSaveOnce: true });
  const pending = page.locator('[data-review-id="new"]');
  await pending.locator('.task-open').click();
  const drawer = page.locator('#task-drawer');
  await drawer.getByRole('button', { name: '编辑', exact: true }).click();
  await drawer.getByLabel('任务名称', { exact: true }).fill('修改后的研究计划');
  await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
  await expect(pending).toHaveCount(0);
  await expect(drawer).not.toBeVisible();
  const confirmed = await createdTaskRow(page, f), taskId = await createdTaskId(f);
  await expect(confirmed).toContainText('修改后的研究计划');
  await expect(page.locator('#sync-alert .user-error')).toBeVisible();
  await page.getByRole('button', { name: '重试保存', exact: true }).click();
  await expect(page.locator('#sync-alert')).toBeEmpty();
  await expect(confirmed).toContainText('修改后的研究计划');
  expect(f.resolutions).toHaveLength(2);
  expect(f.resolutions[1].operation.id).toBe(f.resolutions[0].operation.id);
  expect(f.resolutions[1].operation.intent.newTaskId).toBe(taskId);
  await expect(page.locator(`[data-task-id="${taskId}"]`)).toHaveCount(1);
  expect(f.writes).toEqual([]);
});

test('failed pending completion remains staged and retries without recreating the candidate', async ({ page }) => {
  const f = await openWorkspace(page, { failSaveOnce: true });
  const pending = page.locator('[data-review-id="new"]');
  await pending.getByRole('button', { name: '完成任务：提交课程研究计划' }).click();
  await expect(pending).toHaveCount(0);
  await expect(page.locator('#sync-alert .user-error')).toBeVisible();
  const taskId = await createdTaskId(f);
  await navigate(page, '已完成');
  const done = await createdTaskRow(page, f);
  await expect(done).toHaveClass(/is-completed/);
  await expect(done).not.toContainText('待核对');
  await page.getByRole('button', { name: '重试保存', exact: true }).click();
  await expect(page.locator('#sync-alert')).toBeEmpty();
  await expect(done).toHaveClass(/is-completed/);
  expect(f.resolutions).toHaveLength(2);
  expect(f.resolutions[1].operation.id).toBe(f.resolutions[0].operation.id);
  expect(f.resolutions[1].operation.intent.newTaskId).toBe(taskId);
  expect(f.writes).toEqual([]);
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

test('ignore failures retain the staged removal and retry the same candidate operation', async ({ page }) => {
  const f = await openWorkspace(page, { failIgnoreOnce: true });
  const row = page.locator('[data-review-id="new"]');
  await row.getByRole('button', { name: '忽略：提交课程研究计划' }).click();
  await expect(row).toHaveCount(0);
  await expect(page.locator('#sync-alert .user-error')).toBeVisible();
  await page.getByRole('button', { name: '重试保存', exact: true }).click();
  await expect(page.locator('#sync-alert')).toBeEmpty();
  await expect(row).toHaveCount(0);
  expect(f.resolutions).toHaveLength(2);
  expect(f.resolutions.map(item => [item.action, item.version])).toEqual([['ignore', 1], ['ignore', 1]]);
  expect(f.resolutions[1].operation.id).toBe(f.resolutions[0].operation.id);
  expect(f.writes).toEqual([]);
});

test('failed candidate reads stay visible and can retry without affecting saved tasks', async ({ page }) => {
  await openWorkspace(page, { failReadOnce: true });
  await expect(page.locator('[data-task-id="target"]')).toBeVisible();
  await expect(page.getByText('待核对任务尚未读取，当前列表和数量可能不完整。')).toBeVisible();
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


test('pending candidate confirmation allows ordinary tasks to queue behind its atomic write', async ({ page }) => {
  const f = await openWorkspace(page, { holdResolve: true });
  try {
    await page.locator('[data-review-id="new"] [data-task-complete]').click();
    await expect.poll(() => f.resolutions.length).toBe(1);
    await expect(page.locator('[data-task-id="target"] [data-task-complete]')).toBeEnabled();
    await page.locator('[data-task-open="target"]').click();
    await expect(page.locator('#task-drawer')).toBeVisible();
    await page.locator('[data-action="drawer-close"]').click();
    await page.locator('[data-task-id="target"] [data-task-complete]').click();
    expect(f.writes).toHaveLength(0);
    f.releaseResolve();
    await expect.poll(() => f.writes.length).toBe(1);
    expect(f.writes[0].value.find(task => task.id === f.resolutions.find(item => item.id === 'new')?.operation.intent.newTaskId).status).toBe('completed');
    expect(f.writes[0].value.find(task => task.id === 'target').status).toBe('completed');
  } finally { f.releaseResolve(); }
});
