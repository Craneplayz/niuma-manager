import test from 'node:test';
import assert from 'node:assert/strict';
import {createSourceSyncUI} from '../src/source-sync-ui.js';
import {syncRun} from './sync-fixtures.js';

const API = '/api/sources/sync-runs';
const VERSION = 'source-sync/0.1';
const clone = value => structuredClone(value);
const markup = ui => ui.renderProgress() + ui.renderHistory();
const check = (name, fn) => test(name, {timeout: 3000}, fn);
const button = (action, run, request) => ({dataset: {sourceSyncAction: action, sourceSyncRun: run, sourceSyncRequest: request}});
const category = (ui, run, value) => ui.click({dataset: {sourceSyncAction: 'category', sourceSyncRun: run, sourceSyncCategory: value}});
const problem = (ui, run, value) => ui.click({dataset: {sourceSyncAction: 'problem', sourceSyncRun: run, sourceSyncItem: String(value)}});
function deferred() {
  let resolve, reject;
  const wait = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {wait, resolve, reject};
}

function fixture(initial = []) {
  let account = 1, authenticated = true, demo = false, otherBusy = false;
  let runs = clone(initial), lastCheck = null, renders = 0, refreshes = 0, flushes = 0, handler = null, changed = null, flush = null;
  let serial = 0, timerId = 0, view = {tasks: [], batches: []};
  const calls = [], timers = new Map(), locks = [], callbacks = [], messages = [];
  const remote = {tasks: [], batches: []};
  const defaultAPI = call => {
    if (call.path === '/api/workspace') return {tasks: clone(remote.tasks)};
    if (call.path === '/api/recognition/batches') return clone(remote.batches);
    if (call.path === API && call.method === 'GET') return {contract_version: VERSION, runs: clone(runs), last_check: clone(lastCheck)};
    if (call.path === API && call.method === 'POST') {
      const run = syncRun({id: `run:${++serial}`, state: 'queued', states: []});
      runs = [run, ...runs]; return clone(run);
    }
    const id = decodeURIComponent(call.path.slice(API.length + 1).split('/')[0]);
    const parent = runs.find(run => run.run_id === id);
    assert.ok(parent, 'write targets a known server run');
    if (call.path.endsWith('/stop')) {
      const stopped = clone(parent); stopped.state = 'stopped'; stopped.revision++;
      stopped.records.forEach(record => {
        if (record.state === 'queued') record.state = 'cancelled';
        if (record.state === 'running') record.state = 'interrupted';
      });
      for (const state of ['queued','running','succeeded','failed','incomplete','cancelled','interrupted']) stopped.counts[state] = stopped.records.filter(record => record.state === state).length;
      runs = runs.map(run => run.run_id === id ? stopped : run);
      return clone(stopped);
    }
    if (call.path.endsWith('/retry') || call.path.endsWith('/recover')) {
      const run = syncRun({id: `run:retry:${++serial}`, state: 'queued', states: call.body.request_ids.map(() => 'queued')});
      run.parent_run_id = id;
      run.records.forEach((record, index) => record.request_id = call.body.request_ids[index]);
      runs = [run, ...runs]; return clone(run);
    }
    throw new Error(`unexpected API ${call.path}`);
  };
  let ui;
  const host = {
    accountVersion: () => account, isAuthenticated: () => authenticated, isDemo: () => demo,
    toast: message => messages.push(message),
    isImportBusy: () => otherBusy, render: () => renders++,
    async api(path, body, method = 'GET', options = {}) {
      const call = {path, body: clone(body), method, signal: options.signal, account};
      calls.push(call);
      return handler ? handler(call, defaultAPI) : defaultAPI(call);
    },
    async flushStore() { flushes++; if (flush) await flush(); },
    async onChanged(valid) {
      refreshes++; locks.push(ui.isBusy()); callbacks.push(valid);
      if (changed) return changed(valid);
      const workspace = await host.api('/api/workspace');
      if (!valid()) return false;
      const batches = await host.api('/api/recognition/batches');
      if (!valid()) return false;
      view = {tasks: workspace.tasks, batches};
      return true;
    },
    setTimer(fn, ms) { const id = ++timerId; timers.set(id, {fn, ms}); return id; },
    clearTimer(id) { timers.delete(id); },
  };
  ui = createSourceSyncUI(host);
  return {
    ui, host, calls, remote, timers, locks, callbacks, messages,
    setLastCheck(value) { lastCheck = clone(value); },
    get view() { return view; }, get renders() { return renders; }, get refreshes() { return refreshes; }, get flushes() { return flushes; },
    sourceCalls: () => calls.filter(call => call.path.startsWith(API)),
    posts: () => calls.filter(call => call.method === 'POST'),
    setRuns(value) { runs = clone(value); }, setAPI(value) { handler = value; }, setChanged(value) { changed = value; }, setFlush(value) { flush = value; },
    setDemo(value) { demo = value; }, setAuthenticated(value) { authenticated = value; }, setOtherBusy(value) { otherBusy = value; },
    switchAccount() { account++; ui.reset(); remote.tasks = []; remote.batches = []; },
    async tick() { const [id, timer] = [...timers.entries()][0] || []; assert.ok(timer, 'a poll is scheduled'); timers.delete(id); await timer.fn(); },
  };
}

