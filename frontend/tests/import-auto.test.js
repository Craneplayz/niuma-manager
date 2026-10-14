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
    if (path === '/api/sources/sync-runs') return response({contract_version:'source-sync/0.1',runs:[]});
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

