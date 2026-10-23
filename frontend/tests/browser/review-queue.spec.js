import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';

// Real handlers, SQLite, cookies and encrypted IndexedDB; faults affect transport only.
const harness = String.raw`
from copy import deepcopy
import hashlib,json,sys,time
from pathlib import Path
import app
app.DB=Path(sys.argv[1])/'reviews.sqlite3'
app.initialize(recover=False)
fixture=json.loads((Path(app.__file__).resolve().parents[1]/'contracts/text-to-app/text-contract-v0.2.json').read_text())
with app.connection() as db:
    db.execute('INSERT INTO users VALUES(?,?,?,?,?)',('alice','alice@review-queue.test','Synthetic','00'*16,'unused'))
class Handler(app.Handler):
    def route(self):
        if self.path=='/__test/reset':
            with app.connection() as db:
                for table in ('workspace','task_write_receipts','task_write_epochs','recognition_candidates','recognition_batches','sessions'):
                    db.execute('DELETE FROM '+table)
                db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(b'alice').hexdigest(),'alice',time.time()+3600))
                task=dict(id='existing',title='普通任务',description='',course=None,content_type='task',status='not_started',completedAt='',due=None,source='手动添加',plans=[],subtasks=[])
                db.execute('INSERT INTO workspace VALUES(?,?,?,1)',('alice','tasks',json.dumps([task])))
                request=deepcopy(fixture['request']);request['request_id']='review-queue-fixture';request['records']=request['records'][:1]
                candidates=[]
                for letter in ('a','b'):
                    candidate=deepcopy(fixture['expected']['tasks'][0])
                    candidate.update(candidate_id=letter,title='待核对任务'+letter.upper(),notice_type='create',content_type='task',needs_review=True,review_reasons=['合成核对'])
                    candidate['deadline']['value']='2099-10-25'
                    candidates.append(candidate)
                    db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',('candidate-'+letter,'alice',request['request_id'],json.dumps(candidate)))
                result=dict(contract_version='0.2',request_id=request['request_id'],status='success',tasks=candidates,errors=[],record_results=[dict(input_id=request['records'][0]['input_id'],status='success',task_ids=['a','b'],warnings=[])])
                db.execute('INSERT INTO recognition_batches(user_id,request_id,fingerprint,request,result,demo) VALUES(?,?,?,?,?,0)',('alice',request['request_id'],'synthetic',json.dumps(request),json.dumps(result)))
            return self.reply(200,{})
        if self.path=='/__test/state':
            with app.connection() as db:
                row=db.execute('SELECT value,version FROM workspace WHERE user_id="alice" AND name="tasks"').fetchone()
                candidates=[dict(row) for row in db.execute('SELECT id,state,version FROM recognition_candidates ORDER BY id')]
                receipts=db.execute('SELECT count(*) FROM task_write_receipts WHERE user_id="alice"').fetchone()[0]
            return self.reply(200,dict(tasks=dict(value=json.loads(row['value']),version=row['version']),candidates=candidates,receipts=receipts))
        return super().route()
with app.ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
    print('REVIEW_QUEUE_READY '+json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port),cookie=app.runtime_environment.cookie_name())),flush=True)
    server.serve_forever()
`;