check('rendering is read-only and hidden, demo or signed-out components make no requests', async () => {
  const f = fixture();
  assert.equal(markup(f.ui), ''); markup(f.ui);
  assert.equal(f.calls.length, 0); assert.equal(f.timers.size, 0);
  f.setDemo(true); await f.ui.load(); await f.ui.start();
  assert.equal(f.calls.length, 0);
  f.setDemo(false); f.setAuthenticated(false); await f.ui.load(); await f.ui.start();
  assert.equal(f.calls.length, 0); assert.equal(markup(f.ui), '');
});

check('only useful completed runs remain in collapsed history; empty stops and skips are hidden', async () => {
  const stopped = syncRun({id: 'run:empty', state: 'stopped', states: []});
  const f = fixture([stopped, syncRun({skipped:52}), syncRun({id:'run:useful',added:1})]); await f.ui.load();
  assert.equal(f.ui.renderProgress(), '');
  assert.match(f.ui.renderHistory(), /历史导入 · 1/);
  assert.doesNotMatch(f.ui.renderHistory(), /data-source-sync-history open|已处理 0 \/ 0|<h2>|跳过|已停止导入/);
});

check('running status moves into the action while only actual results occupy the compact area', async () => {
  const f = fixture(), gate = deferred(); f.setFlush(() => gate.wait);
  const starting = f.ui.start();
  assert.match(f.ui.progressLabel(), /正在启动识别/);
  assert.equal(f.ui.renderProgress(), '');
  gate.resolve(); await starting;
  assert.equal(f.ui.progressLabel(), '等待导入');
  assert.equal(f.ui.renderProgress(), '');
  assert.equal(f.ui.renderHistory(), '');
  f.setRuns([syncRun({id: 'run:1', added: 1})]); await f.ui.load();
  assert.match(f.ui.renderProgress(), /新增 1 项/);
  assert.doesNotMatch(f.ui.renderProgress(), /<progress/);
  await f.ui.setVisible(true);
  assert.equal(f.ui.renderProgress(), '');
  assert.match(f.ui.renderHistory(), /历史导入 · 1/);
});

check('entering restores active progress by GET and leaving pauses polling without stopping the server', async () => {
  const run = syncRun({state: 'recognizing', states: ['succeeded','running','queued'], added: 1}), f = fixture([run]);
  f.remote.tasks = [{id: 'saved-task'}];
  await f.ui.setVisible(true);
  assert.equal(f.ui.isBusy(), true); assert.equal(f.ui.ownsRequest('sync:0'), true);
  assert.match(f.ui.progressLabel(), /正在识别，已处理 1 \/ 3/);
  assert.match(markup(f.ui), /新增 1 项/);
  assert.doesNotMatch(markup(f.ui), /<progress|<span class="status-note">已处理 1 \/ 3|<strong[^>]*>正在识别/); assert.equal(f.view.tasks[0].id, 'saved-task');
  assert.equal([...f.timers.values()][0].ms, 2000);
  await f.tick(); assert.equal(f.posts().length, 0);
  const reads = f.sourceCalls().length;
  f.ui.setVisible(false); assert.equal(f.timers.size, 0); markup(f.ui);
  assert.equal(f.sourceCalls().length, reads); assert.equal(f.ui.isBusy(), true);
  await f.ui.setVisible(true); assert.equal(f.sourceCalls().length, reads + 1);
  assert.equal(f.posts().length, 0);
});

