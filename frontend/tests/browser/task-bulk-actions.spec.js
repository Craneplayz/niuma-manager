import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';

// Exercise the real task/candidate transaction and browser journal. Routes below
// forward HTTP; individual tests only delay or interrupt transport.
const harness = String.raw`
import copy,hashlib,json,sys,time
from pathlib import Path
import app
app.DB=Path(sys.argv[1])/'bulk.sqlite3'
app.initialize(recover=False)
fixture=json.loads((Path(app.__file__).parent.parent/'contracts/text-to-app/text-contract-v0.2.json').read_text())
case=0
class Handler(app.Handler):
    def route(self):
        global case
        if self.path=='/__test/reset':
            case+=1
            users=[name+'-'+str(case) for name in ('alice','bob')]
            with app.connection() as db:
                for uid in users:
                    db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,uid+'@bulk.test',uid,'00'*16,'unused'))
                    db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(uid.encode()).hexdigest(),uid,time.time()+3600))
                    def task(id,title,**fields):
                        return dict(id=id,title=title,status='not_started',description='',course='数据结构',content_type='task',due=None,plans=[],subtasks=[],source='教学网',sourceUrl='https://example.test/course',**fields)
                    tasks=[task('a','同组 任务 A'),task('b','同组 任务 B'),task('c','其他任务 C'),task('notice','既有通知')]
                    tasks[0]['due']=dict(date='2026-10-20',time='18:00',precision='datetime')
                    tasks[1]['due']=dict(date='2026-10-21',precision='date')
                    tasks[2]['course']='其他课程'
                    tasks[3]['content_type']='notice'
                    db.execute('INSERT INTO workspace VALUES(?,?,?,1)',(uid,'tasks',json.dumps(tasks)))
                    request=copy.deepcopy(fixture['request']);request['request_id']='bulk-source'
                    cards=[]
                    for suffix,title,fields in [('new','同组 待核对新任务',{}),('change','任务 A 延期建议',dict(notice_type='postpone',target_reference='a')),('notice','待核对培训通知',dict(content_type='notice'))]:
                        candidate=copy.deepcopy(fixture['expected']['tasks'][0])
                        candidate.update(dict(candidate_id='bulk-'+suffix,title=title,description='合成说明',course_or_project='数据结构',needs_review=True,review_reasons=['请确认内容'],notice_type='create',content_type='task',deadline=dict(kind='deadline',status='explicit',precision='date',value='2026-10-21')),**fields)
                        cards.append(candidate)
                        db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',(uid+'-'+suffix,uid,'bulk-source',json.dumps(candidate)))
                    result=copy.deepcopy(fixture['expected']);result['tasks']=cards
                    db.execute('INSERT INTO recognition_batches(user_id,request_id,fingerprint,request,result,demo) VALUES(?,?,?,?,?,0)',(uid,'bulk-source','synthetic',json.dumps(request),json.dumps(result)))
            return self.reply(200,dict(users=users))
        if self.path=='/__test/arrival':
            uid=self.body()['owner']
            with app.connection() as db:
                original=db.execute('SELECT candidate FROM recognition_candidates WHERE id=?',(uid+'-new',)).fetchone()
                candidate=json.loads(original['candidate']);candidate.update(candidate_id='bulk-arrival',title='新导入的待核对任务')
                db.execute('INSERT INTO recognition_candidates(id,user_id,request_id,candidate) VALUES(?,?,?,?)',(uid+'-arrival',uid,'bulk-source',json.dumps(candidate)))
                import recognition
                batches=recognition.batches(db,uid)
            return self.reply(200,dict(batches=batches))
        return super().route()
with app.ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
    print('BULK_READY '+json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port),cookie=app.runtime_environment.cookie_name())),flush=True)
    server.serve_forever()
`;