test.describe.configure({ mode: 'serial' });
test.use({ timezoneId: 'Asia/Shanghai' });
let folder, backend, api, cookie;
test.beforeAll(async () => {
  test.setTimeout(60000);
  folder = await mkdtemp(join(tmpdir(), 'niuma-review-queue-'));
  const script = join(folder, 'harness.py'), root = resolve('..');
  await writeFile(script, harness);
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(NIUMA_|DDL_|VITE_|WECHAT_|TEXT_HARNESS)/.test(key)));
  env.PYTHONPATH = root + ':' + join(root, 'server');
  env.NIUMA_ALLOWED_ORIGINS = 'http://127.0.0.1:' + (process.env.NIUMA_BROWSER_TEST_PORT || 18763);
  env.NIUMA_BACKGROUND_RECOVERY = '0';
  env.NIUMA_ENABLE_SCHEDULED_SYNC = '0';
  backend = spawn(process.env.NIUMA_TEST_PYTHON || 'python3', ['-u', script, folder], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  await new Promise((done, reject) => {
    let output = '', errors = '';
    const timer = setTimeout(() => reject(new Error('API startup: ' + errors + '; output: ' + output)), 40000);
    backend.on('error', error => { clearTimeout(timer); reject(error); });
    backend.stderr.on('data', bytes => { errors += bytes; });
    backend.on('exit', code => { clearTimeout(timer); reject(new Error('API exit ' + code + ': ' + errors)); });
    backend.stdout.on('data', bytes => {
      output += bytes;
      const line = output.split('\n').find(line => line.startsWith('REVIEW_QUEUE_READY '));
      if (line) { ({ url: api, cookie } = JSON.parse(line.slice('REVIEW_QUEUE_READY '.length))); clearTimeout(timer); done(); }
    });
  });
});
test.afterAll(async () => {
  if (backend && backend.exitCode === null) { backend.kill(); await once(backend, 'exit'); }
  if (folder) await rm(folder, { recursive: true, force: true });
});
test.beforeEach(async ({ request }) => { expect((await request.post(api + '/__test/reset')).ok()).toBeTruthy(); });

const review = (page, id) => page.locator(`[data-review-id="${id}"]`);
const task = (page, id) => page.locator(`[data-task-id="${id}"]`);
const resolveURL = '**/api/recognition/candidates/*/resolve';
const snapshot = async request => (await request.get(api + '/__test/state')).json();
const pending = async page => page.evaluate(async () => (await import('/src/task-journal.js')).readOperations('alice'));
async function open(page, context) {
  await context.addCookies([{ name: cookie, value: 'alice', domain: '127.0.0.1', path: '/' }]);
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    return route.continue({ url: api + url.pathname + url.search });
  });
  await page.goto('/?page=tasks&scope=all&view=list');
  await expect(task(page, 'existing')).toBeVisible();
}
async function edit(page, row, title) {
  await row.locator('.task-open').click();
  const drawer = page.locator('#task-drawer');
  await drawer.getByRole('button', { name: '编辑', exact: true }).click();
  await drawer.getByLabel('任务名称', { exact: true }).fill(title);
  await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
  await expect(drawer).not.toBeVisible();
}
function deferred() {
  let release;
  return { promise: new Promise(resolve => { release = resolve; }), release: () => release() };
}
async function holdFirstResolve(page) {
  const gate = deferred(), entered = deferred(), requests = [];
  await page.route(resolveURL, async route => {
    requests.push(route.request().postDataJSON());
    if (requests.length === 1) { entered.release(); await gate.promise; }
    if (page.isClosed()) { await route.abort().catch(() => {}); return; }
    await route.continue({ url: api + new URL(route.request().url()).pathname }).catch(() => {});
  });
  return { release: gate.release, entered: entered.promise, requests };
}
async function holdLocalEncryption(page) {
  await page.evaluate(() => {
    const subtle = crypto.subtle, original = subtle.encrypt;
    const own = Object.getOwnPropertyDescriptor(subtle, 'encrypt');
    let resume, released = false;
    const gate = new Promise(resolve => { resume = resolve; });
    const state = window.__reviewQueueStageGate = {
      entered: false,
      release() {
        if (released) return;
        released = true;
        if (own) Object.defineProperty(subtle, 'encrypt', own);
        else delete subtle.encrypt;
        resume();
      },
    };
    Object.defineProperty(subtle, 'encrypt', { configurable: true, value: async function (...args) {
      state.entered = true;
      await gate;
      return original.apply(this, args);
    } });
  });
  return {
    entered: () => expect.poll(() => page.evaluate(() => window.__reviewQueueStageGate.entered)).toBe(true),
    release: () => page.evaluate(() => window.__reviewQueueStageGate?.release()),
  };
}