check('queued, crawling and recognizing remain active without creating a separate progress row', async () => {
  for (const [state,label] of [['queued','等待导入'],['crawling','正在采集'],['recognizing','正在识别']]) {
    const f=fixture([syncRun({state,states:state==='recognizing'?['succeeded','running']:[]})]);
    await f.ui.load();
    assert.equal(f.ui.canStop(),true);assert.equal(f.ui.isRunning(),true);
    assert.match(f.ui.progressLabel(),new RegExp(label));
    assert.equal(f.ui.renderProgress(),'');
    if(state==='recognizing') assert.match(f.ui.progressLabel(),/已处理 1 \/ 2/);
    f.ui.reset();assert.equal(f.ui.progressLabel(),'');
  }
});

check('unchanged progress neither refreshes the workspace again nor rerenders editable forms', async () => {
  const f = fixture([syncRun({added: 1})]);
  await f.ui.load(); const renders = f.renders, refreshes = f.refreshes;
  await f.ui.load(); markup(f.ui);
  assert.equal(f.renders, renders); assert.equal(f.refreshes, refreshes);
  assert.equal(f.ui.isBusy(), false); assert.deepEqual(f.locks, [true]);
});

check('an unchanged read redraws controls when its loading state was rendered', async () => {
  const f = fixture(); await f.ui.load();
  const gate = deferred();
  f.setAPI(async (call, normal) => { await gate.wait; return normal(call); });
  const loading = f.ui.load();
  assert.equal(f.ui.isBusy(), true);
  const renders = f.renders;
  gate.resolve(); await loading;
  assert.equal(f.ui.isBusy(), false);
  assert.equal(f.renders, renders + 1);
  assert.equal(f.refreshes, 0);
});

check('start waits for local saves and sends only the durable teaching operation contract', async () => {
  const f = fixture(), save = deferred(); f.setFlush(() => save.wait);
  const starting = f.ui.start();
  assert.equal(f.ui.isBusy(), true); assert.equal(f.posts().length, 0);
  save.resolve(); assert.equal(await starting, true);
  const call = f.posts()[0];
  assert.equal(call.path, API); assert.ok(call.signal instanceof AbortSignal);
  assert.deepEqual(Object.keys(call.body).sort(), ['consent','contract_version','operation_id','sources']);
  assert.equal(call.body.contract_version, VERSION); assert.deepEqual(call.body.sources, ['teaching_web']);
  assert.equal(call.body.consent, true); assert.equal(typeof call.body.operation_id, 'string');
  assert.equal(f.flushes, 1); assert.deepEqual(f.locks, [true]);
  assert.equal(f.calls.some(call => /planning|\/resolve|\/api\/sources\/sync$/.test(call.path)), false);
});

check('a failed local save blocks starting and reports the saving problem accurately', async () => {
  const f = fixture(); f.setFlush(() => { throw new Error('save conflict'); });
  assert.equal(await f.ui.start(), false); assert.equal(f.posts().length, 0);
  assert.equal(f.ui.isBusy(), false); assert.match(markup(f.ui), /任务修改还未保存/);
});

check('a network-uncertain start reuses its operation id without render resubmissions', async () => {
  const f = fixture(); let attempts = 0;
  f.setAPI((call, normal) => {
    if (call.path === API && call.method === 'POST' && ++attempts === 1) throw new Error('offline');
    return normal(call);
  });
  assert.equal(await f.ui.start(), false);
  markup(f.ui); markup(f.ui); assert.equal(f.posts().length, 1);
  assert.equal(await f.ui.start(), true);
  assert.equal(f.posts()[0].body.operation_id, f.posts()[1].body.operation_id);
  assert.equal(f.ui.isBusy(), true);
  await f.ui.start(); assert.equal(f.posts().length, 2, 'an active run cannot start competing work');
});

