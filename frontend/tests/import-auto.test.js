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
const root = '/api/sources/sync-runs', version = 'source-sync/0.1';
const clone = value => structuredClone(value);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const check = (name, fn) => test(name, {timeout: 3000}, fn);

function teaching(name = 'date-only', suffix = '') {
  const entry = clone(cases.find(entry => entry.name === name));
  assert.ok(entry, 'use a synthetic saved teaching fixture');
  entry.run.request_id += suffix;
  entry.request.request_id = entry.result.request_id = entry.run.request_id;
  return entry;
}

function fixture({entries = [teaching(), teaching('no-deadline')], manualRequests = [], failedRequests = [], startState = 'succeeded', demo = false} = {}) {
  let account = 'A', state = {tasks: [], trash: []}, workspaceFailures = 0, startFailures = 0;
  const calls = [], pauses = [], messages = [], manual = new Set(manualRequests), failed = new Set(failedRequests);
  const accounts = Object.fromEntries(['A','B'].map(who => [who, {batches: [], tasks: [], runs: [], version: 0, operations: new Map()}]));
  const response = value => ({ok: true, json: async () => clone(value)});
  const rejection = (status, error) => ({ok: false, status, json: async () => ({error})});
  const abortError = () => Object.assign(new Error('aborted'), {name: 'AbortError'});
  const starts = () => calls.filter(call => call.path === root && call.method === 'POST');
  const batchFor = (who, id) => accounts[who].batches.find(batch => batch.request.request_id === id);

  function seed(entry, who = 'A', applied = false) {
    const previous = batchFor(who, entry.request.request_id);
    if (previous) return previous;
    const db = accounts[who];
    const batch = {
      request: clone(entry.request), result: clone(entry.result),
      items: entry.result.tasks.map((candidate, index) => ({id: `${who}:${entry.run.request_id}:storage:${index}`, state: applied ? 'applied' : 'pending', version: applied ? 2 : 1, candidate: clone(candidate)})),
    };
    db.batches.push(batch);
    if (applied) for (const item of batch.items) {
      const candidate = item.candidate;
      db.tasks.push({id: `task:${item.id}`, title: candidate.title, description: candidate.description, course: candidate.course_or_project, due: candidate.deadline.status === 'not_provided' ? null : {date: candidate.deadline.value, precision: 'date'}, status: 'not_started', source: '教学网', subtasks: [], plans: [], recognitionHistory: [{storageId: item.id, requestId: batch.request.request_id, candidateId: candidate.candidate_id, action: 'create'}]});
      db.version++;
    }
    return batch;
  }

  function recount(run) {
    run.counts = Object.fromEntries(['queued','running','succeeded','failed','incomplete','cancelled','interrupted'].map(name => [name, run.records.filter(record => record.state === name).length]));
    Object.assign(run.counts, {total: run.records.length, skipped: run.skipped, added: run.records.reduce((sum, record) => sum + record.added, 0)});
  }

  function complete(run, who = 'A') {
    for (const record of run.records) {
      const entry = entries.find(entry => entry.run.request_id === record.request_id);
      if (failed.has(record.request_id)) {
        record.state = 'failed';record.added = 0;
        record.error = {code: 'agent_timeout', message: '识别服务超时', retryable: true};
        if (!batchFor(who, record.request_id)) {
          const batch = seed(entry, who);
          batch.items = [];batch.result = {...batch.result, status: 'failed', tasks: [], errors: [record.error]};
        }
      } else {
        const existing = batchFor(who, record.request_id);
        if (existing?.result.status === 'failed') accounts[who].batches.splice(accounts[who].batches.indexOf(existing), 1);
        const batch = seed(entry, who, !manual.has(record.request_id));
        record.state = 'succeeded';record.error = null;record.added = existing && existing.result.status !== 'failed' ? 0 : batch.items.filter(item => item.state === 'applied').length;
      }
    }
    run.state = run.records.some(record => record.state === 'failed') ? 'partial_success' : 'succeeded';
    run.revision++;recount(run);
    return run;
  }

  function makeRun(who, operationId, currentEntries = entries, parent = null, status = startState) {
    const run = {
      contract_version: version, run_id: `${who}:sync:${accounts[who].runs.length}`, operation_id: operationId,
      sources: ['teaching_web'], trigger: 'manual', parent_run_id: parent, state: status, revision: 1,
      created_at: '2026-10-14T12:00:00Z', updated_at: '2026-10-14T12:00:01Z', skipped: 0, error: null,
      records: currentEntries.map(entry => ({request_id: entry.run.request_id, label: `${who} 合成通知`, course: '合成课程', state: 'queued', added: 0, seconds: null, error: null})),
    };
    recount(run);accounts[who].runs.unshift(run);
    if (status === 'succeeded') complete(run, who);
    return run;
  }

  async function pause(phase, call) {
    const index = pauses.findIndex(gate => gate.phase === phase);
    if (index < 0) return;
    const gate = pauses.splice(index, 1)[0];gate.enter(call);
    if (gate.ignoreAbort) {await gate.wait;return;}
    if (call.signal.aborted) throw abortError();
    let abort;
    try {
      await Promise.race([gate.wait, new Promise((_, reject) => {
        abort = () => reject(abortError());call.signal.addEventListener('abort', abort, {once: true});
      })]);
    } finally {call.signal.removeEventListener('abort', abort);}
  }

  globalThis.fetch = async (path, options = {}) => {
    const who = account, db = accounts[who], method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : undefined;
    const call = {path, method, body, account: who, signal: options.signal};calls.push(call);
    if (path === '/api/auth/login') {account = body.email.split('@')[0];return response({});}
    if (path === '/api/auth/me') return response({user: {id: who, name: who, email: `${who}@test.local`}});
    if (path === '/api/auth/logout') return response({});
    if (path === '/api/workspace') {
      await pause('workspace', call);
      if (workspaceFailures && db.tasks.length) {workspaceFailures--;return rejection(503, '无法连接服务，请检查后端');}
      return response({tasks: {version: db.version, value: db.tasks}});
    }
    if (path === '/api/data/tasks') {
      await pause('save', call);
      if (body.version !== db.version) return rejection(409, '任务已变化');
      db.tasks = clone(body.value);db.version++;return response({version: db.version});
    }
    if (path === '/api/sources/sync-schedule') return response({contract_version:'source-sync/0.1', enabled:false, available:true, interval_minutes:180, revision:0});
    if (path === '/api/sources/credentials') {
      await pause('credentials', call);
      return response({configured: true, username: `${who}-campus`});
    }
    if (path === root && method === 'GET') {
      const snapshot = {contract_version: version, runs: clone(db.runs)};
      await pause('runs', call);return response(snapshot);
    }
    if (path === root && method === 'POST') {
      assert.deepEqual(Object.keys(body).sort(), ['consent','contract_version','operation_id','sources']);
      assert.equal(body.contract_version, version);assert.deepEqual(body.sources, ['teaching_web']);assert.equal(body.consent, true);assert.ok(body.operation_id);
      await pause('start', call);
      if (!db.operations.has(body.operation_id)) db.operations.set(body.operation_id, makeRun(who, body.operation_id));
      if (startFailures) {startFailures--;throw new TypeError('connection lost after the backend accepted this operation');}
      return response(db.operations.get(body.operation_id));
    }
    if (path.startsWith(root + '/') && method === 'POST') {
      const [,id,action] = path.slice(root.length).split('/');
      const run = db.runs.find(run => run.run_id === decodeURIComponent(id));assert.ok(run, 'run belongs to the current account');
      if (action === 'stop') {
        assert.deepEqual(body, {contract_version: version});
        run.state = 'stopped';run.revision++;run.records.filter(record => ['queued','running'].includes(record.state)).forEach(record => record.state = 'cancelled');recount(run);return response(run);
      }
      assert.equal(action, 'retry');assert.equal(body.contract_version, version);assert.equal(body.consent, true);assert.ok(body.operation_id);assert.ok(body.request_ids.length);
      const selected = entries.filter(entry => body.request_ids.includes(entry.run.request_id));
      for (const entry of selected) failed.delete(entry.run.request_id);
      return response(makeRun(who, body.operation_id, selected, run.run_id, 'succeeded'));
    }
    if (path === '/api/recognition/batches' && method === 'GET') return response(db.batches);
    if (path === '/api/recognition/batches' && method === 'POST') {
      assert.equal(body.contract_version, '0.2');assert.equal(body.records[0].platform, 'manual');
      const entry = teaching();entry.run.request_id = body.request_id;entry.request = clone(body);entry.result.request_id = body.request_id;
      entry.result.record_results[0].input_id = body.records[0].input_id;entry.result.tasks[0].input_id = body.records[0].input_id;
      entry.result.tasks[0].title = `${who} 文本任务`;entry.result.tasks[0].sources.forEach(source => source.platform = 'manual');
      seed(entry, who);return response(db.batches);
    }
    throw new Error(`unexpected request ${method} ${path}`);
  };

  const host = {
    escapeHtml, icon: () => '', render() {}, openDialog() {}, closeDialog() {},
    toast: message => messages.push(message), getState: () => state, isDemo: () => demo,
    refresh: async () => {state = await store.refreshStore();},
  };
  return {
    host, calls, accounts, seed, batchFor, starts, messages,
    get state() {return state;},
    async login(who = 'A') {state = await store.authenticate('login', {email: `${who}@test.local`, password: 'test-password'});},
    restoreActive(who = 'A') {return makeRun(who, 'restored-operation', entries, null, 'recognizing');},
    finish(who = 'A') {return complete(accounts[who].runs[0], who);},
    failNextWorkspaceRead() {workspaceFailures++;},
    failNextStartResponse() {startFailures++;},
    pauseNext(phase, ignoreAbort = false) {
      let enter, release;const entered = new Promise(resolve => enter = resolve), wait = new Promise(resolve => release = resolve);
      pauses.push({phase, ignoreAbort, enter, wait});return {entered, release};
    },
  };
}

