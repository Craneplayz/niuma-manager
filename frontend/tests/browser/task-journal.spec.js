import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';

// Real HTTP handler, SQLite, cookies and browser IndexedDB. Faults only delay/drop transport.
const harness = String.raw`
import hashlib,json,sys,time
from pathlib import Path
import app
app.DB=Path(sys.argv[1])/'tasks.sqlite3'
app.initialize()
with app.connection() as db:
    for uid in ('alice','bob'):
        db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,uid+'@journal.test',uid,'00'*16,'unused'))
        db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(uid.encode()).hexdigest(),uid,time.time()+3600))
class Handler(app.Handler):
    def route(self):
        if self.path=='/__test/reset':
            with app.connection() as db:
                db.execute('DELETE FROM workspace');db.execute('DELETE FROM task_write_receipts');db.execute('DELETE FROM task_write_epochs')
                for uid in ('alice','bob'):
                    tasks=[dict(id=id,title=uid+' '+id,status='not_started',due=None,plans=[],subtasks=[],sourceUrl='https://example.test/course',recognitionHistory=[dict(candidateId=id)]) for id in ('a','b','c')]
                    db.execute('INSERT INTO workspace VALUES(?,?,?,1)',(uid,'tasks',json.dumps(tasks)))
            return self.reply(200,{})
        return super().route()
with app.ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
    print('JOURNAL_READY '+json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port),cookie=app.runtime_environment.cookie_name())),flush=True)
    server.serve_forever()
`;
test.describe.configure({ mode: 'serial' });
let folder, backend, api, cookie;
test.beforeAll(async () => {
  folder = await mkdtemp(join(tmpdir(), 'niuma-task-journal-'));
  const script = join(folder, 'harness.py'), root = resolve('..');
  await writeFile(script, harness);
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(NIUMA_|DDL_|VITE_|WECHAT_|TEXT_HARNESS)/.test(key)));
  env.PYTHONPATH = root + ':' + join(root, 'server');
  env.NIUMA_ALLOWED_ORIGINS = 'http://127.0.0.1:' + (process.env.NIUMA_BROWSER_TEST_PORT || 18763);
  backend = spawn(process.env.NIUMA_TEST_PYTHON || 'python3', ['-u', script, folder], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  await new Promise((done, reject) => {
    let output = '', errors = '';
    const timer = setTimeout(() => reject(new Error('API startup: '+errors)), 10000);
    backend.stderr.on('data', bytes => { errors += bytes; });
    backend.on('exit', code => { clearTimeout(timer); reject(new Error('API exit '+code+': '+errors)); });
    backend.stdout.on('data', bytes => {
      output += bytes;
      const line = output.split('\n').find(line => line.startsWith('JOURNAL_READY '));
      if (line) { ({url: api, cookie} = JSON.parse(line.slice(14))); clearTimeout(timer); done(); }
    });
  });
});
test.afterAll(async () => {
  if (backend && backend.exitCode === null) { backend.kill(); await once(backend, 'exit'); }
  if (folder) await rm(folder, { recursive: true, force: true });
});
test.beforeEach(async ({ request }) => { expect((await request.post(api+'/__test/reset')).ok()).toBeTruthy(); });
const row = (page, id) => page.locator(`[data-task-id="${id}"]`);
async function open(page, context, user = 'alice') {
  await context.addCookies([{name: cookie, value: user, domain: '127.0.0.1', path: '/'}]);
  await page.route('**/api/**', route => route.continue({ url: api + new URL(route.request().url()).pathname }));
  await page.goto('/'); await expect(page.locator('.task-title').first()).toBeVisible();
}
const workspace = async page => page.evaluate(async () => (await fetch('/api/workspace')).json());
const pending = async page => page.evaluate(async () => {
  const { readOperations } = await import('/src/task-journal.js');
  return (await readOperations('alice')).map(op => op.id);
});
async function edit(page, id, title) {
  await row(page,id).locator('.task-open').click();
  await page.locator('#task-drawer').getByRole('button', {name:'编辑',exact:true}).click();
  await page.getByLabel('任务名称', {exact:true}).fill(title);
  await page.getByRole('button',{name:'保存任务',exact:true}).click();
  await expect(page.locator('#task-drawer')).not.toBeVisible();
}

test('offline edits survive a closed tab and recover without overwriting newer unrelated metadata', async ({page,context}) => {
  await open(page,context);
  await page.route('**/api/data/tasks', route => route.abort('internetdisconnected'));
  await edit(page,'a','断网时已暂存');
  await expect(page.getByRole('button',{name:'重试保存',exact:true})).toBeVisible();
  expect(await pending(page)).toHaveLength(1);
  // Another writer changes a different task while this browser is offline.
  await page.evaluate(async () => {
    const snapshot=(await (await fetch('/api/workspace')).json()).tasks;
    snapshot.value.find(t=>t.id==='b').description='另一窗口修改';
    await fetch('/api/data/tasks?external=1',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(snapshot)});
  });
  await page.close();
  const reopened=await context.newPage(); await open(reopened,context);
  await expect(row(reopened,'a')).toContainText('断网时已暂存');
  await expect.poll(async () => (await pending(reopened)).length).toBe(0);
  const saved=(await workspace(reopened)).tasks;
  expect(saved.version).toBe(3);
  expect(saved.value.find(t=>t.id==='b').description).toBe('另一窗口修改');
  expect(saved.value.every(t=>t.recognitionHistory?.length===1)).toBe(true);
});

