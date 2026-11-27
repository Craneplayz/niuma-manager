import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';
import { existsSync } from 'node:fs';

// Real API, SQLite and frontend; only the billed model boundary is synthetic.
const harness = String.raw`
import copy,hashlib,json,sys,threading,time
from pathlib import Path
import app,recognition

app.DB=Path(sys.argv[1])/'browser.sqlite3'
app.initialize()
with app.connection() as db:
    db.execute('INSERT INTO users VALUES(?,?,?,?,?)',('synthetic','browser@text.test','文本测试','00'*16,'unused'))
    db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(b'synthetic-browser-session').hexdigest(),'synthetic',time.time()+3600))
fixture=json.loads((Path.cwd()/'contracts/text-to-app/text-contract-v0.2.json').read_text())
pending={}
def recognize(request,ApiError,*,on_admitted=None):
    item={'event':threading.Event(),'mode':'empty'}
    pending.setdefault(request['request_id'],[]).append(item)
    if on_admitted:on_admitted()
    if not item['event'].wait(30):raise ApiError(502,'合成识别等待超时')
    if item['mode']=='configuration':
        error=ApiError(503,'文本识别配置不可用，请检查当前环境的模型配置文件')
        error.code='model_configuration'
        raise error
    record=request['records'][0]; tasks=[]
    if item['mode']=='success':
        task=copy.deepcopy(fixture['expected']['tasks'][0])
        task['candidate_id']=record['input_id']+':candidate:1';task['input_id']=record['input_id']
        task['sources']=[dict(input_id=record['input_id'],platform='manual',url=None,label=record['source_label'],source_item_id=None)]
        for evidence in task['evidence']:evidence['input_id']=record['input_id']
        tasks=[task]
    status='success' if tasks else 'failed' if item['mode']=='failure' else 'no_tasks'
    return dict(contract_version='0.2',request_id=request['request_id'],status=status,tasks=tasks,
        record_results=[dict(input_id=record['input_id'],status=status,task_ids=[t['candidate_id'] for t in tasks],warnings=[])],
        errors=[dict(input_id=record['input_id'],code='invalid_agent_output',message='合成识别结果异常',retryable=True)] if status=='failed' else [])
recognition.recognize=recognize
class Handler(app.Handler):
    def route(self):
        if self.path=='/__test/finish':
            body=self.body();item=pending[body['request_id']][body.get('index',-1)]
            item['mode']=body['mode'];item['event'].set();return self.reply(200,{})
        return super().route()
with app.ThreadingHTTPServer(('127.0.0.1',0),Handler) as server:
    print('TEXT_JOB_TEST_READY '+json.dumps(dict(url='http://127.0.0.1:'+str(server.server_port),cookie=app.runtime_environment.cookie_name())),flush=True)
    server.serve_forever()
`;

test.describe.configure({ mode: 'serial' });
let folder, backend, api, cookie;
test.beforeAll(async () => {
  folder = await mkdtemp(join(tmpdir(), 'niuma-text-browser-'));
  const script = join(folder, 'harness.py'), root = resolve('..');
  await writeFile(script, harness);
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(NIUMA_|DDL_|VITE_|WECHAT_|TEXT_HARNESS)/.test(key)));
  env.PYTHONPATH = root + ':' + join(root, 'server');
  env.NIUMA_ALLOWED_ORIGINS = 'http://127.0.0.1:' + (process.env.NIUMA_BROWSER_TEST_PORT || 18763);
  const localPython = join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  const python = process.env.NIUMA_TEST_PYTHON || (existsSync(localPython) ? localPython : process.platform === 'win32' ? 'python' : 'python3');
  backend = spawn(python, ['-u', script, folder], { cwd: root, env, stdio: ['ignore','pipe','pipe'] });
  await new Promise((done, reject) => {
    let output = '', errors = '';
    const timer = setTimeout(() => reject(new Error('Synthetic API startup timed out: ' + errors)),10000);
    backend.stderr.on('data', bytes => { errors += bytes.toString(); });
    backend.on('exit', code => { clearTimeout(timer); reject(new Error('Synthetic API exited '+code+': '+errors)); });
    backend.stdout.on('data', bytes => {
      output += bytes.toString();
      const ready = output.split('\n').find(line => line.startsWith('TEXT_JOB_TEST_READY '));
      if (ready) { ({url:api,cookie} = JSON.parse(ready.slice('TEXT_JOB_TEST_READY '.length))); clearTimeout(timer); done(); }
    });
  });
});
test.afterAll(async () => {
  if (backend && backend.exitCode === null) { backend.kill(); await once(backend,'exit'); }
  if (folder) await rm(folder,{recursive:true,force:true});
});

async function open(page, context) {
  await context.addCookies([{name:cookie,value:'synthetic-browser-session',domain:'127.0.0.1',path:'/'}]);
  await page.route('**/api/**', route => route.continue({url: api + new URL(route.request().url()).pathname}));
  await page.goto('/?page=tasks&view=list');
  await page.locator('.add-task').click();
}
async function start(page, label = '') {
  await page.locator('#quick-text').fill(label + '本次有两项安排。请在2026年9月30日前提交需求报告。');
  const accepted = page.waitForResponse(response => response.url().endsWith('/api/recognition/text-jobs') && response.request().method()==='POST');
  await page.getByRole('button',{name:'加入',exact:true}).click();
  const receipt = await accepted;
  expect(receipt.ok()).toBeTruthy();
  return (await receipt.json()).jobs.find(job => job.request_id === receipt.request().postDataJSON().request_id);
}
// Unloading can interrupt an in-flight progress read. Exercise the existing
// explicit transport recovery while keeping the original server job receipt.
async function reloadProgress(page) {
  await page.reload();
  await expect(page.locator('.task-page')).toBeVisible();
  const retry = page.locator('.status-note').filter({hasText: '无法连接服务，请检查网络后重试'})
    .getByRole('button', {name: '重试', exact: true});
  while (await retry.count()) await retry.first().click();
}

