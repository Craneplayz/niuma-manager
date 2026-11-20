import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const splitting = JSON.parse(readFileSync(new URL('../../../contracts/task-decomposition/examples-v0.1.json', import.meta.url)));
const planning = JSON.parse(readFileSync(new URL('../../../contracts/task-planning/api-examples.json', import.meta.url)));
const date = '2026-12-18';
const task = (id, title) => ({ id, title, due: { date, precision: 'date' }, status: 'not_started', subtasks: [], plans: [] });

async function setup(page, options = {}) {
  let version = 1, tasks = [task('project', '课程汇报'), task('atomic', '提交作业')];
  tasks[0].plans = [{ id: 'previous', title: '已有排练', startAt: '2026-12-17T18:00:00+08:00', endAt: '2026-12-17T18:30:00+08:00' }];
  if (options.replaceWithSingleStep) tasks[1].subtasks = [
    { id: 'draft', title: '整理初稿', status: 'completed', dependsOn: [] },
    { id: 'revise', title: '再次修改报告', status: 'pending', dependsOn: ['draft'] },
  ];
  const requests = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() || {};
    let json = {}, status = 200;
    if (path === '/api/auth/me') json = { user: { id: 'learner', name: '测试学生', emailVerified: true } };
    else if (path === '/api/workspace') json = { tasks: { value: tasks, version }, availability: { value: { startTime: '16:00', endTime: '17:15', weekdays: [5] } } };
    else if (path === '/api/recognition/batches') json = [];
    else if (path === '/api/recognition/task-evidence') json = { evidence: [] };
    else if (path === '/api/decomposition/proposals') {
      requests.push({ path, body });
      if (options.gate && body.taskId === 'project') await options.gate;
      json = body.taskId === 'atomic' ? splitting.no_split.response : body.conversation?.length ? splitting.ready.response : splitting.clarification.response;
      if (body.taskId === 'atomic' && options.replaceWithSingleStep) json = { ...json, requestId: 'replace-remaining' };
    } else if (path === '/api/decomposition/apply') {
      requests.push({ path, body });
      if (body.requestId === 'replace-remaining' && body.taskId === 'atomic') {
        tasks[1].subtasks = [tasks[1].subtasks[0], { id: 'submit', title: '提交报告', estimatedMinutes: 5, status: 'pending', dependsOn: [] }];
      } else tasks[0].subtasks = [{ id: 'intro', title: '完成任务页', outcome: '可以演示', estimatedMinutes: 80, status: 'pending', dependsOn: [] },
        { id: 'ppt', title: '制作演示文稿', estimatedMinutes: 40, status: 'pending', dependsOn: ['intro'] }];
      json = { value: tasks, version: ++version };
    } else if (path === '/api/decomposition/steps/status') {
      requests.push({ path, body });
      expect(body.version).toBe(version);
      tasks[0].subtasks.find(s => s.id === body.stepId).status = body.status;
      json = { value: tasks, version: ++version };
    } else if (path === '/api/planning/proposals') {
      requests.push({ path, body });
      json = { ...planning.replanResponse, blocks: [{ taskId: 'project', title: '课程汇报排练', startAt: '2026-12-17T16:30:00+08:00', endAt: '2026-12-17T17:00:00+08:00' }],
        estimates: { project: 60 }, replannedTaskIds: ['project'], warnings: ['课程汇报：截止前尚缺 30 分钟'] };
    } else if (path === '/api/planning/apply') {
      requests.push({ path, body });
      if (options.conflict) { status = 409; json = { error: '任务已变化，请重新规划' }; }
      else { tasks[0].plans = [{ id: 'new', title: '课程汇报排练', startAt: '2026-12-17T16:30:00+08:00', endAt: '2026-12-17T17:00:00+08:00' }];
        json = { value: tasks, version: ++version }; }
    }
    await route.fulfill({ json, status });
  });
  await page.goto('/');
  await expect(page.locator('[data-task-id=project]')).toBeVisible();
  return requests;
}
const dialog = p => p.locator('#dialog');
const drawer = p => p.locator('#task-drawer');

test('clarifies progress, previews the remaining steps, then saves and tracks dependencies', async ({ page }, info) => {
  const requests = await setup(page);
  await page.locator('[data-task-open=project]').click();
  await drawer(page).getByRole('button', { name: 'AI 拆分' }).click();
  await dialog(page).getByRole('button', { name: '生成步骤' }).click();
  await dialog(page).getByLabel('目前完成到哪一步？').fill('已完成登录');
  await dialog(page).getByRole('button', { name: '继续拆分' }).click();
  await expect(dialog(page)).toContainText('先完成 · 完成任务页');
  expect(requests.at(-1).body.conversation.at(-1).content).toBe('已完成登录');
  await page.screenshot({ path: info.outputPath('split-preview.png') });
  await dialog(page).getByRole('button', { name: '确认步骤' }).click();
  await expect(dialog(page)).not.toBeVisible();
  const first = drawer(page).locator('[data-study-step=intro]'), second = drawer(page).locator('[data-study-step=ppt]');
  await expect(second).toBeDisabled();
  await first.check();
  await expect(second).toBeEnabled();
  await second.check();
  await expect(first).toBeDisabled();
  await expect(drawer(page)).toContainText('先取消完成：制作演示文稿');
  await drawer(page).getByRole('button', { name: '关闭任务详情' }).click();
  await page.locator('[data-view=calendar]').click();
  await page.locator('[data-study-open]').click();
  await expect(dialog(page).locator('[name=taskId][value=project]')).toHaveCount(0);
  await expect(dialog(page).locator('[name=taskId][value=atomic]')).toBeChecked();
  expect(requests.filter(r => r.path.endsWith('/apply'))).toHaveLength(1);
});