function makeUI(f, t) {
  const ui = createImportUI(f.host);t.after(() => ui.reset());return ui;
}
async function ready(f, t) {
  await f.login();const ui = makeUI(f, t);await ui.load();await ui.loadSourceSync();await ui.click({dataset: {importSource: 'teaching'}});await new Promise(r=>setImmediate(r));return ui;
}
const sync = async ui => {return ui.click({dataset: {importAction: 'sync'}});};
const noClientRecognition = f => {
  assert.equal(f.calls.some(call => call.path === '/api/sources/sync' || call.path.endsWith('/resolve') || call.path.includes('/planning/')), false);
  assert.equal(f.calls.some(call => call.method === 'POST' && ['/api/recognition/batches','/api/recognition/retry'].includes(call.path) && call.body.contract_version !== '0.2'), false);
};
function textForm(text) {
  return {dataset: {importForm: 'text'}, values: {text, published: '2026-10-14T12:00:00'}, querySelector() {return null;}, querySelectorAll() {return [];}};
}

check('returning to import unlocks controls for either read order with empty or completed progress', async t => {
  for (const withHistory of [false, true]) for (const progressLast of [true, false]) {
    const f = fixture();
    let onRender = () => {};
    f.host.render = () => onRender();
    const ui = await ready(f, t);
    if (withHistory) await sync(ui);
    let visible = false, html = '';
    onRender = () => {
      ui.setVisible(visible);
      if (visible) html = ui.render();
    };
    f.host.render();
    const gate = f.pauseNext(progressLast ? 'runs' : 'credentials'); t.after(gate.release);
    visible = true; f.host.render();
    const loading = ui.load();
    await gate.entered;
    if (progressLast) await loading;
    else await new Promise(resolve => setImmediate(resolve));
    for (const control of ['data-import-source="text"', 'data-import-source="teaching"'])
      assert.doesNotMatch(html, new RegExp(control + '[^>]*disabled'), control + ' remains navigable during loading');
    assert.match(html,/data-import-action="sync"[^>]*disabled/);
    if (!progressLast) assert.match(html,/data-import-action="refresh"[^>]*disabled/);
    const reads = f.calls.filter(call => call.path === '/api/workspace').length;
    gate.release(); await loading; await new Promise(resolve => setImmediate(resolve));
    for (const control of ['data-import-source="text"', 'data-import-source="teaching"', 'data-import-action="sync"', 'data-import-action="refresh"'])
      assert.doesNotMatch(html, new RegExp(control + '[^>]*disabled'), control + ' unlocks after unchanged progress');
    assert.equal(f.calls.filter(call => call.path === '/api/workspace').length, reads, 'unchanged progress does not reload the workspace');
    assert.equal(f.starts().length, Number(withHistory), 'returning never starts another import');
    ui.setVisible(false);
  }
});

