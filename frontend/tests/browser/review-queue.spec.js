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
                second=dict(task,id='second',title='切换用普通任务')
                db.execute('UPDATE workspace SET value=?,version=2 WHERE user_id="alice" AND name="tasks"',(json.dumps([task,second]),))
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

for (const action of ['save', 'delete']) {
  test(`a late ordinary ${action} staging result preserves another task's open editor and unsent draft`, async ({ page, context, request }) => {
    await open(page, context);
    const second = {id:'second'};
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
