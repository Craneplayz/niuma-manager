import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setMaxListeners} from 'node:events';

globalThis.window = new EventTarget();
setMaxListeners(0, window);
globalThis.CustomEvent ||= class extends Event {
  constructor(type, options) { super(type); this.detail = options?.detail; }
};
globalThis.FormData = class {
  constructor(form) { this.values = form.values; }
  *[Symbol.iterator]() { yield* Object.entries(this.values); }
};

const store = await import('../src/team-store.js');
const {createImportUI} = await import('../src/import-ui.js');
const cases = JSON.parse(await readFile(new URL('../../contracts/examples/teaching-workspace-v01.json', import.meta.url), 'utf8')).cases;
const clone = value => structuredClone(value);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const check = (name, fn) => test(name, {timeout: 3000}, fn);

function teaching(name = 'date-only', suffix = '', edit = () => {}) {
  const entry = clone(cases.find(entry => entry.name === name));
  assert.ok(entry, 'use a synthetic server fixture');
  entry.run.request_id += suffix;
  entry.request.request_id = entry.result.request_id = entry.run.request_id;
  edit(entry);
  return entry;
}

function fixture({entries = [teaching(), teaching('no-deadline')], rejectAutomatic = [], failAutomaticOnce = [], demo = false} = {}) {
  let account = 'A', state = {tasks: [], trash: []}, workspaceFailures = 0;
  const calls = [], modelCalls = [], messages = [], pauses = [], refusals = new Set(rejectAutomatic), failures = new Set(failAutomaticOnce);
  const accounts = Object.fromEntries(['A','B'].map(who => [who, {batches: [], tasks: [], version: 0, resolutions: new Map()}]));
  const response = value => ({ok: true, json: async () => clone(value)});
  const rejection = (status, error) => ({ok: false, status, json: async () => ({error})});
  const abortError = () => Object.assign(new Error('aborted'), {name: 'AbortError'});
  const automaticCalls = () => calls.filter(call => call.path.endsWith('/resolve') && call.body?.auto_apply === true);
  const batchFor = (who, id) => accounts[who].batches.find(batch => batch.request.request_id === id);
  const snapshots = who => clone(accounts[who].batches);

  function seed(entry, who = 'A') {
    const batch = {
      request: clone(entry.request), result: clone(entry.result), demo: Boolean(entry.demo),
      items: entry.result.tasks.map((candidate, index) => ({id: `${who}:${entry.run.request_id}:storage:${index}`, state: 'pending', version: 1, candidate: clone(candidate)})),
    };
    accounts[who].batches.push(batch);
    return batch;
  }
  async function pause(phase, call) {
    const index = pauses.findIndex(gate => gate.phase === phase && (!gate.requestId || gate.requestId === call.requestId));
    if (index < 0) return;
    const gate = pauses.splice(index, 1)[0];
    gate.enter(call);
    if (gate.ignoreAbort) { await gate.wait; return; }
    if (call.signal.aborted) throw abortError();
    let abort;
    try {
      await Promise.race([gate.wait, new Promise((_, reject) => {
        abort = () => reject(abortError());
        call.signal.addEventListener('abort', abort, {once: true});
      })]);
    } finally { call.signal.removeEventListener('abort', abort); }
  }
  function stillPending(batch) {
    // The mock server advertises saved, unapplied work without rerunning extraction.
    return !batch.demo && !refusals.has(batch.request.request_id) && batch.items.some(item => item.state === 'pending' && item.version === 1 && item.candidate.notice_type === 'create' && item.candidate.needs_review === false && !item.candidate.review_reasons.length);
  }
  globalThis.fetch = async (path, options = {}) => {
    const who = account, db = accounts[who], method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : undefined;
    const call = {path, method, body, account: who, signal: options.signal};
    calls.push(call);
    if (path === '/api/auth/login') { account = body.email.split('@')[0]; return response({}); }
    if (path === '/api/auth/me') return response({user: {id: who, name: who, email: `${who}@test.local`}});
    if (path === '/api/auth/logout') return response({});
    if (path === '/api/workspace') {
      if (workspaceFailures && db.tasks.length) { workspaceFailures--; return rejection(503, '无法连接服务，请检查后端'); }
      return response({tasks: {version: db.version, value: db.tasks}});
    }
    if (path === '/api/data/tasks') {
      await pause('save', call);
      if (body.version !== db.version) return rejection(409, '任务已变化');
      db.tasks = clone(body.value); db.version++;
      return response({version: db.version});
    }
    if (path === '/api/sources/credentials') return response({configured: true, username: `${who}-campus`});
    if (path === '/api/sources/sync') {
      assert.deepEqual(body, {sources: ['teaching_web']});
      const pending = entries.filter(entry => {
        const batch = batchFor(who, entry.run.request_id);
        return !batch || ['failed','partial_success'].includes(batch.result.status) || stillPending(batch);
      });
      return response({requests: pending.map(entry => entry.request), summary: {total: entries.length, skipped: entries.length - pending.length, pending: pending.length}});
    }
    if (path === '/api/recognition/batches' && method === 'GET') return response(snapshots(who));
    if ((path === '/api/recognition/batches' || path === '/api/recognition/retry') && method === 'POST') {
      call.requestId = body.request_id;
      await pause('recognize', call);
      if (body.contract_version === 'teaching-run/0.1') {
        const entry = entries.find(entry => entry.run.request_id === body.request_id);
        assert.ok(entry, 'run identity must belong to a captured server fixture');
        assert.deepEqual(body, entry.run, 'the browser must not submit the teaching snapshot');
        const previous = batchFor(who, body.request_id);
        if (!previous || ['failed','partial_success'].includes(previous.result.status)) {
          modelCalls.push({account: who, requestId: body.request_id});
          if (previous) db.batches.splice(db.batches.indexOf(previous), 1);
          seed(entry, who);
        }
      } else {
        assert.equal(body.contract_version, '0.2');
        assert.equal(body.records[0].platform, 'manual');
        const entry = teaching();
        entry.run.request_id = body.request_id;
        entry.request = clone(body); entry.result.request_id = body.request_id;
        entry.result.record_results[0].input_id = body.records[0].input_id;
        entry.result.tasks[0].input_id = body.records[0].input_id;
        entry.result.tasks[0].title = `${who} 文本任务`;
        entry.result.tasks[0].sources.forEach(source => source.platform = 'manual');
        seed(entry, who);
      }
      return response(snapshots(who));
    }
    if (path.endsWith('/resolve')) {
      const id = decodeURIComponent(path.split('/').at(-2));
      const batch = db.batches.find(batch => batch.items.some(item => item.id === id));
      const item = batch?.items.find(item => item.id === id);
      assert.ok(item, 'resolve stays in the authenticated account');
      call.requestId = batch.request.request_id;
      call.candidateId = id;
      await pause('resolve', call);
      const previous = db.resolutions.get(id);
      if (item.state !== 'pending' && body.action !== 'reopen') {
        return previous && JSON.stringify(previous) === JSON.stringify(body) ? response({batches: snapshots(who)}) : rejection(409, '候选已处理');
      }
      if (body.version !== item.version) return rejection(409, '候选已变化，请刷新');
      if (body.action === 'ignore') { item.state = 'ignored'; item.version++; db.resolutions.set(id, clone(body)); return response({batches: snapshots(who)}); }
      if (body.action === 'reopen') {
        assert.equal(item.state, 'ignored');
        item.state = 'pending'; item.version++; db.resolutions.delete(id);
        return response({batches: snapshots(who)});
      }
      assert.deepEqual(body, {action: 'create', auto_apply: true, version: item.version});
      assert.ok(call.signal instanceof AbortSignal, 'automatic resolve participates in cancellation');
      if (refusals.has(batch.request.request_id)) return rejection(400, '此候选需要人工处理，不能自动加入日程');
      if (failures.delete(batch.request.request_id)) return rejection(503, '无法连接服务，请检查后端');
      const candidate = item.candidate;
      db.tasks.push({id: `task:${id}`, title: candidate.title, description: candidate.description, course: candidate.course_or_project, due: candidate.deadline.status === 'not_provided' ? null : {date: candidate.deadline.value, precision: 'date'}, status: 'not_started', source: '教学网', subtasks: [], plans: [], recognitionHistory: [{storageId: id, requestId: batch.request.request_id, candidateId: candidate.candidate_id, action: 'create'}]});
      db.version++; item.state = 'applied'; item.version++; db.resolutions.set(id, clone(body));
      return response({batches: snapshots(who)});
    }
    throw new Error(`unexpected request ${method} ${path}`);
  };
  const host = {
    escapeHtml, icon: () => '', render() {}, openDialog() {}, closeDialog() {},
    toast: message => messages.push(message), getState: () => state, isDemo: () => demo,
    refresh: async () => { state = await store.refreshStore(); },
  };
  return {
    host, calls, modelCalls, accounts, seed, batchFor, automaticCalls,
    get state() { return state; },
    async login(who = 'A') { state = await store.authenticate('login', {email: `${who}@test.local`, password: 'test-password'}); },
    failNextWorkspaceRead() { workspaceFailures++; },
    pauseNext(phase, requestId = '', ignoreAbort = false) {
      let enter, release;
      const entered = new Promise(resolve => enter = resolve), wait = new Promise(resolve => release = resolve);
      pauses.push({phase, requestId, ignoreAbort, enter, wait});
      return {entered, release};
    },
  };
}