check('teaching start sends consent and operation identity while backend-applied tasks retain source evidence', async t => {
  const f = fixture(), ui = await ready(f, t);await sync(ui);
  assert.equal(f.starts().length, 1);assert.equal(f.state.tasks.length, 2);
  for (const task of f.state.tasks) assert.ok(task.recognitionHistory[0].storageId);
  assert.ok(f.accounts.A.batches.every(batch => batch.items.every(item => item.state === 'applied')));
  assert.match(ui.render(), /导入完成/);assert.doesNotMatch(ui.render(), /data-import-review=/);
  noClientRecognition(f);
});

check('manual and already-applied results use the backend result without browser eligibility decisions', async t => {
  const entry = teaching('unknown-year'), f = fixture({entries: [entry, teaching()], manualRequests: [entry.run.request_id]}), ui = await ready(f, t);await sync(ui);
  assert.equal(f.state.tasks.length, 1);assert.equal(f.batchFor('A', entry.run.request_id).items[0].state, 'pending');
  assert.match(ui.render(), /data-import-review=/);noClientRecognition(f);
});

check('plain-text recognition and normal history loading do not create unrelated teaching candidates', async t => {
  const f = fixture(), saved = f.seed(teaching());await f.login();const ui = makeUI(f, t);await ui.load();
  await ui.submit(textForm('这是一条合成文本通知'));
  assert.equal(f.starts().length, 0);assert.equal(saved.items[0].state, 'pending');assert.equal(f.accounts.A.tasks.length, 0);
  assert.equal(f.calls.filter(call => call.path === '/api/recognition/batches' && call.method === 'POST').length, 1);
  noClientRecognition(f);
});