check('completed server task creation refreshes both tasks and candidates while the completion stays locked', async () => {
  const active = syncRun({state: 'recognizing', states: ['running']}), f = fixture([active]);
  await f.ui.load();
  const finished = syncRun({added: 1}); finished.revision = 2;
  f.remote.tasks = [{id: 'automatically-created'}]; f.remote.batches = [{items: [{state: 'applied'}]}]; f.setRuns([finished]);
  const refresh = deferred(); f.setChanged(async valid => {
    assert.equal(f.ui.isBusy(), true);
    await refresh.wait;
    if (!valid()) return false;
    const workspace = await f.host.api('/api/workspace'), batches = await f.host.api('/api/recognition/batches');
    f.remote.observed = {tasks: workspace.tasks, batches};
    return true;
  });
  const reading = f.ui.load(); await new Promise(resolve => setImmediate(resolve));
  await f.ui.start(); assert.equal(f.posts().length, 0); assert.equal(f.ui.isBusy(), true);
  refresh.resolve(); await reading;
  assert.equal(f.ui.isBusy(), false);
  assert.deepEqual(f.remote.observed, {tasks: [{id: 'automatically-created'}], batches: [{items: [{state: 'applied'}]}]});
  assert.equal(f.calls.some(call => call.path.endsWith('/resolve')), false);
});

check('workspace or candidate refresh failures keep the saved run and retry unchanged progress', async () => {
  for (const failedPath of ['/api/workspace','/api/recognition/batches']) {
    const f = fixture([syncRun({added: 1})]); let fail = true;
    f.remote.tasks = [{id: 'saved'}];
    f.setAPI((call, normal) => { if (call.path === failedPath && fail) { fail = false; throw new Error('temporary read failure'); } return normal(call); });
    assert.equal(await f.ui.load(), false); assert.match(markup(f.ui), /结果已保存.*暂时无法更新页面/);
    assert.match(markup(f.ui), /新增 1 项/); assert.equal(f.ui.isBusy(), false);
    assert.equal(await f.ui.load(), true); assert.equal(f.refreshes, 2); assert.equal(f.view.tasks[0].id, 'saved');
    await f.ui.load(); assert.equal(f.refreshes, 2); assert.equal(f.posts().length, 0);
  }
});

check('a host false result is not consumed and can be retried with the same run snapshot', async () => {
  const f = fixture([syncRun()]); f.setChanged(async () => false);
  assert.equal(await f.ui.load(), false); assert.equal(f.refreshes, 1);
  f.setChanged(null); assert.equal(await f.ui.load(), true); assert.equal(f.refreshes, 2);
  await f.ui.load(); assert.equal(f.refreshes, 2);
});

check('stop is a server POST, preserves saved results and discards an older in-flight poll', async () => {
  const run = syncRun({state: 'recognizing', states: ['succeeded','running','queued'], added: 1}), f = fixture([run]);
  f.remote.tasks = [{id: 'already-saved'}]; await f.ui.load();
  const delayed = deferred(); let captured;
  f.setAPI((call, normal) => {
    if (call.path === API && call.method === 'GET') { captured = call; return delayed.wait; }
    return normal(call);
  });
  const polling = f.ui.load(); assert.equal(f.ui.canStop(), true); await f.ui.stop();
  assert.deepEqual(f.posts()[0].body, {contract_version: VERSION});
  assert.equal(f.posts()[0].path, `${API}/${encodeURIComponent(run.run_id)}/stop`); assert.equal(captured.signal.aborted, true);
  delayed.resolve({contract_version: VERSION, runs: [run]}); await polling;
  assert.match(markup(f.ui), /已停止导入/); assert.equal(f.ui.isBusy(), false);
  assert.equal(f.view.tasks[0].id, 'already-saved'); assert.equal(f.refreshes, 2);
});

