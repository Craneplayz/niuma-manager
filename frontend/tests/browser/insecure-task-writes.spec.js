import { test as base, expect } from '@playwright/test';

const origin = 'http://plain.niuma.test';
// Keep Chromium's configured executable and its normal secure-context rules.
const test = base.extend({
  browser: [async ({ playwright, launchOptions, headless, channel }, use) => {
    const browser = await playwright.chromium.launch({
      headless, channel, ...launchOptions,
      args: [...(launchOptions.args || []), '--host-resolver-rules=MAP plain.niuma.test 127.0.0.1'],
    });
    try { await use(browser); }
    finally { await browser.close(); }
  }, { scope: 'worker' }],
});

const clone = value => structuredClone(value);
const marker = 'HTTP-PRIVATE-CONTENT-8327';
const row = (page, id) => page.locator(`[data-task-id="${id}"]`);
const review = (page, id) => page.locator(`[data-review-id="${id}"]`);
function deferred() {
  let resolve;
  return { promise: new Promise(done => { resolve = done; }), resolve: () => resolve() };
}
async function fixture(page, baseURL) {
  let version = 1, dropReply = false, held = null, applied = 0;
  let tasks = [{ id: 'existing', title: marker + '-existing', description: marker + '-notes',
    course: null, content_type: 'task', status: 'not_started', completedAt: '', due: null,
    source: '手动添加', plans: [], subtasks: [] }];
  const make = id => ({ id, state: 'pending', version: 1, candidate: {
    candidate_id: id, input_id: 'source', title: marker + '-' + id, description: marker + '-candidate-notes',
    course_or_project: null, content_type: 'task', notice_type: 'create', needs_review: true,
    review_reasons: ['合成核对'], deadline: { kind: 'deadline', status: 'explicit', precision: 'date', value: '2099-10-25' },
    sources: [{ input_id: 'source', platform: 'manual', label: '合成文本', url: null }],
    evidence: [{ input_id: 'source', quote: marker + '-source' }],
  } });
  const candidates = [make('candidate-a'), make('candidate-b')], receipts = new Map();
  const mutations = [], reconciliations = [];
  const states = ids => candidates.filter(item => ids.includes(item.id)).map(item => ({
    id: item.id, state: item.state, version: item.version, ...(item.taskId ? { taskId: item.taskId } : {}),
  }));
  const batches = () => [{ request: { request_id: 'insecure-synthetic', contract_version: '0.2', records: [
    { input_id: 'source', platform: 'manual', text: marker + '-source', source_label: '合成文本' },
  ] }, result: { status: 'success', errors: [] }, items: clone(candidates) }];

  await page.addInitScript(() => {
    window.__insecurePersistence = { journalOpWrites: 0, storageValues: [] };
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      window.__insecurePersistence.storageValues.push(String(value));
      return setItem.call(this, key, value);
    };
    for (const name of ['put', 'add']) {
      const write = IDBObjectStore.prototype[name];
      IDBObjectStore.prototype[name] = function (...args) {
        if (this.transaction.db.name === 'niuma-task-journal-v1') window.__insecurePersistence.journalOpWrites++;
        return write.apply(this, args);
      };
    }
  });
  await page.route(origin + '/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (!url.pathname.startsWith('/api/')) {
      // The browser loads the real Vite app at the unsafe origin, not an iframe or a mocked app.
      const target = new URL(url.pathname + url.search, baseURL).href;
      return route.fulfill({ response: await route.fetch({ url: target,
        headers: { ...request.headers(), host: new URL(target).host } }) });
    }
    const path = url.pathname, body = request.postDataJSON();
    let value;
    if (path === '/api/auth/me') value = { user: { id: 'insecure-synthetic', name: '合成 HTTP 账号', emailVerified: true } };
    else if (path === '/api/workspace') value = { taskWriteOwner: 'insecure-synthetic', taskWriteGeneration: 0, tasks: { value: tasks, version } };
    else if (path === '/api/recognition/batches') value = batches();
    else if (path === '/api/tasks/reconcile') {
      reconciliations.push(clone(body));
      value = { applied: body.operations.filter(operation => receipts.has(operation.id)).map(operation => operation.id),
        tasks: { value: tasks, version }, generation: 0,
        candidates: states(body.operations.filter(operation => operation.intent.kind === 'review').map(operation => operation.intent.candidateId)) };
    } else if (path === '/api/data/tasks' || path.endsWith('/resolve')) {
      const operation = body.operation, intent = operation.intent, identity = JSON.stringify(operation);
      mutations.push({ path, body: clone(body) });
      const replayed = receipts.has(operation.id);
      if (replayed) expect(receipts.get(operation.id)).toBe(identity);
      else {
        expect(operation.owner).toBe('insecure-synthetic');
        if (path === '/api/data/tasks') {
          expect(body.version).toBe(version);
          tasks = clone(body.value); version++;
        } else {
          const item = candidates.find(candidate => candidate.id === intent.candidateId);
          expect(body.version).toBe(item.version);
          expect(item.state).toBe(body.action === 'reopen' ? 'ignored' : 'pending');
          if (body.action === 'create') {
            expect(body.tasks_version).toBe(version);
            expect(tasks.some(task => task.id === intent.newTaskId)).toBe(false);
            tasks.push({ id: intent.newTaskId, ...clone(body.draft), source: '文本导入',
              status: body.complete ? 'completed' : 'not_started', completedAt: body.complete ? intent.completedAt : '',
              createdAt: intent.createdAt, plans: [], subtasks: [],
              recognitionHistory: [{ storageId: item.id, action: 'create' }] });
            item.taskId = intent.newTaskId; version++;
          } else expect(['ignore', 'reopen']).toContain(body.action);
          item.state = body.action === 'ignore' ? 'ignored' : body.action === 'reopen' ? 'pending' : 'applied';
          item.version++;
        }
        receipts.set(operation.id, identity); applied++;
      }
      value = path === '/api/data/tasks'
        ? { operationId: operation.id, version, replayed, ...(replayed ? { value: tasks } : {}) }
        : { operationId: operation.id, replayed, tasks: { value: tasks, version }, generation: 0, candidates: states([intent.candidateId]) };
      const waiting = held; held = null;
      if (waiting) { waiting.entered.resolve(); await waiting.gate.promise; }
      if (dropReply) { dropReply = false; return route.abort('failed'); }
    } else if (path === '/api/recognition/task-evidence') value = { evidence: [] };
    else if (path === '/api/recognition/text-jobs') value = { jobs: [] };
    else if (path === '/api/sources/credentials') value = { configured: false };
    else if (path === '/api/sources/sync-runs') value = { contract_version: 'source-sync/0.1', runs: [] };
    else if (path === '/api/version') value = { environment: 'production', revision: 'synthetic-http' };
    else if (path === '/api/usage/visit') value = { offerRating: false };
    else if (path === '/api/usage/guest-visit') value = {};
    else throw new Error('Unexpected API: ' + path);
    return route.fulfill({ json: clone(value) });
  });
  await page.goto(origin + '/?page=tasks&scope=all&view=list');
  await expect(row(page, 'existing')).toBeVisible();
  expect(await page.evaluate(() => ({ origin: location.origin, secure: isSecureContext, subtle: Boolean(crypto.subtle) })))
    .toEqual({ origin, secure: false, subtle: false });
  return {
    mutations, reconciliations, candidates, receipts,
    get tasks() { return tasks; }, get version() { return version; }, get applied() { return applied; },
    dropNextReply() { dropReply = true; },
    holdNextReply() {
      const gate = deferred(), entered = deferred(); held = { gate, entered };
      return { entered: entered.promise, release: gate.resolve };
    },
  };
}
async function noLocalOperationContent(page) {
  const saved = await page.evaluate(async () => ({
    databases: (await indexedDB.databases()).map(database => database.name),
    journalOpWrites: window.__insecurePersistence.journalOpWrites,
    storage: JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, writes: window.__insecurePersistence.storageValues }),
  }));
  expect(saved.databases).not.toContain('niuma-task-journal-v1');
  expect(saved.journalOpWrites).toBe(0);
  expect(saved.storage).not.toContain(marker);
}
async function edit(page, id, title) {
  await row(page, id).locator('.task-open').click();
  const drawer = page.locator('#task-drawer');
  await drawer.getByRole('button', { name: '编辑', exact: true }).click();
  await drawer.getByLabel('任务名称', { exact: true }).fill(title);
  await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
  await expect(drawer).not.toBeVisible();
}