test('lost response after commit is reconciled on refresh with no second application or version bump', async ({page,context}) => {
  await open(page,context);
  let committed=false;
  await page.route('**/api/data/tasks', async route => {
    const response=await route.fetch({url:api+'/api/data/tasks'});
    expect(response.ok()).toBeTruthy(); committed=true; await route.abort('failed');
  });
  await row(page,'a').locator('[data-task-complete]').click();
  await expect.poll(()=>committed).toBe(true);
  await expect(page.getByRole('button',{name:'重试保存',exact:true})).toBeVisible();
  expect(await pending(page)).toHaveLength(1);
  await page.unroute('**/api/data/tasks');
  await page.reload();
  await expect.poll(async ()=>(await pending(page)).length).toBe(0);
  const saved=(await workspace(page)).tasks;
  expect(saved.version).toBe(2); expect(saved.value[0].status).toBe('completed');
});

test('refresh while the first request is held restores ordered completion, edit and deletion', async ({page,context}) => {
  await open(page,context);
  let release; const gate=new Promise(resolve=>{release=resolve;});
  await page.route('**/api/data/tasks', async route => { await gate; await route.abort().catch(()=>{}); });
  try {
    await row(page,'a').locator('[data-task-complete]').click();
    await edit(page,'b','队列中编辑');
    await row(page,'c').locator('.task-open').click();
    await page.getByRole('button',{name:'移入回收站',exact:true}).click();
    await expect.poll(async ()=>(await pending(page)).length).toBe(3);
    await page.close(); release();
    const restored=await context.newPage(); await open(restored,context);
    await expect.poll(async ()=>(await pending(restored)).length).toBe(0);
    const saved=(await workspace(restored)).tasks;
    expect(saved.version).toBe(4);
    expect(saved.value.find(t=>t.id==='a').status).toBe('completed');
    expect(saved.value.find(t=>t.id==='b').title).toBe('队列中编辑');
    expect(saved.value.find(t=>t.id==='c').recycleBin).toBeTruthy();
  } finally { release(); }
});

test('same-field multi-window conflict retains the journal until explicit discard', async ({page,context}) => {
  await open(page,context);
  await page.route('**/api/data/tasks', route=>route.abort());
  await edit(page,'a','旧窗口草稿');
  await expect(page.getByRole('button',{name:'重试保存',exact:true})).toBeVisible();
  await page.evaluate(async () => {
    const snapshot=(await (await fetch('/api/workspace')).json()).tasks;
    snapshot.value[0].title='其他窗口的新标题';
    await fetch('/api/data/tasks?external=1',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(snapshot)});
  });
  await page.unroute('**/api/data/tasks'); await page.reload();
  await expect(page.getByRole('button',{name:'重新读取',exact:true})).toBeVisible();
  expect(await pending(page)).toHaveLength(1);
  await page.getByRole('button',{name:'重新读取',exact:true}).click();
  await expect(row(page,'a')).toContainText('其他窗口的新标题');
  expect(await pending(page)).toHaveLength(0);
  expect((await workspace(page)).tasks.version).toBe(2);
});

test('an old tab cannot send its journal through a new account cookie', async ({page,context}) => {
  await open(page,context);
  await page.route('**/api/data/tasks', route=>route.abort());
  await edit(page,'a','仅 Alice 草稿');
  await expect(page.getByRole('button',{name:'重试保存',exact:true})).toBeVisible();
  await context.addCookies([{name:cookie,value:'bob',domain:'127.0.0.1',path:'/'}]);
  await page.unroute('**/api/data/tasks');
  await page.getByRole('button',{name:'重试保存',exact:true}).click();
  await expect(page.locator('#toast')).toBeVisible();
  expect((await workspace(page)).tasks.value[0].title).toBe('bob a');
  expect(await pending(page)).toHaveLength(1);
  await page.reload(); await expect(row(page,'a')).toContainText('bob a');
  expect((await workspace(page)).tasks.version).toBe(1);
});

test('blocked local storage does not optimistically alter a task or send a PUT', async ({page,context}) => {
  await open(page,context);
  let writes=0; page.on('request', request=>{if(request.method()==='PUT')writes++;});
  await page.evaluate(()=>{Object.defineProperty(window,'indexedDB',{value:undefined,configurable:true});});
  await row(page,'a').locator('[data-task-complete]').click();
  await expect(page.locator('#toast')).toContainText('浏览器存储');
  await expect(row(page,'a')).toBeVisible(); expect(writes).toBe(0);
});