check('demo imports and source-run recovery make no server requests', async t => {
  const f = fixture({demo: true});await f.login();const ui = makeUI(f, t), before = f.calls.length;
  await ui.load();await ui.loadSourceSync();await sync(ui);await ui.submit(textForm('合成示例'));
  assert.equal(f.calls.length, before);assert.equal(f.accounts.A.tasks.length, 0);
});

check('restoring an active run blocks duplicate teaching starts but permits independent text recognition', async t => {
  const f = fixture(), run = f.restoreActive(), ui = await ready(f, t);
  await sync(ui);await ui.submit(textForm('运行中的通知'));assert.equal(f.starts().length, 0);
  assert.equal(f.calls.filter(call => call.method === 'POST' && call.path === '/api/recognition/batches').length, 1);
  f.finish();await ui.loadSourceSync();assert.equal(f.state.tasks.length, 2);assert.equal(f.accounts.A.runs.length, 1);assert.equal(f.accounts.A.runs[0].run_id, run.run_id);
  noClientRecognition(f);
});

check('reading the same terminal run does not rewrite tasks or repeat completion refresh', async t => {
  const f = fixture(), ui = await ready(f, t);await sync(ui);
  const reads = f.calls.filter(call => call.path === '/api/workspace').length;
  await ui.loadSourceSync();await ui.loadSourceSync();
  assert.equal(f.starts().length, 1);assert.equal(f.accounts.A.tasks.length, 2);
  assert.equal(f.calls.filter(call => call.path === '/api/workspace').length, reads);noClientRecognition(f);
});

check('starting a background run waits for pending local edits and preserves the saved task', async t => {
  const f = fixture(), ui = await ready(f, t), gate = f.pauseNext('save');t.after(gate.release);
  store.saveStore({tasks: [{id: 'local-task', title: '本地编辑', notes: '已编辑的说明', course: '', due: '', dueTime: '', status: 'todo'}]});
  await gate.entered;const running = sync(ui);await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.starts().length, 0);gate.release();await running;
  assert.equal(f.state.tasks.length, 3);assert.equal(f.state.tasks[0].notes, '已编辑的说明');noClientRecognition(f);
});

check('a task save conflict prevents background start until the workspace is explicitly recovered', async t => {
  const f = fixture(), ui = await ready(f, t);f.accounts.A.version++;
  t.after(() => store.reloadStore());store.saveStore({tasks: [{id: 'unsaved', title: '未保存编辑', status: 'todo'}]});
  await assert.rejects(store.flushStore(), /任务已变化/);await sync(ui);
  assert.equal(f.starts().length, 0);assert.equal(f.accounts.A.tasks.length, 0);assert.match(ui.render(), /class="error" role="alert"/);
});

check('retrying a failed background record sends only stored request identity and refreshes backend-applied tasks', async t => {
  const entry = teaching(), f = fixture({failedRequests: [entry.run.request_id]}), ui = await ready(f, t);await sync(ui);
  const failedRun = f.accounts.A.runs[0];assert.equal(f.state.tasks.length, 1);
  assert.doesNotMatch(ui.render(), new RegExp(`data-import-retry="${entry.run.request_id}"`));
  assert.doesNotMatch(ui.render(), /import-failure|历史导入 · 识别未完成/);
  assert.doesNotMatch(ui.render().split('class="import-results"')[1], /已处理 ·|class="error"/);
  await ui.click({dataset: {sourceSyncAction: 'retry', sourceSyncRun: failedRun.run_id, sourceSyncRequest: entry.run.request_id}});
  const retry = f.calls.find(call => call.path.endsWith('/retry'));
  assert.deepEqual(retry.body.request_ids, [entry.run.request_id]);assert.equal(Object.hasOwn(retry.body, 'records'), false);
  assert.equal(f.state.tasks.length, 2);assert.equal(f.starts().length, 1);noClientRecognition(f);
});

