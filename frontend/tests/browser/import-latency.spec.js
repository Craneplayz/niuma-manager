import { test, expect } from '@playwright/test';

const deferred = () => {
  let release, entered;
  const waiting = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { entered = resolve; });
  return { release, started, async wait() { entered(); await waiting; } };
};

async function workspace(page, { holdCredentials = false, rejectCredentials = false, holdSubmission = false, rejectSubmission = false, holdHistory = false, rejectBatches = false } = {}) {
  const credentials = deferred(), submission = deferred(), history = deferred();
  const calls = [], posts = [];
  let jobs = [], credentialsFailed = false, workspaceReads = 0;
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const method = request.method();
    calls.push({ path, method });
    let value = {}, status = 200;
    if (path === '/api/auth/me') value = { user: { id: 'import-latency', name: '合成测试账号', emailVerified: true } };
    else if (path === '/api/workspace') { workspaceReads++; value = { tasks: { version: 1, value: [] } }; }
    else if (path === '/api/recognition/batches') {
      if (rejectBatches) { status = 503; value = { error: '合成候选读取失败' }; }
      else value = [];
    } else if (path === '/api/sources/credentials') {
      if (holdCredentials) await credentials.wait();
      if (rejectCredentials && !credentialsFailed) { credentialsFailed = true; status = 503; value = { error: '合成连接读取失败' }; }
      else value = { configured: true, username: 'synthetic-campus' };
    } else if (path === '/api/recognition/text-jobs') {
      if (method === 'POST') {
        const body = request.postDataJSON(); posts.push(body);
        if (holdSubmission) await submission.wait();
        if (rejectSubmission) { status = 503; value = { error: '合成提交失败' }; }
        else jobs = [{ request_id: body.request_id, state: 'recognizing', attempt: 1, created_at: '2026-10-23T01:00:00Z', updated_at: '2026-10-23T01:00:00Z', error: null }];
      } else if (holdHistory) {
        await history.wait();
        jobs = [{ request_id: 'synthetic-old-job', state: 'success', attempt: 1, created_at: '2026-10-23T01:00:00Z', updated_at: '2026-10-23T01:00:00Z', error: null }];
      }
      if (status === 200) value = { contract_version: 'text-import-jobs/0.1', jobs };
    } else if (path === '/api/sources/sync-runs') value = { contract_version: 'source-sync/0.1', runs: [] };
    else if (path === '/api/sources/sync-schedule') value = { contract_version: 'source-sync/0.1', enabled: false, available: false, interval_minutes: 180, revision: 0 };
    else if (path === '/api/materials/limits') value = { limits: { asset_bytes: 20971520, account_bytes: 209715200 } };
    else if (path === '/api/materials/imports') value = { jobs: [] };
    else if (path === '/api/version') value = { environment: 'test', revision: 'synthetic' };
    else if (path === '/api/usage/visit') value = { offerRating: false };
    else if (path !== '/api/usage/guest-visit') throw new Error('Unexpected API: ' + path);
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
  });
  await page.goto('/?page=import&importSource=text');
  await expect(page.locator('#import-text')).toBeVisible();
  return { credentials, submission, history, calls, posts, get workspaceReads() { return workspaceReads; } };
}

const source = (page, name) => page.locator(`[data-import-source="${name}"]`);

test('text and file drafting stay usable while teaching credentials are slow', async ({ page }) => {
  const f = await workspace(page, { holdCredentials: true });
  try {
    await expect(page.locator('.import-results')).toContainText('暂无待核对任务');
    expect(f.calls.some(call => call.path === '/api/sources/credentials')).toBe(false);
    await source(page, 'teaching').click();
    await f.credentials.started;
    await source(page, 'text').click();
    await expect(page.locator('#import-text')).toBeEnabled();
    await page.locator('#import-text').fill('不等待教学网读取的下一段草稿');
    await source(page, 'files').click();
    await expect(page.locator('#material-files')).toBeEnabled();
    await source(page, 'text').click();
    await expect(page.locator('#import-text')).toHaveValue('不等待教学网读取的下一段草稿');
    f.credentials.release();
    await expect(page.locator('#import-text')).toBeEnabled();
  } finally { f.credentials.release(); }
});