test.use({ timezoneId: 'Asia/Shanghai' });
test.describe.configure({ mode: 'serial' });
let folder, backend, api, cookie, users;
test.beforeAll(async () => {
  test.setTimeout(60000);
  folder = await mkdtemp(join(tmpdir(), 'niuma-task-bulk-'));
  const script = join(folder, 'harness.py'), root = resolve('..');
  await writeFile(script, harness);
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(NIUMA_|DDL_|VITE_|WECHAT_|TEXT_HARNESS)/.test(key)));
  env.PYTHONPATH = root + ':' + join(root, 'server');
  env.NIUMA_ALLOWED_ORIGINS = 'http://127.0.0.1:' + (process.env.NIUMA_BROWSER_TEST_PORT || 18763);
  backend = spawn(process.env.NIUMA_TEST_PYTHON || 'python3', ['-u', script, folder], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  await new Promise((done, reject) => {
    let output = '', errors = '';
    const timer = setTimeout(() => reject(new Error('Bulk API startup: ' + errors + '; output: ' + output)), 40000);
    backend.on('error', error => { clearTimeout(timer); reject(error); });
    backend.stderr.on('data', bytes => { errors += bytes; });
    backend.on('exit', code => { clearTimeout(timer); reject(new Error('Bulk API exit ' + code + ': ' + errors)); });
    backend.stdout.on('data', bytes => {
      output += bytes;
      const line = output.split('\n').find(line => line.startsWith('BULK_READY '));
      if (line) { ({ url: api, cookie } = JSON.parse(line.slice(11))); clearTimeout(timer); done(); }
    });
  });
});
test.afterAll(async () => {
  if (backend && backend.exitCode === null) { backend.kill(); await once(backend, 'exit'); }
  if (folder) await rm(folder, { recursive: true, force: true });
});
test.beforeEach(async ({ request }) => {
  const response = await request.post(api + '/__test/reset');
  expect(response.ok()).toBeTruthy();
  ({ users } = await response.json());
});

const row = (page, id) => page.locator(`[data-task-id="${id}"]`);
const candidateRow = (page, suffix = 'new') => page.locator(`[data-review-id="${users[0]}-${suffix}"]`);
const action = (page, name) => page.locator(`[data-bulk-action="${name}"]:visible`);
const select = (page, id) => page.locator(`input[data-bulk-select="${id}"]`);
async function open(page, context) {
  await page.clock.setFixedTime(new Date('2026-10-20T04:00:00Z'));
  await context.addCookies([{ name: cookie, value: users[0], domain: '127.0.0.1', path: '/' }]);
  await page.route('**/api/**', route => route.continue({ url: api + new URL(route.request().url()).pathname }));
  await page.goto('/');
  await expect(row(page, 'a')).toBeVisible();
  await expect(candidateRow(page)).toBeVisible();
}
async function navigate(page, name) {
  if (await page.locator('#sidebar-show').isVisible()) await page.locator('#sidebar-show').click();
  await page.getByRole('button', { name, exact: true }).click();
}
async function choose(page, ...ids) {
  if (!await action(page, 'select-all').count()) await action(page, 'enter').click();
  for (const id of ids) await select(page, id).check();
}
const snapshot = page => page.evaluate(async () => {
  const workspace = await (await fetch('/api/workspace')).json();
  const batches = await (await fetch('/api/recognition/batches')).json();
  return { ...workspace, items: batches.flatMap(batch => batch.items) };
});
const journal = page => page.evaluate(async owner => {
  const { readOperations } = await import('/src/task-journal.js');
  return readOperations(owner);
}, users[0]);
const confirmedTask = (state, suffix = 'new') => state.tasks.value.find(task => task.recognitionHistory?.some(event => event.storageId === users[0] + '-' + suffix));
async function changeDate(page, date) {
  await action(page, 'deadline').click();
  const form = page.locator('#bulk-deadline-form');
  await form.locator('input[name="date"]').fill(date);
  await form.getByRole('button', { name: '应用日期', exact: true }).click();
  await expect(form).not.toBeVisible();
}

