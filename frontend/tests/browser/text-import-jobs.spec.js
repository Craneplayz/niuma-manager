import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';

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
  env.NIUMA_ALLOWED_ORIGINS = 'http://127.0.0.1:18763';
  const python = process.env.NIUMA_TEST_PYTHON || join(root, '.venv/bin/python');
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
  await page.goto('/?page=import&importSource=text');
  await expect(page.locator('#import-text')).toBeVisible();
}
async function start(page) {
  await page.locator('#import-text').fill('请在2026年9月30日前提交需求报告。');
  const accepted = page.waitForResponse(response => response.url().endsWith('/api/recognition/text-jobs') && response.request().method()==='POST');
  await page.getByRole('button',{name:'识别任务',exact:true}).click();
  return (await (await accepted).json()).jobs.find(job => ['queued','recognizing'].includes(job.state));
}
async function finish(request, job, mode) {
  const response = await request.post(api+'/__test/finish',{data:{request_id:job.request_id,mode}});
  expect(response.ok()).toBeTruthy();
}

test('running import survives refresh, then empty completion stays in history', async ({page,context,request},info) => {
  await open(page,context);
  const job = await start(page);
  await expect(page.locator('.import-progress')).toContainText('正在识别');
  await page.reload();
  await expect(page.locator('.import-progress')).toContainText('正在识别');
  await expect(page.getByRole('button',{name:'正在识别…',exact:true})).toBeDisabled();
  await page.screenshot({path:info.outputPath('text-import-running.png'),fullPage:true});
  await finish(request,job,'empty');
  await expect(page.locator('.import-progress')).toHaveCount(0);
  const history = page.locator('[data-source-sync-history]');
  await history.evaluate(element => { element.open=true; });
  await expect(page.locator(`[data-import-history-batch-entry="${job.request_id}"]`)).toContainText('未识别出任务');
  await page.reload();
  await page.locator('[data-source-sync-history]').evaluate(element => { element.open=true; });
  await expect(page.locator(`[data-import-history-batch-entry="${job.request_id}"]`)).toContainText('未识别出任务');
});

test('stop is durable and late success cannot create a candidate', async ({page,context,request}) => {
  await open(page,context); const job=await start(page);
  await page.locator('.import-progress').getByRole('button',{name:'停止导入'}).click();
  await expect(page.locator('.import-progress')).toHaveCount(0);
  await finish(request,job,'success');
  await page.reload();
  await page.locator('[data-source-sync-history]').evaluate(element => { element.open=true; });
  await expect(page.locator(`[data-import-history-batch-entry="${job.request_id}"]`)).toContainText('已停止');
  await expect(page.locator('.candidate-row')).toHaveCount(0);
});

test('failure retry keeps one history record and yields one reviewable candidate', async ({page,context,request},info) => {
  await open(page,context); const job=await start(page);
  await finish(request,job,'failure');
  await expect(page.locator('.import-progress')).toHaveCount(0);
  await page.locator('[data-source-sync-history]').evaluate(element => { element.open=true; });
  const row=page.locator(`[data-import-history-batch-entry="${job.request_id}"]`);
  await expect(row).toContainText('识别失败');
  await row.getByRole('button',{name:'重试识别'}).click();
  await expect(page.locator('.import-progress')).toContainText('正在识别');
  await page.reload();
  await expect(page.locator('.import-progress')).toContainText('正在识别');
  await finish(request,job,'success');
  await expect(page.locator('.candidate-row')).toHaveCount(1);
  await expect(page.locator('.candidate-row')).toContainText('提交需求报告');
  await page.locator('[data-source-sync-history]').evaluate(element => { element.open=true; });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText('识别完成');
  await page.screenshot({path:info.outputPath('text-import-completed.png'),fullPage:true});
});