check('stopping background import sends the backend stop request and preserves previously stored tasks', async t => {
  const f = fixture({startState: 'recognizing'}), ui = await ready(f, t);await sync(ui);
  f.seed(teaching(), 'A', true);await f.host.refresh();
  await sync(ui);
  const stop = f.calls.find(call => call.path.endsWith('/stop'));assert.deepEqual(stop.body, {contract_version: version});
  assert.equal(f.accounts.A.runs[0].state, 'stopped');assert.equal(f.state.tasks.length, 1);assert.match(ui.render(), /已停止/);noClientRecognition(f);
});

check('switching accounts prevents a late background-start response from displaying the previous account data', async t => {
  const f = fixture(), b = f.seed(teaching('date-only', ':B-private'), 'B');b.items[0].candidate.title = 'B 合成私有任务';
  const ui = await ready(f, t), gate = f.pauseNext('start', true);t.after(gate.release);
  const running = sync(ui), active = await gate.entered;await f.login('B');gate.release();await running;await ui.load();await ui.loadSourceSync();
  assert.equal(active.signal.aborted, true);assert.equal(f.accounts.A.tasks.length, 2);assert.equal(f.accounts.B.tasks.length, 0);
  assert.match(ui.render(), /B 合成私有任务/);assert.doesNotMatch(ui.render(), /A-campus|A 合成通知/);noClientRecognition(f);
});

check('an old run-list response cannot restore a previous account run or its import lock', async t => {
  const f = fixture(), ui = await ready(f, t);f.restoreActive();const gate = f.pauseNext('runs', true);t.after(gate.release);
  const loading = ui.loadSourceSync(), active = await gate.entered;await f.login('B');gate.release();await loading;await ui.load();await ui.loadSourceSync();
  assert.equal(active.signal.aborted, true);assert.doesNotMatch(ui.render(), /A 合成通知|A-campus/);
  await ui.click({dataset: {importSource: 'teaching'}});await new Promise(resolve => setImmediate(resolve));await sync(ui);assert.equal(f.starts().at(-1).account, 'B');noClientRecognition(f);
});

check('terminal task refresh blocks duplicate sync but does not block independent text recognition', async t => {
  const f = fixture({startState: 'recognizing'}), ui = await ready(f, t);await sync(ui);f.finish();
  const gate = f.pauseNext('workspace');t.after(gate.release);const refreshing = ui.loadSourceSync();await gate.entered;
  const before = f.calls.length;await sync(ui);await ui.submit(textForm('终态刷新期间的通知'));
  assert.equal(f.starts().length, 1);assert.equal(f.calls.length, before + 1);
  gate.release();await refreshing;assert.equal(f.state.tasks.length, 2);
  await ui.submit(textForm('终态刷新完成后的通知'));assert.equal(f.calls.filter(call => call.method === 'POST' && call.path === '/api/recognition/batches').length, 2);noClientRecognition(f);
});

check('a workspace read failure retains the server-created result and retrying progress never restarts recognition', async t => {
  const f = fixture(), ui = await ready(f, t);f.failNextWorkspaceRead();await sync(ui);
  assert.equal(f.accounts.A.tasks.length, 2);assert.equal(f.state.tasks.length, 0);assert.equal(f.starts().length, 1);
  assert.match(ui.render(), /刷新|读取/);await ui.loadSourceSync();assert.equal(f.state.tasks.length, 2);assert.equal(f.starts().length, 1);noClientRecognition(f);
});

check('retrying a lost start response retains operation identity and cannot duplicate backend-created tasks', async t => {
  const f = fixture(), ui = await ready(f, t);f.failNextStartResponse();await sync(ui);
  assert.equal(f.accounts.A.tasks.length, 2);assert.equal(f.state.tasks.length, 0);await sync(ui);
  assert.equal(f.starts().length, 2);assert.equal(f.starts()[0].body.operation_id, f.starts()[1].body.operation_id);
  assert.equal(f.accounts.A.runs.length, 1);assert.equal(f.state.tasks.length, 2);noClientRecognition(f);
});