test('selection follows current filters, excludes attached changes, and viewing or cancelling preserves review state', async ({ page, context }, info) => {
  await open(page, context);
  await page.locator('#task-search').fill('同组');
  await action(page, 'enter').click();
  await action(page, 'select-all').click();
  await expect(page.locator('input[data-bulk-select]:checked')).toHaveCount(3);
  await expect(select(page, 'c')).toHaveCount(0);
  await expect(select(page, 'review:' + users[0] + '-change')).toHaveCount(0);
  await expect(row(page, 'a')).toContainText('待核对变更');
  await page.screenshot({ path: info.outputPath('bulk-mixed-selection.png'), fullPage: true });
  if (info.project.name !== 'desktop') {
    await candidateRow(page).scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo(0, document.scrollingElement.scrollHeight));
    await expect.poll(async () => {
      const last = await candidateRow(page).boundingBox();
      const toolbar = await page.locator('#bulk-controls .bulk-toolbar').boundingBox();
      return last && toolbar ? last.y + last.height <= toolbar.y + 1 : false;
    }, { message: 'The last candidate must scroll completely above the fixed bulk toolbar' }).toBe(true);
    for (const button of await page.locator('#bulk-controls .bulk-toolbar button').all()) {
      const box = await button.boundingBox();
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
  }
  await candidateRow(page).locator('.task-open').click();
  await expect(page.locator('#task-drawer')).toContainText('待核对');
  await page.locator('#task-drawer [data-action="drawer-close"]').click();
  await action(page, 'deadline').click();
  await page.locator('#dialog').getByRole('button', { name: '取消', exact: true }).click();
  expect((await snapshot(page)).items.every(item => item.state === 'pending')).toBe(true);
  await page.locator('#task-search').fill('其他任务');
  await expect(page.locator('input[data-bulk-select]:checked')).toHaveCount(0);
  await expect(row(page, 'c')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('bulk-filter-selection.png'), fullPage: true });
});


test('newly arriving candidates stay unselected while existing selections survive the refresh', async ({ page, context, request }) => {
  await open(page, context);
  await choose(page, 'a', 'review:' + users[0] + '-new');
  const response = await request.post(api + '/__test/arrival', { data: { owner: users[0] } });
  expect(response.ok()).toBeTruthy();
  const detail = await response.json();
  await page.evaluate(detail => window.dispatchEvent(new CustomEvent('niuma:bulk-saved', { detail })), detail);
  await expect(candidateRow(page, 'arrival')).toBeVisible();
  await expect(select(page, 'review:' + users[0] + '-arrival')).not.toBeChecked();
  await expect(select(page, 'a')).toBeChecked();
  await expect(select(page, 'review:' + users[0] + '-new')).toBeChecked();
  await expect(page.locator('input[data-bulk-select]:checked')).toHaveCount(2);
});

test('mixed completion confirms new candidates once, leaves attached changes pending, and undo keeps the confirmed task ordinary', async ({ page, context }, info) => {
  await open(page, context);
  await choose(page, 'a', 'review:' + users[0] + '-new');
  await action(page, 'complete').click();
  await expect(candidateRow(page)).toHaveCount(0);
  await expect(row(page, 'a')).toHaveCount(0);
  const saved = await snapshot(page), created = confirmedTask(saved);
  expect(saved.tasks.version).toBe(2);
  expect(created.status).toBe('completed');
  expect(saved.tasks.value.find(task => task.id === 'a').status).toBe('completed');
  expect(saved.items.find(item => item.id === users[0] + '-new').state).toBe('applied');
  expect(saved.items.find(item => item.id === users[0] + '-change').state).toBe('pending');
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(row(page, 'a')).toBeVisible();
  await expect(row(page, created.id)).toBeVisible();
  await expect(row(page, created.id).locator('.review-label')).toHaveCount(0);
  const undone = await snapshot(page);
  expect(confirmedTask(undone).status).toBe('not_started');
  expect(undone.items.find(item => item.id === users[0] + '-new').state).toBe('applied');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('bulk-confirmed-and-undone.png'), fullPage: true });
});