test('ordinary HTTP supports task CRUD and restoration without local plaintext or early UI success', async ({ page, baseURL }, info) => {
  const f = await fixture(page, baseURL), slowCreate = f.holdNextReply();
  try {
    await page.locator('[data-action="task-new"]').click();
    const drawer = page.locator('#task-drawer');
    await drawer.getByLabel('任务名称', { exact: true }).fill(marker + '-new');
    await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
    await slowCreate.entered;
    const id = f.mutations[0].body.operation.intent.draft.id;
    await expect(drawer).toBeVisible();
    await expect(row(page, id)).toHaveCount(0);
    await noLocalOperationContent(page);
    await page.screenshot({ path: info.outputPath('http-online-saving.png'), fullPage: true });
    slowCreate.release();
    await expect(drawer).not.toBeVisible();
    await expect(row(page, id)).toContainText(marker + '-new');
    await edit(page, id, marker + '-edited');
    await expect(row(page, id)).toContainText(marker + '-edited');
    await row(page, id).locator('[data-task-complete]').click();
    await expect(row(page, id)).toHaveCount(0);
    await page.getByRole('button', { name: '撤销', exact: true }).click();
    await expect(row(page, id)).toBeVisible();
    const slowDelete = f.holdNextReply();
    try {
      await row(page, id).locator('.task-open').click();
      await drawer.getByRole('button', { name: '移入回收站', exact: true }).click();
      await slowDelete.entered;
      await expect(drawer).toBeVisible();
      await expect(row(page, id)).toBeVisible();
      await noLocalOperationContent(page);
      slowDelete.release();
      await expect(drawer).not.toBeVisible();
      await expect(row(page, id)).toHaveCount(0);
    } finally { slowDelete.release(); }
    // Trash belongs to the main page footer; the mobile navigation would cover it.
    await expect(page.locator('#main')).not.toHaveAttribute('inert', '');
    await page.locator('[data-action="open-trash"]').click();
    await page.locator(`[data-restore-task="${id}"]`).click();
    await expect(row(page, id)).toBeVisible();
    expect(f.tasks.find(task => task.id === id).recycleBin).toBeUndefined();
    expect(f.tasks.find(task => task.id === id).status).toBe('not_started');
    expect(f.tasks.find(task => task.id === id).title).toBe(marker + '-edited');
    expect(f.version).toBe(7);
    expect(f.applied).toBe(6);
    await noLocalOperationContent(page);
  } finally { slowCreate.release(); }
});

