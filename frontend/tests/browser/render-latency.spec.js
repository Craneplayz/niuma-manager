import { test, expect } from '@playwright/test';

async function fixture(page, { calendar = false, holdReviews = false, holdJobs = false } = {}) {
  let releaseReviews, releaseJobs, enterReviews, enterJobs;
  const reviewGate = new Promise(r => { releaseReviews = r; }), jobGate = new Promise(r => { releaseJobs = r; });
  const reviewsStarted = new Promise(r => { enterReviews = r; }), jobsStarted = new Promise(r => { enterJobs = r; });
  let evidenceReads = 0, workspaceReads = 0, version = 1;
  let tasks = [{ id: 'a', title: '合成任务A', status: 'not_started', source: '文本导入', due: null, plans: [], subtasks: [] }];
  await page.route('**/api/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    let value;
    if (path === '/api/auth/me') value = { user: { id: 'render-synthetic', name: '合成检查', emailVerified: true } };
    else if (path === '/api/workspace') { workspaceReads++; value = { tasks: { version, value: tasks } }; }
    else if (path === '/api/recognition/batches') { enterReviews(); if (holdReviews) await reviewGate; value = []; }
    else if (path === '/api/sources/credentials') value = { configured: false };
    else if (path === '/api/recognition/text-jobs') {
      enterJobs(); if (holdJobs) await jobGate;
      value = { contract_version: 'text-import-jobs/0.1', jobs: [{ request_id: 'past-job', state: 'success', attempt: 1, created_at: '2026-10-23T01:00:00Z', updated_at: '2026-10-23T01:01:00Z', error: null }] };
    } else if (path === '/api/data/tasks') {
      const body = req.postDataJSON(); tasks = body.value; value = { version: ++version, operationId: body.operation?.id };
    } else if (path === '/api/recognition/task-evidence') {
      evidenceReads++; value = { evidence: [{ original: '这是一段合成通知原文。', sourceLabel: '合成来源', quotes: [], sources: [] }] };
    } else if (path === '/api/sources/sync-runs') value = { contract_version: 'source-sync/0.1', runs: [], last_check: null };
    else if (path === '/api/version') value = { environment: 'test', revision: 'synthetic' };
    else if (path === '/api/usage/guest-visit') value = {};
    else if (path === '/api/usage/visit') value = { offerRating: false };
    else throw new Error('Unexpected API: ' + path);
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
  });
  await page.goto(calendar ? '/?page=tasks&scope=all&view=calendar' : holdJobs ? '/?page=import' : '/');
  return { releaseReviews, releaseJobs, reviewsStarted, jobsStarted, get evidenceReads() { return evidenceReads; }, get workspaceReads() { return workspaceReads; } };
}

test('calendar is usable before unrelated review data arrives', async ({ page }) => {
  const f = await fixture(page, { calendar: true, holdReviews: true });
  try {
    await f.reviewsStarted;
    await expect(page.locator('.calendar-grid-wrap')).toBeVisible();
    await expect(page.locator('[data-scope="all"] .nav-count')).toHaveText('…');
    f.releaseReviews();
    await expect(page.locator('[data-scope="all"] .nav-count')).toHaveText('1');
  } finally { f.releaseReviews(); }
});

test('background history refresh keeps composing text, focus and publication details', async ({ page }) => {
  const f = await fixture(page, { holdJobs: true });
  try {
    await f.jobsStarted;
    const input = page.locator('#import-text');
    await expect(input).toBeEnabled();
    await page.locator('details.import-time summary').click();
    await input.fill('下一段通知草稿');
    await input.focus();
    const original = await input.elementHandle();
    await input.evaluate(node => node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true })));
    f.releaseJobs();
    await expect.poll(() => f.workspaceReads).toBeGreaterThan(1);
    await expect(input).toHaveValue('下一段通知草稿');
    expect(await original.evaluate(node => node === document.querySelector('#import-text'))).toBe(true);
    await expect(input).toBeFocused();
    await expect(page.locator('details.import-time')).toHaveAttribute('open', '');
    await input.evaluate(node => node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true })));
  } finally { f.releaseJobs(); }
});

test('a status change reuses the open evidence pane and its disclosure state', async ({ page }) => {
  const f = await fixture(page);
  await page.locator('[data-task-open="a"]').click();
  const drawer = page.locator('#task-drawer'), details = drawer.locator('.task-evidence-entry details');
  await expect(details).toBeVisible();
  await details.locator('summary').click();
  const original = await details.elementHandle();
  expect(f.evidenceReads).toBe(1);
  await drawer.locator('[data-task-complete="a"]').click();
  await expect(drawer.getByRole('button', { name: '重新打开', exact: true })).toBeEnabled();
  expect(f.evidenceReads).toBe(1);
  expect(await original.evaluate(node => node.isConnected)).toBe(true);
  await expect(details).toHaveAttribute('open', '');
  await drawer.getByRole('button', { name: '关闭任务详情' }).click();
  const menu = page.getByRole('button', { name: '展开侧栏', exact: true });
  if (await menu.isVisible()) await menu.click();
  await page.locator('[data-scope="done"]').click();
  await page.locator('[data-task-open="a"]').click();
  await expect.poll(() => f.evidenceReads).toBe(2);
});