test('mixed date changes preserve per-task times and can clear dates with an exact batch undo', async ({ page, context }) => {
  await open(page, context);
  await choose(page, 'a', 'review:' + users[0] + '-new');
  await changeDate(page, '2026-10-28');
  await expect(candidateRow(page)).toHaveCount(0);
  const saved = await snapshot(page), created = confirmedTask(saved);
  expect(saved.tasks.value.find(task => task.id === 'a').due).toEqual({ date: '2026-10-28', time: '18:00', precision: 'datetime' });
  expect(created.due).toEqual({ date: '2026-10-28', precision: 'date' });
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(row(page, 'a').locator('.task-deadline')).toContainText('18:00');
  await expect.poll(async () => (await snapshot(page)).tasks.value.find(task => task.id === 'a').due.date).toBe('2026-10-20');
  await choose(page, 'a', created.id);
  await changeDate(page, '');
  await expect.poll(async () => (await snapshot(page)).tasks.value.filter(task => ['a', created.id].includes(task.id)).every(task => task.due === null)).toBe(true);
  expect((await snapshot(page)).tasks.value.find(task => task.id === 'b').due.date).toBe('2026-10-21');
});

test('mixed deletion moves ordinary tasks to trash, ignores candidates, and batch undo restores their respective identities', async ({ page, context }) => {
  await open(page, context);
  await choose(page, 'a', 'review:' + users[0] + '-new');
  await action(page, 'trash').click();
  await expect(candidateRow(page)).toHaveCount(0);
  await expect(row(page, 'a')).toHaveCount(0);
  const deleted = await snapshot(page);
  expect(deleted.tasks.value.find(task => task.id === 'a').recycleBin).toBeTruthy();
  expect(deleted.items.find(item => item.id === users[0] + '-new').state).toBe('ignored');
  expect(confirmedTask(deleted)).toBeUndefined();
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(row(page, 'a')).toBeVisible();
  await expect(candidateRow(page)).toBeVisible();
  const restored = await snapshot(page);
  expect(restored.tasks.value.find(task => task.id === 'a').recycleBin).toBeUndefined();
  expect(restored.items.find(item => item.id === users[0] + '-new')).toMatchObject({ state: 'pending', version: 3 });
  expect(confirmedTask(restored)).toBeUndefined();
});

test('notifications use explicit read/unread states and completed tasks reopen together', async ({ page, context }) => {
  await open(page, context);
  await navigate(page, '通知');
  await choose(page, 'notice', 'review:' + users[0] + '-notice');
  await action(page, 'complete').click();
  await expect(candidateRow(page, 'notice')).toHaveCount(0);
  const read = await snapshot(page), created = confirmedTask(read, 'notice');
  expect(read.tasks.value.find(task => task.id === 'notice').status).toBe('completed');
  expect(created.status).toBe('completed');
  await choose(page, 'notice', created.id);
  await action(page, 'reopen').click();
  await expect.poll(async () => (await snapshot(page)).tasks.value.filter(task => task.content_type === 'notice').every(task => task.status === 'not_started')).toBe(true);
  await navigate(page, '全部任务');
  await choose(page, 'a', 'b');
  await action(page, 'complete').click();
  await expect(row(page, 'a')).toHaveCount(0);
  await navigate(page, '已完成');
  await choose(page, 'a', 'b');
  await action(page, 'reopen').click();
  await expect(row(page, 'a')).toHaveCount(0);
  expect((await snapshot(page)).tasks.value.filter(task => ['a', 'b'].includes(task.id)).every(task => task.status === 'not_started')).toBe(true);
});

test('trash supports selecting and restoring an entire deleted group', async ({ page, context }) => {
  await open(page, context);
  await choose(page, 'a', 'b');
  await action(page, 'trash').click();
  await expect(row(page, 'a')).toHaveCount(0);
  await page.locator('[data-action="open-trash"]').click();
  const dialog = page.locator('#dialog');
  await dialog.locator('[data-bulk-action="enter"]').click();
  await dialog.locator('[data-bulk-action="select-all"]').click();
  await expect(dialog.locator('input[data-bulk-trash-select]:checked')).toHaveCount(2);
  await dialog.locator('[data-bulk-action="restore"]').click();
  await expect.poll(async () => (await snapshot(page)).tasks.value.every(task => !task.recycleBin)).toBe(true);
  await dialog.locator('[data-action="dialog-close"]').click();
  await expect(row(page, 'a')).toBeVisible();
  await expect(row(page, 'b')).toBeVisible();
});