test('ordinary HTTP confirms, ignores and reopens candidates only after the server reply', async ({ page, baseURL }) => {
  const f = await fixture(page, baseURL), confirming = f.holdNextReply();
  try {
    await review(page, 'candidate-a').locator('.task-open').click();
    const drawer = page.locator('#task-drawer');
    await drawer.getByRole('button', { name: '编辑', exact: true }).click();
    await drawer.getByLabel('任务名称', { exact: true }).fill(marker + '-confirmed');
    await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
    await confirming.entered;
    const id = f.mutations[0].body.operation.intent.newTaskId;
    await expect(drawer).toBeVisible();
    await expect(review(page, 'candidate-a')).toBeVisible();
    await expect(row(page, id)).toHaveCount(0);
    await noLocalOperationContent(page);
    confirming.release();
    await expect(drawer).not.toBeVisible();
    await expect(review(page, 'candidate-a')).toHaveCount(0);
    await expect(row(page, id)).toContainText(marker + '-confirmed');
    const ignoring = f.holdNextReply();
    try {
      await review(page, 'candidate-b').locator('[data-import-ignore]').click();
      await ignoring.entered;
      await expect(review(page, 'candidate-b')).toBeVisible();
      ignoring.release();
      await expect(review(page, 'candidate-b')).toHaveCount(0);
    } finally { ignoring.release(); }
    const reopening = f.holdNextReply();
    try {
      await page.getByRole('button', { name: '撤销', exact: true }).click();
      await reopening.entered;
      await expect(review(page, 'candidate-b')).toHaveCount(0);
      reopening.release();
      await expect(review(page, 'candidate-b')).toBeVisible();
    } finally { reopening.release(); }
    expect(f.candidates.map(item => [item.state, item.version])).toEqual([['applied', 2], ['pending', 3]]);
    expect(f.version).toBe(2);
    expect(f.applied).toBe(3);
    await noLocalOperationContent(page);
  } finally { confirming.release(); }
});