check('account changes abort waiting requests and reject old responses and callback writes', async () => {
  const f = fixture(), delayed = deferred(); let captured;
  f.setAPI(call => { captured = call; return delayed.wait; });
  const reading = f.ui.load(); f.switchAccount();
  delayed.resolve({contract_version: VERSION, runs: [syncRun({state: 'recognizing', states: ['running']})]}); await reading;
  assert.equal(captured.signal.aborted, true); assert.equal(markup(f.ui), ''); assert.equal(f.ui.ownsRequest('sync:0'), false);
  const next = fixture([syncRun()]), refreshing = deferred(); let valid;
  next.setChanged(async current => { valid = current; await refreshing.wait; if (current()) next.remote.leaked = true; return current(); });
  const loading = next.ui.load(); await new Promise(resolve => setImmediate(resolve));
  next.switchAccount(); assert.equal(valid(), false); refreshing.resolve(); await loading;
  assert.equal(next.remote.leaked, undefined); assert.equal(markup(next.ui), ''); assert.equal(next.ui.isBusy(), false);
});

check('account switching while a start waits cannot submit to the new account', async () => {
  const f = fixture(), saved = deferred(); f.setFlush(() => saved.wait);
  const starting = f.ui.start(); f.switchAccount(); saved.resolve(); await starting;
  assert.equal(f.posts().length, 0); assert.equal(f.ui.isBusy(), false);
});

check('single and batch retry select only the latest recoverable attempts', async () => {
  const old = syncRun({state: 'failed', states: ['failed','failed','incomplete']}), child = syncRun({id: 'run:child'});
  child.parent_run_id = old.run_id;
  const f = fixture([child, old]); await f.ui.load();
  assert.doesNotMatch(markup(f.ui), /后续已恢复|本轮记录保留/); assert.match(markup(f.ui), /处理未完成 · 2/);
  await category(f.ui, old.run_id, 'error'); await problem(f.ui, old.run_id, 1);
  assert.match(markup(f.ui), /data-source-sync-request="sync:1"/);
  assert.doesNotMatch(markup(f.ui), /data-source-sync-request="sync:0"|data-source-sync-request="sync:2"/);
  await f.ui.click(button('retry', old.run_id, 'sync:0')); assert.equal(f.posts().length, 0);
  await f.ui.click(button('retry', old.run_id, 'sync:2')); assert.equal(f.posts().length, 0);
  await f.ui.click(button('retry', old.run_id));
  const sent = f.posts()[0];
  assert.equal(sent.path, `${API}/${encodeURIComponent(old.run_id)}/retry`); assert.deepEqual(sent.body.request_ids, ['sync:1']);
  assert.equal(sent.body.consent, true); assert.equal(sent.body.contract_version, VERSION);
  assert.equal(typeof sent.body.operation_id, 'string'); assert.equal(Object.hasOwn(sent.body, 'records'), false);
  assert.equal(f.flushes, 1); assert.equal(f.ui.isBusy(), true);
});

check('a lost single retry response reuses operation identity and never includes successful records', async () => {
  const old = syncRun({state: 'partial_success', states: ['succeeded','failed']}), f = fixture([old]); await f.ui.load();
  let attempts = 0;
  f.setAPI((call, normal) => { if (call.path.endsWith('/retry') && ++attempts === 1) throw new Error('offline'); return normal(call); });
  await f.ui.click(button('retry', old.run_id, 'sync:1')); await f.ui.click(button('retry', old.run_id, 'sync:1'));
  assert.equal(f.posts().length, 2); assert.equal(f.posts()[0].body.operation_id, f.posts()[1].body.operation_id);
  assert.deepEqual(f.posts()[1].body.request_ids, ['sync:1']);
});

check('incomplete or changed sources offer recollection and configuration failures avoid futile retries', async () => {
  const run = syncRun({state: 'failed', states: ['incomplete','failed','failed']});
  run.records[0].label = '<script>unsafe</script>';
  run.records[1].error = {code: 'source_changed', message: '来源变化', retryable: true};
  run.records[2].error = {code: 'model_configuration', message: '配置不可用', retryable: false};
  const f = fixture([run]); await f.ui.load();
  await category(f.ui, run.run_id, 'error'); await problem(f.ui, run.run_id, 0);
  const html = markup(f.ui);
  assert.match(html, /data-source-sync-action="recover"/); assert.doesNotMatch(html, /data-source-sync-action="retry"|data-source-sync-action="collect"/);
  assert.match(html, /完整通知/); assert.doesNotMatch(html, /<script>|story-|draft-card/);
  await f.ui.click(button('recover',run.run_id)); assert.equal(f.posts()[0].path, `${API}/${encodeURIComponent(run.run_id)}/recover`);
  assert.deepEqual(f.posts()[0].body.request_ids,['sync:0','sync:1']);
});