test('a rejected mixed batch keeps all review state and retries the original journaled operation', async ({ page, context }) => {
  await open(page, context);
  let attempts = 0;
  await page.route('**/api/tasks/bulk', async route => {
    attempts++;
    if (attempts === 1) await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '合成暂时保存失败' }) });
    else await route.continue({ url: api + '/api/tasks/bulk' });
  });
  await choose(page, 'a', 'review:' + users[0] + '-new');
  await action(page, 'complete').click();
  await expect(page.getByRole('button', { name: '重试保存', exact: true })).toBeVisible();
  await expect(candidateRow(page)).toBeVisible();
  await expect(row(page, 'a')).toBeVisible();
  expect((await snapshot(page)).tasks.version).toBe(1);
  expect((await snapshot(page)).items.find(item => item.id === users[0] + '-new').state).toBe('pending');
  expect(await journal(page)).toHaveLength(1);
  await expect(page.locator('#toast')).not.toContainText('已完成 2');
  await page.getByRole('button', { name: '重试保存', exact: true }).click();
  await expect(candidateRow(page)).toHaveCount(0);
  await expect(row(page, 'a')).toHaveCount(0);
  expect((await snapshot(page)).tasks.version).toBe(2);
  expect(await journal(page)).toHaveLength(0);
});

test('lost mixed response reconciles on reload without duplicating a confirmed candidate or increasing the version twice', async ({ page, context }) => {
  await open(page, context);
  let committed = false;
  await page.route('**/api/tasks/bulk', async route => {
    const response = await route.fetch({ url: api + '/api/tasks/bulk' });
    expect(response.ok()).toBeTruthy(); committed = true;
    await route.abort('failed');
  });
  await choose(page, 'a', 'review:' + users[0] + '-new');
  await action(page, 'complete').click();
  await expect.poll(() => committed).toBe(true);
  await expect(page.getByRole('button', { name: '重试保存', exact: true })).toBeVisible();
  expect(await journal(page)).toHaveLength(1);
  await page.unroute('**/api/tasks/bulk');
  await page.reload();
  await expect.poll(async () => (await journal(page)).length).toBe(0);
  await expect(candidateRow(page)).toHaveCount(0);
  const saved = await snapshot(page);
  expect(saved.tasks.version).toBe(2);
  expect(saved.tasks.value.filter(task => task.recognitionHistory?.some(event => event.storageId === users[0] + '-new'))).toHaveLength(1);
  expect(saved.items.find(item => item.id === users[0] + '-new')).toMatchObject({ state: 'applied', version: 2 });
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(row(page, 'a')).toBeVisible();
  await expect(row(page, confirmedTask(saved).id)).toBeVisible();
  const undone = await snapshot(page);
  expect(undone.tasks.version).toBe(3);
  expect(undone.tasks.value.find(task => task.id === 'a').status).toBe('not_started');
  expect(confirmedTask(undone).status).toBe('not_started');
  expect(undone.items.find(item => item.id === users[0] + '-new')).toMatchObject({ state: 'applied', version: 2 });
  await expect(row(page, confirmedTask(saved).id).locator('.review-label')).toHaveCount(0);
});