test('scheduled task selection, availability, shortfall and confirmed blocks reach the calendar', async ({ page }, info) => {
  const requests = await setup(page);
  await page.locator('[data-view=calendar]').click();
  await page.locator('[data-study-open]').click();
  const modal = dialog(page);
  await expect(modal.locator('[name=startTime]')).toHaveValue('16:00');
  await modal.locator('[name=taskId][value=project]').check();
  await modal.locator('[name=taskId][value=atomic]').uncheck();
  await modal.locator('[name=startTime]').fill('16:15');
  await modal.getByRole('button', { name: '生成排程预览' }).click();
  await expect(modal).toContainText('截止前尚缺 30 分钟');
  await expect(modal).toContainText('替换尚未开始且未锁定的安排');
  expect(requests.at(-1).body).toEqual({ taskIds: ['project'], replan: true, availability: { startTime: '16:15', endTime: '17:15', weekdays: [5] } });
  await page.screenshot({ path: info.outputPath('schedule-preview.png') });
  await modal.getByRole('button', { name: '添加到日历' }).click();
  await expect(modal).not.toBeVisible();
  await expect(page).toHaveURL(/date=2026-12-17/);
  await expect(page.locator('.day-panel .study-session')).toHaveText('16:30–17:00课程汇报排练');
  await page.locator('.day-panel .study-session').click();
  await expect(drawer(page).locator('#drawer-heading')).toHaveText('课程汇报');
});

test('discarding a late split leaves the next task and its atomic result isolated', async ({ page }) => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const requests = await setup(page, { gate });
  try {
    await page.locator('[data-task-open=project]').click();
    await drawer(page).getByRole('button', { name: 'AI 拆分' }).click();
    await dialog(page).getByRole('button', { name: '生成步骤' }).click();
    await expect(dialog(page).getByRole('status')).toContainText('正在分析');
    await dialog(page).getByRole('button', { name: '关闭AI 任务拆分' }).click();
    await drawer(page).getByRole('button', { name: '关闭任务详情' }).click();
    await page.locator('[data-task-open=atomic]').click();
    await drawer(page).getByRole('button', { name: 'AI 拆分' }).click();
    await dialog(page).getByRole('button', { name: '生成步骤' }).click();
    release();
    await expect(dialog(page)).toContainText('这项任务可以直接完成');
    await expect(dialog(page).getByRole('button', { name: '确认步骤' })).toHaveCount(0);
    expect(requests.filter(r => r.path.endsWith('/apply'))).toHaveLength(0);
  } finally { release(); }
});

test('confirms a one-step replacement for previously decomposed work', async ({ page }) => {
  const requests = await setup(page, { replaceWithSingleStep: true });
  await page.locator('[data-task-open=atomic]').click();
  await drawer(page).getByRole('button', { name: '重新拆分' }).click();
  await dialog(page).getByLabel('补充进度或要求（可选）').fill('报告已定稿，只剩上传。');
  await dialog(page).getByRole('button', { name: '生成步骤' }).click();
  await expect(dialog(page)).toContainText('这一步将替换原有的未完成步骤');
  await expect(dialog(page)).toContainText('提交报告');
  await dialog(page).getByRole('button', { name: '确认步骤' }).click();
  await expect(dialog(page)).not.toBeVisible();
  const steps = drawer(page).locator('.study-steps');
  await expect(steps.getByLabel('整理初稿')).toBeChecked();
  await expect(steps.getByLabel(/提交报告/)).not.toBeChecked();
  await expect(steps).not.toContainText('再次修改报告');
  expect(requests.filter(r => r.path.endsWith('/apply')).map(r => r.body))
    .toEqual([{ taskId: 'atomic', requestId: 'replace-remaining' }]);
});

test('stale schedule preview asks for regeneration and leaves earlier blocks intact', async ({ page }) => {
  const requests = await setup(page, { conflict: true });
  await page.locator('[data-view=calendar]').click();
  await page.locator('[data-study-open]').click();
  await dialog(page).getByRole('button', { name: '生成排程预览' }).click();
  await dialog(page).getByRole('button', { name: '添加到日历' }).click();
  await expect(dialog(page).getByRole('alert')).toContainText('任务已变化');
  await expect(dialog(page).getByRole('button', { name: '生成排程预览' })).toBeVisible();
  expect(requests.filter(r => r.path.endsWith('/apply'))).toHaveLength(1);
});