test('credential read failure is scoped to teaching and has its own retry', async ({ page }) => {
  await workspace(page, { rejectCredentials: true });
  await source(page, 'teaching').click();
  await expect(page.locator('[data-import-action="refresh-credentials"]')).toBeVisible();
  await source(page, 'text').click();
  await expect(page.locator('[data-import-action="refresh-credentials"]')).toHaveCount(0);
  await expect(page.locator('#import-text')).toBeEnabled();
  await page.locator('#import-text').fill('读取错误不会清空此草稿');
  await source(page, 'teaching').click();
  await expect(page.locator('.teaching-connection')).toContainText('synthetic-campus');
  await source(page, 'text').click();
  await expect(page.locator('#import-text')).toHaveValue('读取错误不会清空此草稿');
});

test('a pending receipt preserves the next draft and focus while the submitted payload stays fixed', async ({ page }) => {
  const f = await workspace(page, { holdSubmission: true });
  try {
    const input = page.locator('#import-text');
    await input.fill('第一段通知，下周一交报告');
    await page.locator('[data-import-form="text"] button[type="submit"]').click();
    await f.submission.started;
    await expect(input).toBeEnabled();
    await input.fill('第二段通知，还没有提交');
    await input.focus();
    const original = await input.elementHandle();
    f.submission.release();
    await expect(page.locator('.import-progress')).toContainText('正在识别');
    await expect(input).toHaveValue('第二段通知，还没有提交');
    await expect(input).toBeFocused();
    expect(await original.evaluate(node => node.isConnected)).toBe(true);
    await expect(page.locator('[data-import-form="text"] button[type="submit"]')).toBeDisabled();
    expect(f.posts).toHaveLength(1);
    expect(f.posts[0].records[0].text).toBe('第一段通知，下周一交报告');
  } finally { f.submission.release(); }
});

test('a rejected old submission preserves the next draft and allows resubmission', async ({ page }) => {
  const f = await workspace(page, { holdSubmission: true, rejectSubmission: true });
  try {
    await page.locator('#import-text').fill('第一段合成通知');
    await page.locator('[data-import-form="text"] button[type="submit"]').click();
    await f.submission.started;
    await expect(page.locator('#import-text')).toBeEnabled();
    await page.locator('#import-text').fill('第二段草稿');
    f.submission.release();
    await expect(page.locator('.import-input .user-error').first()).toBeVisible();
    await expect(page.locator('#import-text')).toHaveValue('第二段草稿');
    await expect(page.locator('[data-import-form="text"] button[type="submit"]')).toBeEnabled();
  } finally { f.submission.release(); }
});

test('candidate read failure does not prevent text editing or file selection', async ({ page }) => {
  await workspace(page, { rejectBatches: true });
  await expect(page.locator('.import-results')).toContainText('当前列表和数量可能不完整');
  await expect(page.locator('#import-text')).toBeEnabled();
  await page.locator('#import-text').fill('候选未读取时的草稿');
  await source(page, 'files').click();
  await expect(page.locator('#material-files')).toBeEnabled();
  await source(page, 'text').click();
  await expect(page.locator('#import-text')).toHaveValue('候选未读取时的草稿');
});

test('background history refresh preserves the current text input node and focus', async ({ page }) => {
  const f = await workspace(page, { holdHistory: true });
  try {
    await f.history.started;
    const input = page.locator('#import-text');
    await expect(input).toBeEnabled();
    await input.fill('正在输入的新通知');
    await input.focus();
    const original = await input.elementHandle(), before = f.workspaceReads;
    f.history.release();
    await expect.poll(() => f.workspaceReads).toBeGreaterThan(before);
    await expect(page.locator('[data-import-history-batch-entry="synthetic-old-job"]')).toHaveCount(1);
    await expect(input).toHaveValue('正在输入的新通知');
    await expect(input).toBeFocused();
    expect(await original.evaluate(node => node.isConnected)).toBe(true);
  } finally { f.history.release(); }
});