async function ready(f) {
  await f.login();
  const ui = createImportUI(f.host);
  await ui.load();
  await ui.click({dataset: {importSource: 'teaching'}});
  return ui;
}
const sync = ui => ui.click({dataset: {importAction: 'sync'}});
const noPlanning = f => assert.equal(f.calls.some(call => call.path.includes('/planning/')), false);

check('clear dated and undated teaching candidates use server automatic creation and preserve evidence', async () => {
  const f = fixture(), ui = await ready(f);
  await sync(ui);
  assert.deepEqual(f.automaticCalls().map(call => call.body), [1,1].map(version => ({action: 'create', auto_apply: true, version})));
  assert.deepEqual(f.state.tasks.map(task => [task.title, task.due, task.dueTime]), [['完成第二次作业','2026-10-23',''], ['完成第二次作业','','']]);
  assert.equal(f.accounts.A.tasks[1].due, null);
  for (const task of f.accounts.A.tasks) assert.equal(task.recognitionHistory[0].storageId, f.automaticCalls().find(call => `task:${call.candidateId}` === task.id).candidateId);
  assert.match(ui.render(), /已自动加入 2 项任务/);
  noPlanning(f);
});

check('review flags, review reasons, demo batches and changes remain manual', async () => {
  const entries = [
    teaching('date-only', ':review', entry => entry.result.tasks[0].needs_review = true),
    teaching('date-only', ':reason', entry => entry.result.tasks[0].review_reasons = ['需核对原文']),
    teaching('date-only', ':demo', entry => entry.demo = true),
    teaching('date-only', ':change', entry => entry.result.tasks[0].notice_type = 'update'),
    teaching('date-only', ':email', entry => entry.request.records[0].platform = 'email'),
  ];
  const f = fixture({entries}), ui = await ready(f);
  await sync(ui);
  assert.equal(f.automaticCalls().length, 0);
  assert.equal(f.accounts.A.tasks.length, 0);
  assert.ok(f.accounts.A.batches.every(batch => batch.items.every(item => item.state === 'pending')));
  assert.match(ui.render(), /5 项待核对/);
  noPlanning(f);
});