check('one recovery action scopes mixed errors and never includes successful content or pending review', async () => {
  const run=syncRun({state:'partial_success',states:['succeeded','succeeded','failed','incomplete'],added:1});
  run.records[1].review=1;run.counts.review=1;
  const f=fixture([run]);await f.ui.load();
  const html=markup(f.ui);assert.match(html,/处理未完成 · 2/);
  assert.equal((html.match(/data-source-sync-action="recover"/g)||[]).length,1);
  assert.doesNotMatch(html,/继续未完成|重新获取来源/);
  let attempts=0;f.setAPI((call,normal)=>{if(call.path.endsWith('/recover')&&++attempts===1)throw new Error('offline');return normal(call);});
  await f.ui.click(button('recover',run.run_id));await f.ui.click(button('recover',run.run_id));
  assert.equal(f.posts().length,2);assert.equal(f.posts()[0].body.operation_id,f.posts()[1].body.operation_id);
  assert.deepEqual(f.posts()[1].body.request_ids,['sync:2','sync:3']);
  assert.equal(f.posts().some(call=>call.path===API),false);
  await f.ui.click(button('recover',run.run_id));assert.equal(f.posts().length,2);
});

check('recognition-only recovery reuses the captured source and targets just recoverable errors', async () => {
  const run=syncRun({state:'partial_success',states:['succeeded','failed'],added:1});
  const f=fixture([run]);await f.ui.load();await f.ui.click(button('recover',run.run_id));
  assert.equal(f.posts()[0].path,`${API}/${encodeURIComponent(run.run_id)}/retry`);
  assert.deepEqual(f.posts()[0].body.request_ids,['sync:1']);
});

check('progress connection errors preserve active state and retry polling without automatic writes', async () => {
  const f = fixture([syncRun({state: 'recognizing', states: ['running']})]); await f.ui.setVisible(true);
  f.setAPI(() => { throw new Error('offline'); }); await f.tick();
  assert.equal(f.ui.isBusy(), true); assert.match(markup(f.ui), /导入可能继续/);
  assert.equal(f.ui.canStop(), true); assert.doesNotMatch(markup(f.ui), /data-source-sync-action="stop"/);
  assert.equal(f.posts().length, 0); assert.equal(f.timers.size, 1);
});

check('history and selected category survive polling and clear on account reset', async () => {
  const old = syncRun({id: 'run:older', state: 'failed', states: ['failed']}), newest = syncRun();
  old.records[0].request_id = 'older:source';
  const f = fixture([newest, old]); await f.ui.load();
  f.ui.toggle({target: {dataset: {sourceSyncHistory: ''}, open: true}});
  await category(f.ui, old.run_id, 'error');
  await f.ui.load();
  assert.match(markup(f.ui), /data-source-sync-history open/);
  assert.match(markup(f.ui), /data-source-sync-category="error" aria-expanded="true"/);
  f.switchAccount(); assert.equal(f.ui.ownsRequest('older:source'), false); assert.equal(markup(f.ui), '');
});

check('hiding during completion refresh releases the stale lock and keeps its callback invalid', async () => {
  const f = fixture([syncRun()]), deferredRefresh = deferred(); let valid;
  f.setChanged(async current => { valid = current; await deferredRefresh.wait; return current(); });
  const loading = f.ui.setVisible(true); await new Promise(resolve => setImmediate(resolve));
  f.ui.setVisible(false); assert.equal(valid(), false);
  deferredRefresh.resolve(); await loading; assert.equal(f.ui.isBusy(), false); assert.equal(f.timers.size, 0);
  f.setChanged(null); await f.ui.setVisible(true); assert.equal(f.refreshes, 2);
});

