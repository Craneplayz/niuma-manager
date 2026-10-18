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
const categoryMarkup = (html, key, run) => html.match(new RegExp(`<button[^>]*${run ? `data-source-sync-run="${run}"[^>]*` : ""}data-source-sync-category="${key}"[^>]*>[^<]*</button>`))?.[0] || "";
const categoryKeys = html => [...html.matchAll(/data-source-sync-category="([^"]+)"/g)].map(match=>match[1]);
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
  let serial = 0, timerId = 0, highlightClears = 0, view = {tasks: [], batches: []};
  const calls = [], timers = new Map(), locks = [], callbacks = [], messages = [], revealed = [];
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
    if (call.method === 'GET') return clone(parent);
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
    revealImportResult: id => revealed.push(id),
    clearImportResultHighlight: () => highlightClears++,
    isImportBusy: () => otherBusy, render: () => renders++,
    getState: () => view,
    getCandidate: id => view.batches.flatMap(batch => batch.items || []).find(item => item.id === id),
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
    ui, host, calls, remote, timers, locks, callbacks, messages, revealed,
    setLastCheck(value) { lastCheck = clone(value); },
    get view() { return view; }, get renders() { return renders; }, get refreshes() { return refreshes; }, get flushes() { return flushes; }, get highlightClears() { return highlightClears; },
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
  assert.equal(f.ui.isBusy(), false); assert.match(markup(f.ui), /任务修改尚未保存/);
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
    assert.equal(await f.ui.load(), false); assert.match(markup(f.ui), /结果已保存.*页面更新失败/);
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
  assert.equal(f.ui.isBusy(), true); assert.match(markup(f.ui), /导入可能仍在进行/);
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
  assert.match(html,/新增 1 项/);assert.match(html,/待核对 1 项/);assert.match(html,/修改 1 项/);assert.match(html,/错误 1 条/);
  assert.doesNotMatch(html,/处理详情|candidate-row|实际新增的作业|需要核对的作业|延期的报告/);
  await category(f.ui,run.run_id,'added');assert.match(markup(f.ui),/实际新增的作业/);assert.doesNotMatch(markup(f.ui),/需要核对的作业|合成通知/);
  await category(f.ui,run.run_id,'review');assert.match(markup(f.ui),/需要核对的作业/);assert.doesNotMatch(markup(f.ui),/实际新增的作业/);
  await category(f.ui,run.run_id,'updated');assert.match(markup(f.ui),/&lt;延期的报告&gt;/);assert.doesNotMatch(markup(f.ui),/<延期的报告>/);
  await category(f.ui,run.run_id,'error');assert.match(markup(f.ui),/合成通知 3/);assert.doesNotMatch(markup(f.ui),/合成通知 2|跳过|合成通知 0|合成通知 1/);
  await category(f.ui,run.run_id,'error');assert.doesNotMatch(markup(f.ui),/合成通知 3|sync-result-list/);
});

check('a run that only updates an existing task is still a useful result', async () => {
  const f=fixture([syncRun({states:[],updates:[{task_id:'task',label:'作业完成状态已同步',course:null}]})]);
  await f.ui.load();assert.match(f.ui.renderHistory(),/历史导入 · 1|修改 1 项|任务已更新/);
});