check('text recognition and normal loading cannot auto-apply unrelated teaching history', async () => {
  const f = fixture(), history = f.seed(teaching('date-only', ':history'));
  await f.login();
  const ui = createImportUI(f.host); await ui.load();
  assert.equal(f.automaticCalls().length, 0);
  await ui.submit({dataset: {importForm: 'text'}, values: {text: '合成文本通知', published: '2026-10-14T12:00:00'}});
  assert.equal(f.automaticCalls().length, 0);
  assert.equal(history.items[0].state, 'pending');
  assert.equal(f.accounts.A.tasks.length, 0);
  assert.match(ui.render(), /A 文本任务/);
  noPlanning(f);
});

check('demo imports make no server requests', async () => {
  const f = fixture({demo: true}); await f.login();
  const ui = createImportUI(f.host), count = f.calls.length;
  await ui.load(); await sync(ui);
  await ui.submit({dataset: {importForm: 'text'}, values: {text: '示例通知', published: ''}});
  assert.equal(f.calls.length, count);
  assert.equal(f.accounts.A.tasks.length, 0);
});

check('a manually ignored and reopened candidate stays manual on retry', async () => {
  const entry = teaching(), f = fixture({entries: [entry]}), batch = f.seed(entry), ui = await ready(f);
  const id = batch.items[0].id;
  await ui.click({dataset: {importIgnore: id}});
  await ui.click({dataset: {importReopen: id}});
  assert.equal(batch.items[0].version, 3);
  await ui.click({dataset: {importRetry: entry.run.request_id}});
  assert.equal(f.automaticCalls().length, 0);
  assert.equal(batch.items[0].state, 'pending');
  assert.equal(f.modelCalls.length, 0);
  assert.match(ui.render(), new RegExp(`data-import-review="${id}"`));
});