check('older revisions and invalid service responses cannot replace saved progress', async () => {
  const saved = syncRun({added: 1}); saved.revision = 4;
  const f = fixture([saved]); await f.ui.load();
  const stale = syncRun({state: 'recognizing', states: ['running']}); stale.revision = 3;
  f.setRuns([stale]); await f.ui.load(); assert.equal(f.ui.isBusy(), false); assert.match(markup(f.ui), /新增 1 项/);
  f.setAPI(() => ({contract_version: 'source-sync/unknown', runs: []}));
  assert.equal(await f.ui.load(), false); assert.match(markup(f.ui), /功能暂时不可用/); assert.match(markup(f.ui), /新增 1 项/);
});

check('technical recovery errors use the shared user guidance and feedback control', async () => {
  const f = fixture();
  f.host.describeError = cause => ({kind: 'crawler_dependency', message: '缺少教学网采集组件，请安装后重新采集。', details: cause.message.includes('crawler') ? '在 crawler/ 安装依赖（pnpm install）。' : ''});
  f.setAPI(() => { throw new Error('crawler dependency unavailable'); });
  await f.ui.start();
  const html = markup(f.ui);
  assert.match(html, /暂时无法从教学网获取通知/); assert.match(html, /data-error-feedback=/); assert.doesNotMatch(html, /pnpm install/);
  assert.doesNotMatch(html, /<details class="import-error-details"[^>]*\bopen\b/);
});

check('history shows only added content, review candidates, updates and problems within a mixed run', async () => {
  const run = syncRun({states:['succeeded','succeeded','succeeded','failed'],state:'partial_success',added:1,skipped:52,updates:[{task_id:'existing',label:'<延期的报告>',course:'合成课程'}]});
  run.records[1].review=1;run.counts.review=1;
  run.records[0].added_items=[{candidate_id:'created',task_id:'task',label:'实际新增的作业',course:null}];
  run.records[1].review_items=[{candidate_id:'pending',label:'需要核对的作业',course:null}];
  const f=fixture([run]);await f.ui.load();
  const html=markup(f.ui);
  assert.match(html,/新增 1 项/);assert.match(html,/待核对 1 项/);assert.match(html,/更新 1 项/);assert.match(html,/错误 1 条/);
  assert.doesNotMatch(html,/处理详情|candidate-row|实际新增的作业|需要核对的作业|延期的报告/);
  await category(f.ui,run.run_id,'added');assert.match(markup(f.ui),/实际新增的作业/);assert.doesNotMatch(markup(f.ui),/需要核对的作业|合成通知/);
  await category(f.ui,run.run_id,'review');assert.match(markup(f.ui),/需要核对的作业/);assert.doesNotMatch(markup(f.ui),/实际新增的作业/);
  await category(f.ui,run.run_id,'updated');assert.match(markup(f.ui),/&lt;延期的报告&gt;/);assert.doesNotMatch(markup(f.ui),/<延期的报告>/);
  await category(f.ui,run.run_id,'error');assert.match(markup(f.ui),/合成通知 3/);assert.doesNotMatch(markup(f.ui),/合成通知 2|跳过|合成通知 0|合成通知 1/);
  await category(f.ui,run.run_id,'error');assert.doesNotMatch(markup(f.ui),/合成通知 3|sync-result-list/);
});

check('a run that only updates an existing task is still a useful result', async () => {
  const f=fixture([syncRun({states:[],updates:[{task_id:'task',label:'作业完成状态已同步',course:null}]})]);
  await f.ui.load();assert.match(f.ui.renderHistory(),/历史导入 · 1|更新 1 项|任务已更新/);
});