check('result names open their stable task or candidate identity and updates show actual changed fields', async () => {
  const run=syncRun({added:2,review:1,updates:[{task_id:'updated',label:'更新的报告',course:null,changed_fields:['due','status']}]});
  run.records[0].added_items=[{candidate_id:'a',task_id:'task:a',label:'<报告 A>',course:null},{candidate_id:'b',task_id:null,label:'已删除的报告',course:null}];
  run.records[0].review_items=[{candidate_id:'pending:a',label:'核对报告',course:null}];
  const f=fixture([run]), opened=[];f.host.openTask=id=>opened.push(['task',id]);f.host.openReview=id=>opened.push(['review',id]);
  f.remote.tasks=[{id:'task:a',status:'todo'},{id:'updated',status:'todo'}];
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

check('a quiet scheduled run completed between polls moves the previous manual result into history', async () => {
  const f = fixture();
  await f.ui.setVisible(true);
  await f.ui.start();
  const finished = syncRun({id: 'run:1', added: 1});
  finished.updated_at = '2026-10-16T01:00:00Z';
  f.setRuns([finished]);
  f.setLastCheck({run_id: finished.run_id, state: 'succeeded', updated_at: finished.updated_at, trigger: 'manual', changed: true});
  await f.tick();
  assert.match(f.ui.renderProgress(), /新增 1 项/);
  assert.equal(f.ui.renderHistory(), '');
  assert.equal([...f.timers.values()][0].ms, 30000);
  const refreshes = f.refreshes, renders = f.renders;

  // The scheduled run starts and finishes before the next poll; only last_check changes.
  f.setLastCheck({run_id: 'scheduled:quiet', state: 'succeeded', updated_at: '2026-10-16T01:30:00Z', trigger: 'scheduled', changed: false});
  await f.tick();
  assert.match(f.ui.renderProgress(), /data-source-sync-last-check/);
  assert.match(f.ui.renderProgress(), /datetime="2026-10-16T01:30:00Z"/);
  assert.match(f.ui.renderProgress(), /无新内容/);
  assert.doesNotMatch(f.ui.renderProgress(), /导入完成|新增 1 项/);
  assert.match(f.ui.renderHistory(), /历史导入 · 1/);
  assert.match(f.ui.renderHistory(), /新增 1 项/);
  assert.equal(f.refreshes, refreshes);
  assert.equal(f.renders, renders + 1);
  await f.tick();
  assert.match(f.ui.renderProgress(), /data-source-sync-last-check/);
  assert.match(f.ui.renderHistory(), /历史导入 · 1/);
  assert.deepEqual(f.messages, []);
});

check('an older check or the focused run own receipt keeps the current manual result visible', async () => {
  for (const [id, time] of [
    ['scheduled:older', '2026-10-16T00:30:00Z'],
    ['scheduled:tied', '2026-10-16T01:00:00Z'],
    ['run:1', '2026-10-16T01:30:00Z'],
  ]) {
    const f = fixture();
    await f.ui.start();
    const finished = syncRun({id: 'run:1', added: 1});
    finished.updated_at = '2026-10-16T01:00:00Z';
    f.setRuns([finished]);
    f.setLastCheck({run_id: id, state: 'succeeded', updated_at: time, trigger: id === 'run:1' ? 'manual' : 'scheduled', changed: id === 'run:1'});
    await f.ui.load();
    assert.match(f.ui.renderProgress(), /新增 1 项/);
    assert.doesNotMatch(f.ui.renderProgress(), /data-source-sync-last-check/);
    assert.equal(f.ui.renderHistory(), '');
  }
});

check('a completed check never displaces an active import or its results', async () => {
  const run = syncRun({state: 'recognizing', states: ['succeeded', 'running'], added: 1});
  run.updated_at = '2026-10-16T01:00:00Z';
  const f = fixture([run]);
  f.setLastCheck({run_id: 'scheduled:quiet', state: 'succeeded', updated_at: '2026-10-16T01:30:00Z', trigger: 'scheduled', changed: false});
  await f.ui.load();
  assert.equal(f.ui.isRunning(), true);
  assert.equal(f.ui.canStop(), true);
  assert.match(f.ui.progressLabel(), /正在识别/);
  assert.match(f.ui.renderProgress(), /新增 1 项/);
  assert.doesNotMatch(f.ui.renderProgress(), /data-source-sync-last-check/);
  assert.equal(f.ui.renderHistory(), '');
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

check('a recovered no-task retry keeps a gray receipt and a green category without an exception action', async () => {
  const old=syncRun({state:'failed',states:['failed']});old.records[0].recovered=true;
  const f=fixture([old]);await f.ui.load();
  await category(f.ui,old.run_id,'error');
  assert.match(markup(f.ui),/sync-item-status-muted">已恢复/);
  assert.match(categoryMarkup(markup(f.ui),"error"),/sync-category-success/);
  assert.match(categoryMarkup(markup(f.ui),"error"),/>错误 1 条<\/button>/);
  assert.doesNotMatch(markup(f.ui),/处理未完成|data-source-sync-action="recover"|sync-item-status-error/);
  await f.ui.click(button('retry',old.run_id));assert.equal(f.posts().length,0);
});

check('resolved errors stay within the original batch alongside additions and review receipts', async () => {
  const run=syncRun({state:'partial_success',states:['succeeded','succeeded','failed'],added:1});
  run.records[1].review=1;run.counts.review=1;
  run.records[2].recovered=true;run.error=run.records[2].error;
  const f=fixture([run]);await f.ui.load();
  const html=f.ui.renderHistory();
  assert.match(html,/新增 1 项/);assert.match(html,/待核对 1 项/);
  assert.match(html,/错误 1 条/);assert.doesNotMatch(html,/处理未完成/);
  await category(f.ui,run.run_id,'error');assert.match(markup(f.ui),/sync-item-status-muted">已恢复/);
});

check('eight recovered errors keep their historical total with an all-recovered summary', async () => {
  const run=syncRun({state:'succeeded',states:Array(8).fill('failed')});
  run.records.forEach(record=>record.recovered=true);
  const f=fixture([run]);await f.ui.load();await category(f.ui,run.run_id,'error');
  const html=markup(f.ui);
  assert.match(html,/导入完成/);assert.match(categoryMarkup(html,"error"),/sync-category-success/);
  assert.match(categoryMarkup(html,"error"),/>错误 8 条<\/button>/);
  assert.doesNotMatch(html,/全部已恢复|部分已恢复/);
  assert.equal((html.match(/sync-item-status-muted">已恢复/g)||[]).length,8);
  assert.doesNotMatch(html,/处理未完成|sync-category-error|sync-item-status-error/);
});

check('partial recovery preserves additions and review totals with an amber summary and remaining retry', async () => {
  const run=syncRun({state:'partial_success',states:['succeeded','failed','failed'],added:1,review:1});
  run.records[1].recovered=true;
  const f=fixture([run]);await f.ui.load();await category(f.ui,run.run_id,'error');
  const html=markup(f.ui);
  assert.match(html,/新增 1 项/);assert.match(html,/待核对 1 项/);
  assert.match(categoryMarkup(html,"error"),/sync-category-review/);
  assert.match(categoryMarkup(html,"error"),/>错误 2 条<\/button>/);
  assert.match(categoryMarkup(html,"error"),/已恢复 1 \/ 2 条；仍需处理 1 条/);
  assert.match(html,/sync-item-status-muted">已恢复/);assert.match(html,/sync-item-status-error">错误/);
  assert.match(html,/处理未完成 · 1/);assert.doesNotMatch(html,/全部已恢复/);
  await f.ui.click(button('recover',run.run_id));
  assert.deepEqual(f.posts()[0].body.request_ids,[run.records[2].request_id]);
});

check('unresolved errors keep red status and do not claim recovery', async () => {
  const run=syncRun({state:'failed',states:['failed']});
  const f=fixture([run]);await f.ui.load();await category(f.ui,run.run_id,'error');
  assert.match(categoryMarkup(markup(f.ui),"error"),/sync-category-error/);
  assert.match(markup(f.ui),/sync-item-status-error">错误/);
  assert.doesNotMatch(markup(f.ui),/全部已恢复|部分已恢复/);
});

check('a recovered collection error keeps its receipt, hides stale guidance and locates the later batch', async () => {
  const old=syncRun({id:'collection:failed',states:[],state:'succeeded'});
  old.error={code:'collection_failed',message:'核对密码并重新获取',retryable:true};
  const target=syncRun({id:'collection:completed',states:['failed'],state:'failed'});
  old.recovered_by={run_id:target.run_id};
  const f=fixture([target,old]);await f.ui.load();await category(f.ui,old.run_id,'error');
  await problem(f.ui,old.run_id,0);
  let html=markup(f.ui);
  assert.match(categoryMarkup(html,"error",old.run_id),/sync-category-success/);assert.match(html,/后续已恢复/);
  assert.doesNotMatch(html,/核对密码并重新获取/);
  assert.doesNotMatch(html,new RegExp(`data-source-sync-action="recover" data-source-sync-run="${old.run_id}"`));
  await f.ui.click(button('recover',old.run_id));assert.equal(f.posts().length,0);
  await f.ui.click({dataset:{sourceSyncAction:'locate-recovery',sourceSyncRun:old.run_id,sourceSyncItem:'0'}});
  assert.deepEqual(f.revealed,[`sync-run-${target.run_id}`]);assert.equal(f.posts().length,0);
});

check('a recovered whole collection can locate a quiet check without starting another import', async () => {
  const old=syncRun({id:'collection:old',states:[],state:'succeeded'});
  old.error={code:'collection_failed',message:'旧采集失败',retryable:true};old.recovered_by={run_id:'quiet:success'};
  const target=syncRun({id:'quiet:success',states:[]}), f=fixture([old]);
  f.setAPI((call,next)=>call.path===`${API}/${encodeURIComponent(target.run_id)}`?clone(target):next(call));
  await f.ui.load();await f.ui.click({dataset:{sourceSyncAction:'locate-recovery',sourceSyncRun:old.run_id,sourceSyncItem:'0'}});
  assert.deepEqual(f.revealed,[`sync-run-${target.run_id}`]);
  assert.match(markup(f.ui),/这次处理已完成，没有新增任务或待核对内容/);assert.equal(f.posts().length,0);
});

check('receipt statuses and confirmed-candidate links follow current tasks without changing totals', async () => {
  const run=syncRun({added:1,review:1});
  run.records[0].added_items=[{candidate_id:'auto',task_id:'task',label:'原始任务名称',course:null}];
  run.records[0].review_items=[{candidate_id:'manual',label:'需要核对的任务',course:null}];
  const f=fixture([run]);
  f.remote.tasks=[{id:'task',status:'todo'}];
  f.remote.batches=[{items:[{id:'manual',state:'pending'}]}];
  await f.ui.load();await category(f.ui,run.run_id,'added');
  assert.match(markup(f.ui),/sync-item-status-muted">未完成/);
  f.view.tasks[0].status='done';
  assert.match(markup(f.ui),/sync-item-status-success">✓ 已完成/);
  await category(f.ui,run.run_id,'review');assert.match(markup(f.ui),/sync-item-status-review">待核对/);
  f.view.batches[0].items[0].state='applied';
  f.view.tasks.push({id:'confirmed',status:'done',recognitionHistory:[{storageId:'manual',action:'create'}]});
  assert.match(markup(f.ui),/sync-item-status-success">✓ 已完成/);
  assert.doesNotMatch(markup(f.ui),/sync-item-status-review/);
  assert.match(markup(f.ui),/新增 1 项/);assert.match(markup(f.ui),/待核对 1 项/);
  await category(f.ui,run.run_id,'added');f.view.tasks=[];
  assert.match(markup(f.ui),/已移除/);assert.doesNotMatch(markup(f.ui),/">未完成<|进行中/);
  await category(f.ui,run.run_id,'error');
  assert.equal(f.posts().length,0);
});

check('categories follow action priority while recovery and review keep the selected list and historical counts', async () => {
  const run=syncRun({state:'partial_success',states:['succeeded','failed'],added:1,review:1,
    updates:[{task_id:'updated',label:'修改过的任务',course:null}]});
  run.records[0].added_items=[{candidate_id:'auto',task_id:'task',label:'新增任务',course:null}];
  run.records[0].review_items=[{candidate_id:'manual',label:'待核对任务',course:null}];
  const f=fixture([run]);f.remote.batches=[{items:[{id:'manual',state:'pending'}]}];
  await f.ui.load();await category(f.ui,run.run_id,'review');
  let html=markup(f.ui);
  assert.deepEqual(categoryKeys(html),['error','review','added','updated']);
  assert.match(categoryMarkup(html,'error'),/sync-category-error/);
  assert.match(categoryMarkup(html,'review'),/sync-category-review/);
  for(const key of ['added','updated'])assert.match(categoryMarkup(html,key),/sync-category-success/);
  run.records[1].recovered=true;run.revision++;
  f.setRuns([run]);await f.ui.load();html=markup(f.ui);
  assert.deepEqual(categoryKeys(html),['review','added','updated','error']);
  assert.match(categoryMarkup(html,'error'),/sync-category-success/);
  assert.match(categoryMarkup(html,'review'),/aria-expanded="true"/);
  f.view.batches[0].items[0].state='applied';
  html=markup(f.ui);
  assert.deepEqual(categoryKeys(html),['added','updated','review','error']);
  assert.match(categoryMarkup(html,'review'),/sync-category-success/);
  assert.match(categoryMarkup(html,'review'),/>待核对 1 项<\/button>/);
  assert.match(categoryMarkup(html,'review'),/aria-expanded="true"/);
  f.view.batches[0].items[0].state='pending';
  assert.deepEqual(categoryKeys(markup(f.ui)),['review','added','updated','error']);
});

check('ignored and confirmed review receipts turn green while unknown legacy receipts remain amber', async () => {
  const run=syncRun({review:2});
  run.records[0].review_items=[{candidate_id:'ignored',label:'已忽略',course:null},{candidate_id:'confirmed',label:'已核对',course:null}];
  const f=fixture([run]);f.remote.batches=[{items:[{id:'ignored',state:'ignored'},{id:'confirmed',state:'applied'}]}];
  await f.ui.load();assert.match(categoryMarkup(markup(f.ui),'review'),/sync-category-success/);
  f.view.batches=[];
  f.view.tasks=[{id:'task',recognitionHistory:[{storageId:'confirmed'}]}];
  assert.match(categoryMarkup(markup(f.ui),'review'),/sync-category-review/);
  f.view.tasks.push({id:'other-task',recognitionHistory:[{storageId:'ignored'}]});
  assert.match(categoryMarkup(markup(f.ui),'review'),/sync-category-success/);
  delete run.records[0].review_items;run.revision++;f.setRuns([run]);await f.ui.load();
  assert.match(categoryMarkup(markup(f.ui),'review'),/sync-category-review/);
});

function linkedRecovery() {
  const old=syncRun({id:'old:failure',state:'failed',states:['failed']});
  const target=syncRun({id:'recovery:success',added:1});
  target.records[0].added_items=[{candidate_id:'repaired',task_id:'repaired-task',label:'恢复后加入的任务',course:null}];
  old.records[0].recovered=true;
  old.records[0].recovered_by={run_id:target.run_id,request_id:target.records[0].request_id};
  return {old,target};
}
const locate=(ui,run)=>ui.click({dataset:{sourceSyncAction:'locate-recovery',sourceSyncRun:run.run_id,sourceSyncItem:'0'}});

check('only the recovered status locates and expands its successful batch without importing again', async () => {
  const {old,target}=linkedRecovery(),f=fixture([target,old]);await f.ui.load();
  await category(f.ui,old.run_id,'error');
  assert.match(markup(f.ui),/class="sync-recovery-link"[^>]*data-source-sync-action="locate-recovery"/);
  await problem(f.ui,old.run_id,0);assert.deepEqual(f.revealed,[]);
  await locate(f.ui,old);
  assert.deepEqual(f.revealed,[`sync-run-${target.run_id}`]);
  assert.match(markup(f.ui),/data-source-sync-history open/);
  assert.match(markup(f.ui),/data-source-sync-highlight/);
  assert.doesNotMatch(markup(f.ui),/↳|sync-recovery-hint|data-source-sync-hint/);
  assert.doesNotMatch(markup(f.ui),/sync-run-located/);
  assert.match(markup(f.ui),/data-source-sync-category="added" aria-expanded="true"/);
  assert.match(markup(f.ui),/恢复后加入的任务/);assert.equal(f.posts().length,0);
});

check('a compacted successful source locates its mixed retry batch and explains the empty result', async () => {
  for (const cached of [true,false]) {
    const {old,target}=linkedRecovery();
    Object.assign(target,syncRun({id:target.run_id,state:'partial_success',states:['failed'],added:1}));
    target.records[0].request_id='other:failed';
    target.records[0].added_items=[{candidate_id:'unrelated',task_id:'unrelated',label:'其他来源新增的任务',course:null}];
    const f=fixture(cached?[target,old]:[old]);
    f.setAPI((call,normal)=>call.path===API+'/'+encodeURIComponent(target.run_id)?clone(target):normal(call));
    await f.ui.load();await locate(f.ui,old);
    assert.deepEqual(f.messages,[]);
    assert.deepEqual(f.revealed,[`sync-run-${target.run_id}`]);
    const html=markup(f.ui),row=html.match(new RegExp(`<article id="sync-run-${target.run_id}"[\\s\\S]*?</article>`))[0];
    assert.match(row,/sync-run-heading" data-source-sync-highlight/);
    assert.match(row,/部分完成/);
    assert.match(row,/这条来源已处理完成，没有新增任务或待核对内容。/);
    assert.match(row,/错误 1 条/);assert.match(row,/新增 1 项/);
    if(cached) assert.match(row,/处理未完成 · 1/);
    assert.doesNotMatch(row,/data-source-sync-category="added" aria-expanded="true"/);
    await f.ui.load();assert.match(markup(f.ui),/这条来源已处理完成/);
    await category(f.ui,target.run_id,'error');
    assert.doesNotMatch(markup(f.ui),/这条来源已处理完成|data-source-sync-highlight/);
    assert.equal(f.posts().length,0);
  }
});

check('a contradictory source receipt, active target or mismatched batch cannot claim recovery', async () => {
  for (const kind of ['failed-source','active','wrong-run']) {
    const {old,target}=linkedRecovery(),f=fixture([old]);
    if(kind==='failed-source') target.records[0].state='failed';
    if(kind==='active') {target.state='recognizing';target.records=[];}
    if(kind==='wrong-run') target.run_id='wrong:run';
    f.setAPI((call,normal)=>call.path===API+'/'+encodeURIComponent(old.records[0].recovered_by.run_id)?clone(target):normal(call));
    await f.ui.load();await locate(f.ui,old);
    assert.deepEqual(f.revealed,[]);assert.equal(f.messages.length,1);
    assert.equal(f.posts().length,0);
  }
});

check('recovery highlights survive redraws, polling and navigation cancellation without a timeout', async () => {
  const {old,target}=linkedRecovery(),f=fixture([target,old]);await f.ui.load();await locate(f.ui,old);
  const renders=f.renders;
  markup(f.ui);markup(f.ui);f.ui.cancelRecoveryNavigation();
  assert.equal([...f.timers.values()].filter(timer=>timer.ms===3000).length,0);
  assert.equal(f.renders,renders);assert.equal(f.highlightClears,0);
  assert.match(markup(f.ui),/data-source-sync-highlight/);
  await f.ui.load();
  assert.match(markup(f.ui),/data-source-sync-highlight/);
  assert.deepEqual(f.revealed,[`sync-run-${target.run_id}`]);
});

check('changing categories or leaving import clears recovery highlights without restoring them on refresh', async () => {
  const {old,target}=linkedRecovery(),f=fixture([target,old]);await f.ui.load();await locate(f.ui,old);
  await category(f.ui,old.run_id,'error');
  assert.doesNotMatch(markup(f.ui),/data-source-sync-highlight/);
  assert.equal(f.highlightClears,1);
  await locate(f.ui,old);
  assert.match(markup(f.ui),/data-source-sync-highlight/);
  f.ui.setVisible(false);
  assert.doesNotMatch(markup(f.ui),/data-source-sync-highlight/);
  assert.equal(f.highlightClears,2);
  await f.ui.load();assert.doesNotMatch(markup(f.ui),/data-source-sync-highlight/);
});

check('recovery highlights every result from the matching source and locates the batch header', async () => {
  const {old,target}=linkedRecovery();
  const second=clone(target.records[0]);second.request_id='source:second';second.added=2;
  second.added_items=[{candidate_id:'second',task_id:'second-task',label:'真正的恢复结果',course:null},
    {candidate_id:'third',task_id:'third-task',label:'同一来源的另一条结果',course:null}];
  target.records.push(second);target.counts.total++;target.counts.succeeded++;target.counts.added+=2;
  old.records[0].recovered_by.request_id=second.request_id;
  const f=fixture([target,old]);await f.ui.load();await locate(f.ui,old);
  assert.deepEqual(f.revealed,[`sync-run-${target.run_id}`]);
  const highlighted=[...markup(f.ui).matchAll(/<button id="([^"]+)"[^>]*data-source-sync-highlight/g)].map(match=>match[1]);
  assert.deepEqual(highlighted,[`sync-item-${target.run_id}-added-1`,`sync-item-${target.run_id}-added-2`]);
});

check('update-only recovery highlights linked task identities and leaves unrelated changes unmarked', async () => {
  const {old,target}=linkedRecovery();target.records[0].added=0;target.records[0].added_items=[];target.counts.added=0;
  target.updated_tasks=[{task_id:'other',label:'无关修改',course:null},{task_id:'linked',label:'对应修改',course:null}];
  target.counts.updated=2;
  const f=fixture([target,old]);f.remote.tasks=[{id:'other'},
    {id:'linked',recognitionHistory:[{requestId:target.records[0].request_id}]}];
  await f.ui.load();await locate(f.ui,old);
  assert.deepEqual(f.revealed,[`sync-run-${target.run_id}`]);
  const highlighted=[...markup(f.ui).matchAll(/<button id="([^"]+)"[^>]*data-source-sync-highlight/g)].map(match=>match[1]);
  assert.deepEqual(highlighted,[`sync-item-${target.run_id}-updated-1`]);
});

check('another interaction cancels delayed navigation without late scrolling or stale highlights', async () => {
  const {old,target}=linkedRecovery(),f=fixture([old]),gate=deferred();
  f.setAPI((call,fallback)=>call.path===API+'/'+encodeURIComponent(target.run_id)?gate.wait:fallback(call));
  await f.ui.load();const locating=locate(f.ui,old);
  f.ui.cancelRecoveryNavigation();gate.resolve(target);await locating;
  assert.deepEqual(f.revealed,[]);assert.deepEqual(f.messages,[]);
  assert.doesNotMatch(markup(f.ui),/data-source-sync-highlight|恢复后加入的任务/);
});

check('recovery navigation fetches a batch outside the recent twenty and keeps it visible', async () => {
  const {old,target}=linkedRecovery();target.created_at='2020-01-01T00:00:00Z';
  const f=fixture([old,...Array.from({length:19},(_,i)=>syncRun({id:'filler:'+i,added:1}))]);
  f.setAPI((call,fallback)=>call.path===API+'/'+encodeURIComponent(target.run_id)?clone(target):fallback(call));
  await f.ui.load();await locate(f.ui,old);
  assert.match(f.ui.renderHistory(),/历史导入 · 20/);
  assert.match(f.ui.renderHistory(),/id="sync-run-recovery:success"/);
  assert.match(f.ui.renderHistory(),/恢复后加入的任务/);
  assert.equal(f.calls.filter(call=>call.path===API+'/'+encodeURIComponent(target.run_id)).length,1);
  assert.equal(f.posts().length,0);
});

check('account switches discard a delayed recovery navigation response', async () => {
  const {old,target}=linkedRecovery(),f=fixture([old]),gate=deferred();
  f.setAPI((call,fallback)=>call.path===API+'/'+encodeURIComponent(target.run_id)?gate.wait:fallback(call));
  await f.ui.load();const locating=locate(f.ui,old);f.switchAccount();gate.resolve(target);await locating;
  assert.equal(markup(f.ui),'');assert.deepEqual(f.revealed,[]);assert.deepEqual(f.messages,[]);
});

check('expired recovery receipts leave saved results intact and never trigger another import', async () => {
  const {old,target}=linkedRecovery(),f=fixture([old]);
  f.setAPI((call,fallback)=>{if(call.path===API+'/'+encodeURIComponent(target.run_id))throw {status:404};return fallback(call);});
  await f.ui.load();await locate(f.ui,old);
  assert.equal(f.messages[0],"对应的恢复记录已无法查看，已保存的任务不受影响");
  assert.deepEqual(f.revealed,[]);assert.equal(f.posts().length,0);
});

check('trash status reuses the shared trash icon and returns to task status after restoring', async () => {
  const run=syncRun({added:1});run.records[0].added_items=[{candidate_id:'auto',task_id:'removed',label:'放入回收站的任务',course:null}];
  const f=fixture([run]);await f.ui.load();await category(f.ui,run.run_id,'added');
  f.view.trash=[{id:'removed',status:'done'}];
  const html=markup(f.ui);
  assert.match(html,/sync-item-status-trash"><svg[^>]*aria-hidden="true"/);
  assert.match(html,/>回收站<\/small>/);assert.doesNotMatch(html,/已移至|✓ 已完成|">未完成</);
  f.view.tasks=f.view.trash;f.view.trash=[];
  assert.match(markup(f.ui),/✓ 已完成/);assert.doesNotMatch(markup(f.ui),/sync-item-status-trash/);
});

check('reviewed cancellations and removed auto additions show the actual outcome without moving categories', async () => {
  const run=syncRun({added:1,review:2});
  run.records[0].added_items=[{candidate_id:'auto',task_id:'removed',label:'自动新增后移除的任务',course:null}];
  run.records[0].review_items=[{candidate_id:'cancel',label:'已确认取消',course:null},{candidate_id:'pending',label:'尚未核对',course:null}];
  const f=fixture([run]);
  f.remote.batches=[{items:[{id:'auto',state:'applied',autoApplied:true},{id:'cancel',state:'applied'},{id:'pending',state:'pending'}]}];
  f.host.getCancelledTasks=()=>[{id:'cancelled',status:'cancelled',recognitionHistory:[{storageId:'cancel',action:'cancel'}]}];
  await f.ui.load();await category(f.ui,run.run_id,'added');
  assert.match(markup(f.ui),/sync-item-status-muted">已移除/);
  assert.doesNotMatch(markup(f.ui),/已核对|尚未核对/);
  await f.ui.click({dataset:{sourceSyncAction:'open-task',sourceSyncRun:run.run_id,sourceSyncItem:'removed'}});
  assert.equal(f.messages.at(-1),'这条任务或通知已移除，无法打开详情');
  await category(f.ui,run.run_id,'review');
  assert.match(markup(f.ui),/sync-item-status-muted">已取消/);
  assert.match(markup(f.ui),/sync-item-status-review">待核对/);
  assert.match(categoryMarkup(markup(f.ui),'review'),/sync-category-review/);
  assert.match(markup(f.ui),/新增 1 项/);assert.match(markup(f.ui),/待核对 2 项/);
});

check('notices use the shared leading badge and task completion status, including after review', async () => {
  const run=syncRun({added:2,review:1});
  run.records[0].added_items=[{candidate_id:'unread',task_id:'notice',label:'地点变更',course:null},
    {candidate_id:'read',task_id:'read-notice',label:'活动安排',course:null}];
  run.records[0].review_items=[{candidate_id:'manual-notice',label:'待确认地点',course:null}];
  const f=fixture([run]);
  f.remote.tasks=[{id:'notice',content_type:'notice',status:'todo'},{id:'read-notice',content_type:'notice',status:'done'}];
  f.remote.batches=[{items:[{id:'manual-notice',state:'pending',candidate:{content_type:'notice'}}]}];
  await f.ui.load();await category(f.ui,run.run_id,'added');
  let html=markup(f.ui);
  assert.match(html,/<span class="notice-label">通知<\/span><span class="sync-result-title">地点变更<\/span><small[^>]*>未完成/);
  assert.match(html,/<span class="notice-label">通知<\/span><span class="sync-result-title">活动安排<\/span><small[^>]*>✓ 已完成/);
  await category(f.ui,run.run_id,'review');
  assert.match(markup(f.ui),/<span class="notice-label">通知<\/span><span class="sync-result-title">待确认地点/);
  f.view.batches[0].items[0].state='applied';
  f.view.tasks.push({id:'manual',content_type:'notice',status:'done',recognitionHistory:[{storageId:'manual-notice',action:'create'}]});
  html=markup(f.ui);
  assert.match(html,/sync-item-status-success">✓ 已完成/);
  assert.match(categoryMarkup(html,'review'),/sync-category-success/);
  assert.doesNotMatch(html,/已核对/);
});

check('whole recovery highlights all categories across fetched retry batches and keeps every linked batch beyond the history limit', async () => {
  const {old,target}=linkedRecovery();
  old.records=[];old.error={code:'collection_failed',message:'首次连接失败',retryable:true};
  old.recovered_by={run_id:target.run_id};old.counts={...old.counts,total:0,failed:0};
  target.created_at='2020-01-01T00:00:00Z';
  const retry=syncRun({id:'retry:later',added:1,review:1,updates:[{task_id:'updated',label:'后续更新',course:null}]});
  retry.created_at='2020-01-02T00:00:00Z';
  retry.records[0].request_id='retry:source';
  retry.records[0].added_items=[{candidate_id:'retry-added',task_id:'retry-task',label:'后续新增',course:null}];
  retry.records[0].review_items=[{candidate_id:'retry-review',label:'后续核对',course:null}];
  const failed={request_id:'earlier:source',label:'首次模型错误',state:'failed',added:0,review:0,error:null,
    recovered:true,recovered_by:{run_id:retry.run_id,request_id:'retry:source'}};
  target.records.push(failed);target.counts.total++;target.counts.failed++;
  const f=fixture([old,...Array.from({length:19},(_,i)=>syncRun({id:'filler:whole:'+i,added:1}))]);
  f.remote.tasks=[{id:'updated',recognitionHistory:[{requestId:'retry:source'}]}];
  f.setAPI((call,normal)=>call.path===API+'/'+encodeURIComponent(target.run_id)?clone(target):
    call.path===API+'/'+encodeURIComponent(retry.run_id)?clone(retry):normal(call));
  await f.ui.load();await locate(f.ui,old);
  assert.deepEqual(f.revealed,[`sync-run-${retry.run_id}`]);
  const html=markup(f.ui);
  const highlighted=[...html.matchAll(/<button id="([^"]+)"[^>]*data-source-sync-highlight/g)].map(match=>match[1]);
  assert.deepEqual(new Set(highlighted),new Set([`sync-item-${target.run_id}-added-0`,
    `sync-item-${retry.run_id}-added-0`,`sync-item-${retry.run_id}-review-0`,`sync-item-${retry.run_id}-updated-0`]));
  for(const key of ['added','review','updated']) assert.match(categoryMarkup(html,key,retry.run_id),/aria-expanded="true"/);
  assert.match(html,/历史导入 · 20/);assert.equal(f.posts().length,0);
  await f.ui.load();assert.equal((markup(f.ui).match(/data-source-sync-highlight/g)||[]).length,4);
  await category(f.ui,retry.run_id,'review');assert.doesNotMatch(markup(f.ui),/data-source-sync-highlight/);
});

check('whole recovery follows unprojected receipts when the listed batch has hidden settled errors', async () => {
  const {old,target}=linkedRecovery();
  old.records=[];old.error={code:'collection_failed',message:'旧连接失败',retryable:true};
  old.recovered_by={run_id:target.run_id};old.counts={...old.counts,total:0,failed:0};
  const next=syncRun({id:'hidden:retry',added:1});
  next.records[0].added_items=[{candidate_id:'next',task_id:'next-task',label:'隐藏错误恢复后的任务',course:null}];
  const raw=clone(target);
  raw.records.push({request_id:'hidden:failure',label:'已隐藏错误',state:'failed',added:0,review:0,error:null,
    recovered:true,recovered_by:{run_id:next.run_id,request_id:next.records[0].request_id}});
  raw.counts.total++;raw.counts.failed++;
  const f=fixture([target,old]);
  f.setAPI((call,normal)=>call.path===API+'/'+encodeURIComponent(target.run_id)?clone(raw):
    call.path===API+'/'+encodeURIComponent(next.run_id)?clone(next):normal(call));
  await f.ui.load();await locate(f.ui,old);
  assert.match(markup(f.ui),/隐藏错误恢复后的任务/);
  assert.equal((markup(f.ui).match(/data-source-sync-highlight/g)||[]).length,2);
  assert.equal(f.calls.filter(call=>call.path===API+'/'+encodeURIComponent(target.run_id)).length,1);
});

check('whole recovery retains read-only legacy links while loading the full receipt', async () => {
  const {old,target}=linkedRecovery();
  old.records=[];old.error={code:'collection_failed',message:'旧连接失败',retryable:true};
  old.recovered_by={run_id:target.run_id};old.counts={...old.counts,total:0,failed:0};
  const next=syncRun({id:'legacy:retry',added:1});
  next.records[0].added_items=[{candidate_id:'next',task_id:'next-task',label:'后续恢复结果',course:null}];
  target.records.push({request_id:'legacy:failed',label:'旧失败',state:'failed',added:0,review:0,error:null,
    recovered:true,recovered_by:{run_id:next.run_id,request_id:next.records[0].request_id}});
  target.counts.total++;target.counts.failed++;
  const raw=clone(target);delete raw.records[1].recovered;delete raw.records[1].recovered_by;
  const f=fixture([next,target,old]);
  f.setAPI((call,normal)=>call.path===API+'/'+encodeURIComponent(target.run_id)?clone(raw):normal(call));
  await f.ui.load();await locate(f.ui,old);
  assert.equal((markup(f.ui).match(/data-source-sync-highlight/g)||[]).length,2);
  assert.deepEqual(f.messages,[]);
});

check('an expired descendant receipt does not hide other saved recovery results', async () => {
  const {old,target}=linkedRecovery();
  old.records=[];old.error={code:'collection_failed',message:'旧连接失败',retryable:true};
  old.recovered_by={run_id:target.run_id};old.counts={...old.counts,total:0,failed:0};
  target.records.push({request_id:'expired:failure',label:'没有产出的已恢复来源',state:'failed',added:0,review:0,error:null,
    recovered:true,recovered_by:{run_id:'expired:quiet',request_id:'quiet:source'}});
  target.counts.total++;target.counts.failed++;
  const f=fixture([target,old]);
  f.setAPI((call,normal)=>{if(call.path===API+'/expired%3Aquiet')throw {status:404};return normal(call);});
  await f.ui.load();await locate(f.ui,old);
  assert.match(markup(f.ui),/data-source-sync-highlight/);
  assert.deepEqual(f.revealed,[`sync-run-${target.run_id}`]);
  assert.deepEqual(f.messages,['部分恢复记录已无法查看，已保存的任务不受影响']);
  assert.equal(f.posts().length,0);
});