check('sync resumes a saved successful candidate without model rerun or duplicate task', async () => {
  const entry = teaching(), f = fixture({entries: [entry]}), batch = f.seed(entry), id = batch.items[0].id, ui = await ready(f);
  await sync(ui); await sync(ui);
  assert.equal(f.modelCalls.length, 0);
  assert.equal(f.automaticCalls().length, 1);
  assert.equal(f.accounts.A.tasks.length, 1);
  assert.equal(f.accounts.A.tasks[0].recognitionHistory[0].storageId, id);
  assert.equal(batch.items[0].state, 'applied');
  assert.equal(batch.items[0].version, 2);
  noPlanning(f);
});

check('automatic creation waits for pending local edits and retains the saved task', async () => {
  const entry = teaching(), f = fixture({entries: [entry]}), ui = await ready(f), gate = f.pauseNext('save');
  store.saveStore({tasks: [{id: 'local-task', title: '本地编辑', notes: '已编辑的说明', course: '', due: '', dueTime: '', status: 'todo'}]});
  await gate.entered;
  const running = sync(ui);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.automaticCalls().length, 0, 'automatic resolve waits for the queued save');
  gate.release(); await running;
  assert.deepEqual(f.state.tasks.map(task => task.id), ['local-task', `task:${f.automaticCalls()[0].candidateId}`]);
  assert.equal(f.state.tasks[0].notes, '已编辑的说明');
  noPlanning(f);
});

check('retry uses a server run identity and automatically applies its newly recovered result', async () => {
  const entry = teaching(), f = fixture({entries: [entry]}), failed = f.seed(entry);
  failed.result = {...failed.result, status: 'failed', tasks: [], errors: [{code: 'agent_timeout', message: 'agent_timeout', retryable: true}]};
  failed.items = [];
  const ui = await ready(f); await ui.click({dataset: {importRetry: entry.run.request_id}});
  assert.deepEqual(f.calls.find(call => call.path === '/api/recognition/retry').body, entry.run);
  assert.equal(f.modelCalls.length, 1);
  assert.equal(f.accounts.A.tasks.length, 1);
  assert.equal(f.automaticCalls().length, 1);
  noPlanning(f);
});

check('one automatic failure does not block later tasks and sync retries only the unapplied work', async () => {
  const entry = teaching(), f = fixture({failAutomaticOnce: [entry.run.request_id]}), ui = await ready(f);
  await sync(ui);
  assert.equal(f.batchFor('A', entry.run.request_id).items[0].state, 'pending');
  assert.equal(f.accounts.A.tasks.length, 1);
  assert.equal(f.state.tasks.length, 1);
  assert.match(ui.render(), /部分.*未完成/);
  await sync(ui);
  assert.equal(f.accounts.A.tasks.length, 2);
  assert.equal(f.modelCalls.length, 2);
  assert.deepEqual(f.automaticCalls().map(call => call.requestId), [entry.run.request_id, 'synthetic:no-deadline', entry.run.request_id]);
  noPlanning(f);
});

check('the backend may reject automatic creation with 400 while other tasks continue', async () => {
  const rejected = teaching('date-only', ':server-refusal', entry => Object.assign(entry.result.tasks[0].deadline, {status: 'unknown', precision: 'unknown', value: null}));
  const f = fixture({entries: [rejected, teaching('no-deadline')], rejectAutomatic: [rejected.run.request_id]}), ui = await ready(f);
  await sync(ui);
  assert.equal(f.automaticCalls().length, 2, 'deadline eligibility belongs to the server');
  assert.equal(f.batchFor('A', rejected.run.request_id).items[0].state, 'pending');
  assert.equal(f.accounts.A.tasks.length, 1);
  assert.match(ui.render(), /1 项待核对/);
  assert.doesNotMatch(ui.render(), /class="error" role="alert"/);
  noPlanning(f);
});