check('result names open their stable task or candidate identity and updates show actual changed fields', async () => {
  const run=syncRun({added:2,review:1,updates:[{task_id:'updated',label:'更新的报告',course:null,changed_fields:['due','status']}]});
  run.records[0].added_items=[{candidate_id:'a',task_id:'task:a',label:'<报告 A>',course:null},{candidate_id:'b',task_id:null,label:'已删除的报告',course:null}];
  run.records[0].review_items=[{candidate_id:'pending:a',label:'核对报告',course:null}];
  const f=fixture([run]), opened=[];f.host.openTask=id=>opened.push(['task',id]);f.host.openReview=id=>opened.push(['review',id]);
  await f.ui.load();await category(f.ui,run.run_id,'added');
  assert.match(markup(f.ui),/&lt;报告 A&gt;/);assert.doesNotMatch(markup(f.ui),/待核对 0|更新 0|错误 0|合成通知/);
  for(const [action,id] of [['open-task','task:a'],['open-review','pending:a'],['open-task','updated'],['open-task','forged']])
    await f.ui.click({dataset:{sourceSyncAction:action,sourceSyncRun:run.run_id,sourceSyncItem:id}});
  assert.deepEqual(opened,[['task','task:a'],['review','pending:a'],['task','updated']]);
  assert.equal(f.posts().length,0);assert.equal(f.messages.length,1);
  await category(f.ui,run.run_id,'updated');assert.match(markup(f.ui),/日期、状态已更新/);
  await category(f.ui,run.run_id,'added');await f.ui.load();assert.match(markup(f.ui),/&lt;报告 A&gt;/);
  await category(f.ui,run.run_id,'added');assert.doesNotMatch(markup(f.ui),/报告 A/);
});

check('legacy names are not fabricated and inconsistent result receipts are rejected', async () => {
  const run=syncRun({added:3});const f=fixture([run]);await f.ui.load();await category(f.ui,run.run_id,'added');
  assert.match(markup(f.ui),/3 项旧记录未保存具体名称/);assert.doesNotMatch(markup(f.ui),/合成通知/);
  run.records[0].added_items=[{candidate_id:'only',task_id:'task',label:'名称',course:null}];
  f.setRuns([run]);assert.equal(await f.ui.load(),false);assert.match(markup(f.ui),/功能暂时不可用/);
});

check('quiet scheduled checks only update last-check status and never create a history row or toast', async () => {
  const f=fixture();
  f.setLastCheck({run_id:'scheduled:quiet',state:'succeeded',updated_at:'2026-10-16T01:30:00Z',trigger:'scheduled',changed:false});
  await f.ui.load();assert.equal(f.ui.renderHistory(),'');assert.match(f.ui.renderProgress(),/上次检查|无新内容/);
  assert.equal(f.messages.length,0);assert.equal(f.refreshes,0);
  f.ui.reset();assert.equal(markup(f.ui),'');
});

check('a manually requested quiet check gives one temporary notification after its run disappears from history', async () => {
  const f=fixture();await f.ui.start();
  const id=f.posts()[0] && 'run:1';
  f.setRuns([]);f.setLastCheck({run_id:id,state:'succeeded',updated_at:'2026-10-16T01:30:00Z',trigger:'manual',changed:false});
  await f.ui.load();await f.ui.load();
  assert.deepEqual(f.messages,['已检查，没有新内容']);assert.equal(f.ui.renderHistory(),'');
  assert.doesNotMatch(f.ui.renderProgress(),/导入完成/);
});

check('a collection error remains visible even with no created tasks and no records', async () => {
  const run=syncRun({states:[],state:'failed'});run.error={code:'collection_failed',message:'合成采集失败',retryable:true};
  const f=fixture([run]);await f.ui.load();assert.match(f.ui.renderHistory(),/历史导入 · 1|处理未完成/);
  await f.ui.click(button('recover',run.run_id));assert.equal(f.posts()[0].path,API);
});

check('a recovered no-task retry does not leave an actionable old error after its quiet receipt is hidden', async () => {
  const old=syncRun({state:'failed',states:['failed']});old.records[0].recovered=true;
  const f=fixture([old]);await f.ui.load();
  assert.equal(f.ui.renderHistory(),'');assert.doesNotMatch(markup(f.ui),/后续已恢复|本轮记录保留|data-source-sync-action="retry"|错误 1 条/);
  await f.ui.click(button('retry',old.run_id));assert.equal(f.posts().length,0);
});

check('resolved errors disappear while genuine additions and pending review remain accessible', async () => {
  const run=syncRun({state:'partial_success',states:['succeeded','succeeded','failed'],added:1});
  run.records[1].review=1;run.counts.review=1;
  run.records[2].recovered=true;run.error=run.records[2].error;
  const f=fixture([run]);await f.ui.load();
  const html=f.ui.renderHistory();
  assert.match(html,/新增 1 项/);assert.match(html,/待核对 1 项/);
  assert.doesNotMatch(html,/错误|后续已恢复|本轮记录保留|处理未完成/);
});