test('ignoring an open review closes its details after staging while ordinary actions remain usable', async ({ page, context, request }) => {
  await open(page, context);
  const held = await holdFirstResolve(page);
  try {
    await review(page, 'candidate-a').locator('.task-open').click();
    const drawer = page.locator('#task-drawer');
    await drawer.getByRole('button', { name: '忽略', exact: true }).click();
    await held.entered;
    await expect(drawer).not.toBeVisible();
    await expect(review(page, 'candidate-a')).toHaveCount(0);
    await task(page, 'existing').locator('[data-task-complete]').click();
    await expect(task(page, 'existing')).toHaveCount(0);
    await expect.poll(async () => (await pending(page)).length).toBe(2);
    expect((await snapshot(request)).tasks.version).toBe(1);
    held.release();
    await expect.poll(async () => (await pending(page)).length).toBe(0);
    const saved = await snapshot(request);
    expect(saved.tasks.value[0].status).toBe('completed');
    expect(saved.candidates[0]).toEqual({ id: 'candidate-a', state: 'ignored', version: 2 });
    expect(saved.receipts).toBe(2);
  } finally { held.release(); }
});

for (const action of ['save', 'delete']) {
  test(`a late ordinary ${action} staging result preserves another task's open editor and unsent draft`, async ({ page, context, request }) => {
    await open(page, context);
    // Give the second editor a real saved task without changing the shared harness fixture.
    await edit(page, review(page, 'candidate-b'), '切换用普通任务');
    await expect.poll(async () => (await pending(page)).length).toBe(0);
    const second = (await snapshot(request)).tasks.value.find(task => task.recognitionHistory?.some(event => event.storageId === 'candidate-b'));
    expect(second).toBeTruthy();
    const stage = await holdLocalEncryption(page);
    try {
      await task(page, 'existing').locator('.task-open').click();
      const drawer = page.locator('#task-drawer');
      if (action === 'save') {
        await drawer.getByRole('button', { name: '编辑', exact: true }).click();
        await drawer.getByLabel('任务名称', { exact: true }).fill('迟到暂存的任务 A');
        await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
      } else await drawer.getByRole('button', { name: '移入回收站', exact: true }).click();
      await stage.entered();
      // The previous operation is still before the encrypted IndexedDB durability boundary.
      expect((await snapshot(request)).tasks.version).toBe(2);
      await drawer.locator('[data-action="drawer-close"]').click();
      await task(page, second.id).locator('.task-open').click();
      await drawer.getByRole('button', { name: '编辑', exact: true }).click();
      const title = drawer.getByLabel('任务名称', { exact: true }), notes = drawer.getByLabel(/^说明/);
      await title.fill('B 编辑器中尚未保存的标题');
      await notes.fill('B 尚未保存的说明，不能被 A 的迟到暂存结果覆盖。');
      await title.focus();
      const control = await title.elementHandle();
      await stage.release();
      await expect.poll(async () => (await snapshot(request)).tasks.version).toBe(3);
      await expect.poll(async () => (await pending(page)).length).toBe(0);
      await expect(drawer).toBeVisible();
      await expect(drawer.locator('#task-edit-form')).toHaveAttribute('data-id', second.id);
      await expect(title).toHaveValue('B 编辑器中尚未保存的标题');
      await expect(notes).toHaveValue('B 尚未保存的说明，不能被 A 的迟到暂存结果覆盖。');
      await expect(title).toBeFocused();
      expect(await control.evaluate(node => node === document.querySelector('#task-edit-form [name="title"]'))).toBe(true);
      const saved = await snapshot(request), first = saved.tasks.value.find(task => task.id === 'existing');
      expect(saved.tasks.version).toBe(3);
      if (action === 'save') expect(first.title).toBe('迟到暂存的任务 A');
      else expect(first.recycleBin).toBeTruthy();
      expect(saved.tasks.value.find(task => task.id === second.id).title).toBe('切换用普通任务');
    } finally { await stage.release().catch(() => {}); }
  });
}