check('stopping an active automatic resolve aborts it and prevents the remaining candidates', async () => {
  const entry = teaching('two-tasks'), f = fixture({entries: [entry]}), ui = await ready(f), gate = f.pauseNext('resolve');
  const running = sync(ui), active = await gate.entered;
  await ui.click({dataset: {importAction: 'stop'}}); gate.release(); await running;
  assert.equal(active.signal.aborted, true);
  assert.equal(f.automaticCalls().length, 1);
  assert.equal(f.accounts.A.tasks.length, 0);
  assert.ok(f.accounts.A.batches[0].items.every(item => item.state === 'pending'));
  assert.match(ui.render(), /已停止导入/);
  await sync(ui);
  assert.equal(f.accounts.A.tasks.length, 2);
  assert.equal(f.modelCalls.length, 1, 'resume uses the saved recognition result');
});

check('a recognition response arriving after stop cannot start automatic writes', async () => {
  const f = fixture(), ui = await ready(f), gate = f.pauseNext('recognize', '', true);
  const running = sync(ui), active = await gate.entered;
  await ui.click({dataset: {importAction: 'stop'}}); gate.release(); await running;
  assert.equal(active.signal.aborted, true);
  assert.equal(f.automaticCalls().length, 0);
  assert.equal(f.accounts.A.tasks.length, 0);
  assert.equal(f.modelCalls.length, 1, 'the remote recognition may finish after stopping the wait');
  assert.match(ui.render(), /已停止导入/);
});

check('switching accounts prevents a late response from auto-applying or exposing the previous account', async () => {
  const f = fixture(), b = f.seed(teaching('date-only', ':B-private'), 'B');
  b.items[0].candidate.title = 'B 合成私有任务';
  const ui = await ready(f), gate = f.pauseNext('recognize', '', true), running = sync(ui), active = await gate.entered;
  await f.login('B'); gate.release(); await running; await ui.load();
  assert.equal(active.signal.aborted, true);
  assert.equal(f.automaticCalls().length, 0);
  assert.equal(f.accounts.A.tasks.length, 0);
  assert.equal(f.accounts.B.tasks.length, 0);
  assert.match(ui.render(), /B 合成私有任务/);
  assert.doesNotMatch(ui.render(), /完成第二次作业|A-campus/);
});

check('switching accounts aborts an active automatic resolve and submits no remaining candidates', async () => {
  const f = fixture({entries: [teaching('two-tasks')]}), ui = await ready(f), gate = f.pauseNext('resolve');
  const running = sync(ui), active = await gate.entered;
  await f.login('B'); gate.release(); await running; await ui.load();
  assert.equal(active.signal.aborted, true);
  assert.equal(f.automaticCalls().length, 1);
  assert.equal(f.automaticCalls()[0].account, 'A');
  assert.equal(f.accounts.A.tasks.length, 0);
  assert.equal(f.accounts.B.tasks.length, 0);
  assert.doesNotMatch(ui.render(), /data-import-review=/);
  noPlanning(f);
});

check('a workspace refresh failure preserves the applied result and later refresh cannot duplicate it', async () => {
  const entry = teaching(), f = fixture({entries: [entry]}), ui = await ready(f);
  f.failNextWorkspaceRead(); await sync(ui);
  assert.equal(f.accounts.A.tasks.length, 1);
  assert.equal(f.batchFor('A', entry.run.request_id).items[0].state, 'applied');
  assert.equal(f.state.tasks.length, 0, 'the failed read must not pretend the task list was refreshed');
  assert.match(ui.render(), /任务已加入.*刷新失败/);
  assert.doesNotMatch(ui.render(), /data-import-review=/);
  await ui.click({dataset: {importAction: 'refresh'}}); await sync(ui);
  assert.equal(f.accounts.A.tasks.length, 1);
  assert.equal(f.automaticCalls().length, 1);
  assert.equal(f.modelCalls.length, 1);
  noPlanning(f);
});
