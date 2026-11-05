import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createTextImportJobs, checkTextJobs } from '../src/text-import-jobs.js';

const examples = JSON.parse(readFileSync(new URL('../../contracts/examples/text-import-jobs-v01.json', import.meta.url)));
const example = state => structuredClone(examples.find(value => value.jobs[0].state === state));
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture(value = example('recognizing')) {
  let account = 1, response = value, updates = 0, renders = 0, timers = [], fail = false;
  let receive = null, refreshResult = true;
  const calls = [];
  const host = {
    accountVersion: () => account, isAuthenticated: () => true, isDemo: () => false,
    escapeHtml: value => String(value).replaceAll('"', '&quot;'), render: () => renders++,
    onChanged: async () => { updates++; if (refreshResult instanceof Error) throw refreshResult; return refreshResult; },
    setTimer: (fn,ms) => { const timer = {ms,fn: () => { timers = timers.filter(value => value !== timer); return fn(); }}; timers.push(timer); return timer; },
    clearTimer: timer => { timers = timers.filter(value => value !== timer); },
    api: async (path,body,method,options) => {
      calls.push({path,body,method,signal:options.signal});
      if (receive) return receive;
      if (fail) throw new Error('网络连接中断');
      return structuredClone(response);
    },
  };
  return { host,calls,
    response: value => { response = value; }, fail: value => { fail = value; },
    defer: promise => { receive = promise; }, account: () => account++,
    refreshResult: value => { refreshResult = value; }, updates: () => updates,
    timers: () => timers, renders: () => renders,
  };
}

test('shared contract fixtures cover every state and malformed receipts are rejected', () => {
  for (const value of examples) assert.equal(checkTextJobs(value).length,1);
  assert.throws(() => checkTextJobs({jobs: []}));
  assert.throws(() => checkTextJobs({...example('queued'),jobs:[{...example('queued').jobs[0],attempt:0}]}));
  const duplicate = example('queued'); duplicate.jobs.push(duplicate.jobs[0]);
  assert.throws(() => checkTextJobs(duplicate));
});

test('recreated page restores running state, polls completion and stops polling when hidden', async () => {
  const f = fixture(), first = createTextImportJobs(f.host);
  await first.load(); assert.match(first.renderProgress(),/正在识别通知/);
  first.reset();
  const page = createTextImportJobs(f.host);
  page.setVisible(true); await tick();
  assert.match(page.renderProgress(),/正在识别通知/);
  assert.equal(f.timers().at(-1).ms,2000);
  f.response(example('no_tasks'));
  await f.timers().at(-1).fn(); await tick();
  assert.equal(page.list()[0].state,'no_tasks');
  assert.equal(page.isActive(),false);
  assert.equal(f.timers().at(-1).ms,30000);
  page.setVisible(false); assert.equal(f.timers().length,0);
});

test('a completion arriving while another edit is busy keeps retrying result refresh',async () => {
  const f = fixture(example('success')), page = createTextImportJobs(f.host);
  f.refreshResult(null); await page.load();
  f.refreshResult(true); await page.load();
  assert.equal(f.updates(),2);
});

test('a terminal retry receipt keeps refreshing unchanged state after a failed result read', async () => {
  for (const failure of [new Error('网络连接中断'), false, null]) {
    const f = fixture(example('failed')), page = createTextImportJobs(f.host);
    await page.load();
    f.response(example('success')); f.refreshResult(failure);
    await page.click({dataset:{textJobAction:'retry',textJobId:'synthetic:failed',textJobAttempt:'1'}});
    const first = f.updates();
    for (let i=0; i<3; i++) await page.load();
    assert.equal(f.updates(),first+3,'unchanged terminal state must not acknowledge a failed result refresh');
    assert.match(page.renderProgress(), failure === null ? /正在更新导入结果/ : /重新读取导入状态/);
    f.refreshResult(true); await page.load();
    const done = f.updates(); await page.load();
    assert.equal(f.updates(),done);
    assert.doesNotMatch(page.renderProgress(),/正在更新导入结果|重新读取导入状态/);
  }
});

test('an immediate start completion also reconciles results on the next unchanged poll',async () => {
  const f=fixture(example('success')),page=createTextImportJobs(f.host);
  await page.start({request_id:'synthetic:success'});
  await page.load(); assert.equal(f.updates(),1);
});

test('account reset during result refresh cannot show the old attempt failure',async () => {
  const f=fixture(example('failed')),page=createTextImportJobs(f.host);
  await page.load(); f.response(example('success'));
  let reject;
  f.host.onChanged=() => new Promise((_,fail) => { reject=fail; });
  const clicking=page.click({dataset:{textJobAction:'retry',textJobId:'synthetic:failed',textJobAttempt:'1'}});
  await tick(); f.account(); page.reset(); reject(new Error('网络连接中断')); await clicking;
  assert.deepEqual(page.list(),[]); assert.equal(page.renderProgress(),'');
});

test('account switches and late reads cannot restore private jobs', async () => {
  const f = fixture(), page = createTextImportJobs(f.host);
  let resolve; f.defer(new Promise(done => { resolve = done; }));
  const reading = page.load(); f.account(); page.reset();
  assert.equal(f.calls[0].signal.aborted,true);
  resolve(example('recognizing')); await reading;
  assert.deepEqual(page.list(),[]);
});

test('stop uses the persisted attempt and a delayed read cannot undo its receipt',async () => {
  const f = fixture(), page = createTextImportJobs(f.host);
  await page.load();
  let resolve; f.defer(new Promise(done => { resolve = done; }));
  const reading = page.load(); f.defer(null);
  f.response(example('stopped'));
  await page.click({dataset:{textJobAction:'stop',textJobId:'synthetic:recognizing',textJobAttempt:'1'}});
  assert.deepEqual(f.calls.at(-1).body,{attempt:1});
  assert.match(f.calls.at(-1).path,/synthetic%3Arecognizing\/stop$/);
  resolve(example('recognizing')); await reading;
  assert.equal(page.list()[0].state,'stopped');
});