test('same-page retry recovers the receipt after a lost response and exposes a working whole-batch undo', async ({ page, context }) => {
  await open(page, context);
  let committed = false;
  await page.route('**/api/tasks/bulk', async route => {
    const response = await route.fetch({ url: api + '/api/tasks/bulk' });
    expect(response.ok()).toBeTruthy(); committed = true;
    await route.abort('failed');
  });
  await choose(page, 'a', 'review:' + users[0] + '-new');
  await action(page, 'complete').click();
  await expect.poll(() => committed).toBe(true);
  await expect(page.getByRole('button', { name: '重试保存', exact: true })).toBeVisible();
  expect(await journal(page)).toHaveLength(1);
  expect((await snapshot(page)).tasks.version).toBe(2);
  await page.unroute('**/api/tasks/bulk');
  await page.getByRole('button', { name: '重试保存', exact: true }).click();
  await expect(candidateRow(page)).toHaveCount(0);
  await expect(row(page, 'a')).toHaveCount(0);
  await expect(page.locator('#toast')).toContainText(/已保存|已完成/);
  const saved = await snapshot(page), created = confirmedTask(saved);
  expect(saved.tasks.version).toBe(2);
  expect(created.status).toBe('completed');
  expect(await journal(page)).toHaveLength(0);
  await expect(page.getByRole('button', { name: '撤销', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect(row(page, 'a')).toBeVisible();
  await expect(row(page, created.id)).toBeVisible();
  await expect(row(page, created.id).locator('.review-label')).toHaveCount(0);
  const undone = await snapshot(page);
  expect(undone.tasks.version).toBe(3);
  expect(undone.tasks.value.find(task => task.id === 'a').status).toBe('not_started');
  expect(confirmedTask(undone).status).toBe('not_started');
  expect(undone.items.find(item => item.id === users[0] + '-new')).toMatchObject({ state: 'applied', version: 2 });
  expect(await journal(page)).toHaveLength(0);
});

test('an unrelated ordinary edit can queue behind a slow mixed batch and is preserved after both writes', async ({ page, context }) => {
  await open(page, context);
  let release, entered = false;
  const gate = new Promise(resolve => { release = resolve; });
  await page.route('**/api/tasks/bulk', async route => {
    entered = true; await gate;
    await route.continue({ url: api + '/api/tasks/bulk' });
  });
  try {
    await choose(page, 'a', 'review:' + users[0] + '-new');
    await action(page, 'complete').click();
    await expect.poll(() => entered).toBe(true);
    await row(page, 'c').locator('.task-open').click();
    const drawer = page.locator('#task-drawer');
    await drawer.getByRole('button', { name: '编辑', exact: true }).click();
    await drawer.getByLabel('任务名称', { exact: true }).fill('其他任务 C 保存期间编辑');
    await drawer.getByRole('button', { name: '保存任务', exact: true }).click();
    await expect(drawer).not.toBeVisible();
    release();
    await expect(candidateRow(page)).toHaveCount(0);
    await expect(row(page, 'c')).toContainText('保存期间编辑');
    await expect.poll(async () => (await journal(page)).length).toBe(0);
    const saved = await snapshot(page);
    expect(saved.tasks.version).toBe(3);
    expect(saved.tasks.value.find(task => task.id === 'a').status).toBe('completed');
    expect(confirmedTask(saved).status).toBe('completed');
    expect(saved.tasks.value.find(task => task.id === 'c').title).toBe('其他任务 C 保存期间编辑');
  } finally { release(); }
});

test('account reset clears bulk selection and another account receives no previous selection or writes', async ({ page, context }) => {
  await open(page, context);
  await choose(page, 'a', 'review:' + users[0] + '-new');
  await page.evaluate(async () => (await import('/src/team-store.js')).logout());
  await expect(page.locator('input[data-bulk-select]:checked')).toHaveCount(0);
  await context.addCookies([{ name: cookie, value: users[1], domain: '127.0.0.1', path: '/' }]);
  await page.reload();
  await expect(row(page, 'a')).toBeVisible();
  await expect(action(page, 'enter')).toBeVisible();
  await expect(page.locator('input[data-bulk-select]')).toHaveCount(0);
  const saved = await snapshot(page);
  expect(saved.taskWriteOwner).toBe(users[1]);
  expect(saved.tasks.version).toBe(1);
  expect(saved.items.every(item => item.state === 'pending')).toBe(true);
});