for (const action of ['edit', 'complete', 'delete']) {
  test(`a staged review-created task can ${action} before its creation reply`, async ({ page, context, request }) => {
    await open(page, context);
    const held = await holdFirstResolve(page);
    try {
      await edit(page, review(page, 'candidate-a'), '初次核对后的任务');
      await held.entered;
      const id = held.requests[0].operation.intent.newTaskId;
      await expect(task(page, id)).toBeVisible();
      expect((await snapshot(request)).tasks.value.map(task => task.id)).toEqual(['existing']);
      if (action === 'edit') {
        await edit(page, task(page, id), '回执之前再次编辑');
        await expect(task(page, id)).toContainText('回执之前再次编辑');
      } else if (action === 'complete') {
        await task(page, id).locator('[data-task-complete]').click();
        await expect(task(page, id)).toHaveCount(0);
      } else {
        await task(page, id).locator('.task-open').click();
        await page.locator('#task-drawer').getByRole('button', { name: '移入回收站', exact: true }).click();
        await expect(page.locator('#task-drawer')).not.toBeVisible();
        await expect(task(page, id)).toHaveCount(0);
      }
      await expect.poll(async () => (await pending(page)).length).toBe(2);
      held.release();
      await expect.poll(async () => (await pending(page)).length).toBe(0);
      const saved = await snapshot(request), created = saved.tasks.value.find(task => task.id === id);
      expect(saved.tasks.version).toBe(3);
      expect(saved.receipts).toBe(2);
      expect(saved.tasks.value).toHaveLength(2);
      expect(created.recognitionHistory.filter(event => event.storageId === 'candidate-a')).toHaveLength(1);
      if (action === 'edit') expect(created.title).toBe('回执之前再次编辑');
      else if (action === 'complete') expect(created.status).toBe('completed');
      else expect(created.recycleBin).toBeTruthy();
      expect(saved.candidates[0]).toEqual({ id: 'candidate-a', state: 'applied', version: 2 });
    } finally { held.release(); }
  });
}

test('two candidate completions and an ordinary completion stage without waiting for the first reply', async ({ page, context, request }) => {
  await open(page, context);
  const held = await holdFirstResolve(page);
  try {
    await review(page, 'candidate-a').locator('[data-task-complete]').click();
    await held.entered;
    await review(page, 'candidate-b').locator('[data-task-complete]').click();
    await task(page, 'existing').locator('[data-task-complete]').click();
    await expect(review(page, 'candidate-a')).toHaveCount(0);
    await expect(review(page, 'candidate-b')).toHaveCount(0);
    await expect(task(page, 'existing')).toHaveCount(0);
    await expect.poll(async () => (await pending(page)).length).toBe(3);
    expect(held.requests).toHaveLength(1);
    expect((await snapshot(request)).tasks.version).toBe(1);
    held.release();
    await expect.poll(async () => (await pending(page)).length).toBe(0);
    const saved = await snapshot(request);
    expect(saved.tasks.version).toBe(4);
    expect(saved.receipts).toBe(3);
    expect(saved.tasks.value).toHaveLength(3);
    expect(saved.tasks.value.every(task => task.status === 'completed')).toBe(true);
    expect(saved.candidates.every(item => item.state === 'applied' && item.version === 2)).toBe(true);
  } finally { held.release(); }
});