test('ordinary HTTP completion keeps open review details mapped to the acknowledged task', async ({ page, baseURL }) => {
  const f = await fixture(page, baseURL), held = f.holdNextReply();
  try {
    await review(page, 'candidate-a').locator('.task-open').click();
    const drawer = page.locator('#task-drawer');
    await drawer.locator('[data-task-complete]').click();
    await held.entered;
    await expect(drawer.getByText('待核对', { exact: true })).toBeVisible();
    const id = f.mutations[0].body.operation.intent.newTaskId;
    held.release();
    await expect(drawer).toBeVisible();
    await expect(drawer.locator(`[data-task-complete="${id}"]`)).toBeEnabled();
    await expect(drawer.getByText('待核对', { exact: true })).toHaveCount(0);
    expect(f.tasks.find(task => task.id === id).status).toBe('completed');
    expect(f.applied).toBe(1);
    await noLocalOperationContent(page);
  } finally { held.release(); }
});

for (const kind of ['ordinary', 'review']) {
  test(`ordinary HTTP retries a lost ${kind} reply with the original operation identity and no duplicate mutation`, async ({ page, baseURL }) => {
    const f = await fixture(page, baseURL); f.dropNextReply();
    const selected = kind === 'ordinary' ? row(page, 'existing') : review(page, 'candidate-a');
    await selected.locator('[data-task-complete]').click();
    await expect(page.getByRole('button', { name: '重试保存', exact: true })).toBeVisible();
    await expect(selected).toBeVisible();
    expect(f.applied).toBe(1);
    const identity = f.mutations[0].body.operation.id;
    await noLocalOperationContent(page);
    await page.getByRole('button', { name: '重试保存', exact: true }).click();
    await expect(selected).toHaveCount(0);
    await expect(page.getByRole('button', { name: '重试保存', exact: true })).toHaveCount(0);
    expect(f.reconciliations.flatMap(body => body.operations.map(operation => operation.id))).toContain(identity);
    expect(f.mutations.map(write => write.body.operation.id)).toEqual([identity]);
    expect(f.receipts.size).toBe(1);
    expect(f.applied).toBe(1);
    expect(f.version).toBe(2);
    if (kind === 'ordinary') expect(f.tasks[0].status).toBe('completed');
    else {
      const id = f.mutations[0].body.operation.intent.newTaskId;
      expect(f.tasks.filter(task => task.id === id)).toHaveLength(1);
      expect(f.tasks.find(task => task.id === id).recognitionHistory).toHaveLength(1);
    }
    await noLocalOperationContent(page);
  });
}