async function finish(request, job, mode) {
  const response = await request.post(api+'/__test/finish',{data:{request_id:job.request_id,mode}});
  expect(response.ok()).toBeTruthy();
}


test('task-page text import survives reload and retries into a single saved task', async ({page,context,request}) => {
  await open(page,context);
  const job = await start(page);
  await expect(page.locator('#quick-task-form')).toHaveCount(0);
  await reloadProgress(page);
  await expect(page.getByText('正在识别，完成后将自动加入清单…')).toBeVisible();
  await finish(request,job,'failure');
  const retry = page.locator('[data-quick-job][data-job-action="retry"]');
  await expect(retry).toBeVisible();
  const acceptance = page.waitForResponse(r => r.url().endsWith('/retry') && r.request().method() === 'POST');
  await retry.click();
  expect((await acceptance).ok()).toBeTruthy();
  await expect.poll(async () => (await (await page.request.get(api+'/api/recognition/text-jobs')).json()).jobs[0].state).toBe('recognizing');
  await finish(request,job,'success');
  const task = page.locator('.task-row[data-task-id]').filter({hasText:'提交需求报告'});
  await expect(task).toHaveCount(1);
  await reloadProgress(page);
  await expect(task).toHaveCount(1);
  await expect(page.locator('.task-row.is-pending-review')).toHaveCount(0);
});


test('four consecutive notices can finish independently after refresh', async ({page,context,request}) => {
  await open(page,context);
  const jobs = [];
  for (const label of ['A','B','C','D']) {
    if (jobs.length) await page.locator('.add-task').click();
    jobs.push(await start(page,label));
    await expect(page.locator('#quick-task-form')).toHaveCount(0);
  }
  await reloadProgress(page);
  const pending = page.getByText('正在识别，完成后将自动加入清单…');
  await expect(pending).toHaveCount(4);
  const states = async () => (await (await page.request.get(api+'/api/recognition/text-jobs')).json()).jobs;
  await expect.poll(async () => (await states()).filter(item =>
    jobs.some(job => job.request_id === item.request_id) && item.state === 'recognizing').length).toBe(4);
  // A stays in progress while the three later notices reach the task list.
  for (const job of jobs.slice(1)) await finish(request,job,'success');
  await expect(pending).toHaveCount(1);
  const savedIds = async () => (await (await page.request.get(api+'/api/workspace')).json()).tasks.value
    .flatMap(task => task.recognitionHistory || []).map(entry => entry.requestId)
    .filter(id => jobs.some(job => job.request_id === id)).sort();
  expect(await savedIds()).toEqual(jobs.slice(1).map(job => job.request_id).sort());
  await finish(request,jobs[0],'success');
  await expect(pending).toHaveCount(0);
  await reloadProgress(page);
  expect(await savedIds()).toEqual(jobs.map(job => job.request_id).sort());
});

test('a notice submitted with all twenty windows busy survives reload and starts in the next free window', async ({page,context,request}) => {
  await open(page,context);
  const fixture = JSON.parse(await readFile('../contracts/text-to-app/text-contract-v0.2.json', 'utf8'));
  const records = [{...fixture.request.records[0], platform: 'manual'}];
  const blockers = [];
  for (let i = 0; i < 20; i++) {
    const id = 'window-' + i;
    const response = await page.request.post(api+'/api/recognition/text-jobs', {
      data: {...fixture.request, request_id:id, records}
    });
    expect(response.ok()).toBeTruthy();
    blockers.push({request_id:id});
  }
  const waiting = await start(page, '排队样例');
  expect(waiting.state).toBe('queued');
  await reloadProgress(page);
  await expect(page.getByText('已收到，正在排队等待识别…')).toBeVisible();
  await finish(request,blockers.pop(),'empty');
  await expect(page.getByText('正在识别，完成后将自动加入清单…')).toBeVisible();
  await finish(request,waiting,'success');
  await expect.poll(async () => {
    const response = await page.request.get(api + '/api/workspace');
    return ((await response.json()).tasks?.value || []).some(task =>
      task.recognitionHistory?.some(entry => entry.requestId === waiting.request_id));
  }).toBe(true);
  for (const job of blockers) await finish(request,job,'empty');
});

test('poll transport failure remains recoverable after reload without a second submission', async ({page,context,request})=>{
 await open(page,context);
 let posts=0;page.on('request',req=>{if(req.method()==='POST'&&req.url().endsWith('/api/recognition/text-jobs'))posts++;});
 await page.route('**/api/recognition/text-jobs',route=>route.request().method()==='GET'?route.abort('failed'):route.fallback());
 const job=await start(page,'轮询恢复');
 await expect(page.getByRole('button',{name:'重试',exact:true})).toBeVisible();await page.reload();
 await expect(page.getByRole('button',{name:'重试',exact:true})).toBeVisible();
 await page.unroute('**/api/recognition/text-jobs');await page.getByRole('button',{name:'重试',exact:true}).click();
 await expect(page.getByText('正在识别，完成后将自动加入清单…')).toBeVisible();
 await finish(request,job,'success');
 await expect.poll(async()=>{const state=await(await page.request.get(api+'/api/workspace')).json();return(state.tasks?.value||[]).filter(t=>t.recognitionHistory?.some(e=>e.requestId===job.request_id)).length;}).toBe(1);
 expect(posts).toBe(1);await page.reload();await expect(page.getByRole('button',{name:'重试',exact:true})).toHaveCount(0);expect(posts).toBe(1);
});