test('closing before delivery restores the ordered mixed review and ordinary queue', async ({ page, context, request }) => {
  await open(page, context);
  const held = await holdFirstResolve(page);
  try {
    await review(page, 'candidate-a').locator('[data-task-complete]').click();
    await held.entered;
    await edit(page, task(page, 'existing'), '刷新恢复的普通编辑');
    await review(page, 'candidate-b').locator('[data-import-ignore]').click();
    await expect.poll(async () => (await pending(page)).length).toBe(3);
    const before = await pending(page), createdId = before[0].intent.newTaskId;
    expect(before.map(operation => operation.intent.kind)).toEqual(['review', 'save', 'review']);
    expect((await snapshot(request)).tasks.version).toBe(1);
    await page.close();
    held.release();
    const restored = await context.newPage();
    await open(restored, context);
    await expect(task(restored, 'existing')).toContainText('刷新恢复的普通编辑');
    await expect.poll(async () => (await pending(restored)).length).toBe(0);
    const saved = await snapshot(request);
    expect(saved.tasks.version).toBe(3);
    expect(saved.receipts).toBe(3);
    expect(saved.tasks.value.find(task => task.id === createdId).status).toBe('completed');
    expect(saved.candidates).toEqual([
      { id: 'candidate-a', state: 'applied', version: 2 },
      { id: 'candidate-b', state: 'ignored', version: 2 },
    ]);
  } finally { held.release(); }
});

test('a committed review with a lost reply reconciles once on refresh', async ({ page, context, request }) => {
  await open(page, context);
  let committed = false, createdId;
  await page.route(resolveURL, async route => {
    createdId = route.request().postDataJSON().operation.intent.newTaskId;
    const response = await route.fetch({ url: api + new URL(route.request().url()).pathname });
    expect(response.ok()).toBeTruthy();
    committed = true;
    await route.abort('failed');
  });
  await review(page, 'candidate-a').locator('[data-task-complete]').click();
  await expect.poll(() => committed).toBe(true);
  await expect(page.getByRole('button', { name: '重试保存', exact: true })).toBeVisible();
  expect(await pending(page)).toHaveLength(1);
  await page.unroute(resolveURL);
  await page.reload();
  await expect.poll(async () => (await pending(page)).length).toBe(0);
  const saved = await snapshot(request);
  expect(saved.tasks.version).toBe(2);
  expect(saved.receipts).toBe(1);
  expect(saved.tasks.value.filter(task => task.id === createdId)).toHaveLength(1);
  expect(saved.tasks.value.find(task => task.id === createdId).recognitionHistory).toHaveLength(1);
  expect(saved.candidates[0]).toEqual({ id: 'candidate-a', state: 'applied', version: 2 });
  await expect(review(page, 'candidate-a')).toHaveCount(0);
});

test('ignore and its immediate undo use the same durable candidate sequence', async ({ page, context, request }) => {
  await open(page, context);
  const held = await holdFirstResolve(page);
  try {
    await review(page, 'candidate-a').locator('[data-import-ignore]').click();
    await held.entered;
    await expect(review(page, 'candidate-a')).toHaveCount(0);
    await page.getByRole('button', { name: '撤销', exact: true }).click();
    await expect(review(page, 'candidate-a')).toBeVisible();
    await expect.poll(async () => (await pending(page)).length).toBe(2);
    expect((await snapshot(request)).candidates[0]).toEqual({ id: 'candidate-a', state: 'pending', version: 1 });
    held.release();
    await expect.poll(async () => (await pending(page)).length).toBe(0);
    let saved = await snapshot(request);
    expect(saved.tasks.version).toBe(1);
    expect(saved.receipts).toBe(2);
    expect(saved.candidates[0]).toEqual({ id: 'candidate-a', state: 'pending', version: 3 });
    expect(held.requests.map(body => [body.action, body.version])).toEqual([['ignore', 1], ['reopen', 2]]);
    await review(page, 'candidate-a').locator('[data-import-ignore]').click();
    await expect(review(page, 'candidate-a')).toHaveCount(0);
    await expect.poll(async () => (await pending(page)).length).toBe(0);
    await page.reload();
    await expect(review(page, 'candidate-a')).toHaveCount(0);
    saved = await snapshot(request);
    expect(saved.candidates[0]).toEqual({ id: 'candidate-a', state: 'ignored', version: 4 });
    expect(saved.receipts).toBe(3);
    expect(saved.tasks.version).toBe(1);
  } finally { held.release(); }
});
