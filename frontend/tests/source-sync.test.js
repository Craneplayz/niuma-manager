import test from 'node:test';
import assert from 'node:assert/strict';
import {createSourceSyncUI} from '../src/source-sync-ui.js';
import {syncRun} from './sync-fixtures.js';

const API = '/api/sources/sync-runs';
const VERSION = 'source-sync/0.1';
const clone = value => structuredClone(value);
const check = (name, fn) => test(name, {timeout: 3000}, fn);
const button = (action, run, request) => ({dataset: {sourceSyncAction: action, sourceSyncRun: run, sourceSyncRequest: request}});
function deferred() {
  let resolve, reject;
  const wait = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {wait, resolve, reject};
}

function fixture(initial = []) {
  let account = 1, authenticated = true, demo = false, otherBusy = false;
  let runs = clone(initial), renders = 0, refreshes = 0, flushes = 0, handler = null, changed = null, flush = null;
  let serial = 0, timerId = 0, view = {tasks: [], batches: []};
  const calls = [], timers = new Map(), locks = [], callbacks = [];
  const remote = {tasks: [], batches: []};
  const defaultAPI = call => {
    if (call.path === '/api/workspace') return {tasks: clone(remote.tasks)};
    if (call.path === '/api/recognition/batches') return clone(remote.batches);
    if (call.path === API && call.method === 'GET') return {contract_version: VERSION, runs: clone(runs)};
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
    if (call.path.endsWith('/retry')) {
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
    ui, host, calls, remote, timers, locks, callbacks,
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
  assert.equal(f.ui.render(), ''); f.ui.render();
  assert.equal(f.calls.length, 0); assert.equal(f.timers.size, 0);
  f.setDemo(true); await f.ui.load(); await f.ui.start();
  assert.equal(f.calls.length, 0);
  f.setDemo(false); f.setAuthenticated(false); await f.ui.load(); await f.ui.start();
  assert.equal(f.calls.length, 0); assert.equal(f.ui.render(), '');
});

check('successful extraction keeps polling association and surfaces its failure without claiming completion', async () => {
  const run=syncRun({id:'run:association',state:'succeeded',states:['succeeded']});
  run.association={job_id:'job',state:'running',error:null,processed:0,failed:0};
  const f=fixture([run]);await f.ui.setVisible(true);
  assert.match(f.ui.render(),/识别完成，关联尚未完成/);
  assert.match(f.ui.render(),/当前列表尚未整合完毕/);
  assert.equal([...f.timers.values()][0].ms,2000);
  run.association={...run.association,state:'failed',error:'关联有一组未完成',processed:2,failed:1};
  f.setRuns([run]);await f.tick();
  assert.match(f.ui.render(),/关联有一组未完成/);
  assert.match(f.ui.render(),/跨来源关联中重试/);
  run.association.state='succeeded';run.association.error=null;f.setRuns([run]);await f.tick();
  assert.match(f.ui.render(),/跨来源关联已完成/);
  run.association.review=2;f.setRuns([run]);await f.tick();
  assert.match(f.ui.render(),/仍有 2 项关系需核对/);
  assert.doesNotMatch(f.ui.render(),/跨来源关联已完成/);
  f.ui.reset();assert.equal(f.timers.size,0);
});

check('entering restores active progress by GET and leaving pauses polling without stopping the server', async () => {
  const run = syncRun({state: 'recognizing', states: ['succeeded','running','queued'], added: 1}), f = fixture([run]);
  f.remote.tasks = [{id: 'saved-task'}];
  await f.ui.setVisible(true);
  assert.equal(f.ui.isBusy(), true); assert.equal(f.ui.ownsRequest('sync:0'), true);
  assert.match(f.ui.render(), /已处理 1 \/ 3/); assert.equal(f.view.tasks[0].id, 'saved-task');
  assert.equal([...f.timers.values()][0].ms, 2000);
  await f.tick(); assert.equal(f.posts().length, 0);
  const reads = f.sourceCalls().length;
  f.ui.setVisible(false); assert.equal(f.timers.size, 0); f.ui.render();
  assert.equal(f.sourceCalls().length, reads); assert.equal(f.ui.isBusy(), true);
  await f.ui.setVisible(true); assert.equal(f.sourceCalls().length, reads + 1);
  assert.equal(f.posts().length, 0);
});

check('unchanged progress neither refreshes the workspace again nor rerenders editable forms', async () => {
  const f = fixture([syncRun({added: 1})]);
  await f.ui.load(); const renders = f.renders, refreshes = f.refreshes;
  await f.ui.load(); f.ui.render();
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
  assert.equal(f.ui.isBusy(), false); assert.match(f.ui.render(), /任务修改尚未保存/);
});

check('a network-uncertain start reuses its operation id without render resubmissions', async () => {
  const f = fixture(); let attempts = 0;
  f.setAPI((call, normal) => {
    if (call.path === API && call.method === 'POST' && ++attempts === 1) throw new Error('offline');
    return normal(call);
  });
  assert.equal(await f.ui.start(), false);
  f.ui.render(); f.ui.render(); assert.equal(f.posts().length, 1);
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
    assert.equal(await f.ui.load(), false); assert.match(f.ui.render(), /结果已保存.*刷新失败/);
    assert.match(f.ui.render(), /新增 1 项/); assert.equal(f.ui.isBusy(), false);
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
  const polling = f.ui.load(); await f.ui.click(button('stop', run.run_id));
  assert.deepEqual(f.posts()[0].body, {contract_version: VERSION});
  assert.equal(f.posts()[0].path, `${API}/${encodeURIComponent(run.run_id)}/stop`); assert.equal(captured.signal.aborted, true);
  delayed.resolve({contract_version: VERSION, runs: [run]}); await polling;
  assert.match(f.ui.render(), /已停止导入，已保存结果保留/); assert.equal(f.ui.isBusy(), false);
  assert.equal(f.view.tasks[0].id, 'already-saved'); assert.equal(f.refreshes, 2);
});

check('account changes abort waiting requests and reject old responses and callback writes', async () => {
  const f = fixture(), delayed = deferred(); let captured;
  f.setAPI(call => { captured = call; return delayed.wait; });
  const reading = f.ui.load(); f.switchAccount();
  delayed.resolve({contract_version: VERSION, runs: [syncRun({state: 'recognizing', states: ['running']})]}); await reading;
  assert.equal(captured.signal.aborted, true); assert.equal(f.ui.render(), ''); assert.equal(f.ui.ownsRequest('sync:0'), false);
  const next = fixture([syncRun()]), refreshing = deferred(); let valid;
  next.setChanged(async current => { valid = current; await refreshing.wait; if (current()) next.remote.leaked = true; return current(); });
  const loading = next.ui.load(); await new Promise(resolve => setImmediate(resolve));
  next.switchAccount(); assert.equal(valid(), false); refreshing.resolve(); await loading;
  assert.equal(next.remote.leaked, undefined); assert.equal(next.ui.render(), ''); assert.equal(next.ui.isBusy(), false);
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
  assert.match(f.ui.render(), /后续已恢复 1 条/); assert.match(f.ui.render(), /继续未完成 · 1/);
  assert.match(f.ui.render(), /data-source-sync-request="sync:1"/);
  assert.doesNotMatch(f.ui.render(), /data-source-sync-request="sync:0"|data-source-sync-request="sync:2"/);
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
  const html = f.ui.render();
  assert.match(html, /data-source-sync-action="collect"/); assert.doesNotMatch(html, /data-source-sync-action="retry"/);
  assert.match(html, /重试识别不会补齐|修复模型配置/); assert.doesNotMatch(html, /<script>|story-|draft-card/);
  await f.ui.click(button('collect')); assert.equal(f.posts()[0].path, API);
});

check('progress connection errors preserve active state and retry polling without automatic writes', async () => {
  const f = fixture([syncRun({state: 'recognizing', states: ['running']})]); await f.ui.setVisible(true);
  f.setAPI(() => { throw new Error('offline'); }); await f.tick();
  assert.equal(f.ui.isBusy(), true); assert.match(f.ui.render(), /后台可能仍在运行/);
  assert.match(f.ui.render(), /停止导入/); assert.equal(f.posts().length, 0); assert.equal(f.timers.size, 1);
});

check('history and detail expansion survive polling and clear on account reset', async () => {
  const old = syncRun({id: 'run:older', state: 'failed', states: ['failed']}), newest = syncRun();
  old.records[0].request_id = 'older:source';
  const f = fixture([newest, old]); await f.ui.load();
  f.ui.toggle({target: {dataset: {sourceSyncHistory: ''}, open: true}});
  f.ui.toggle({target: {dataset: {sourceSyncDetails: old.run_id}, open: false}});
  await f.ui.load();
  assert.match(f.ui.render(), /data-source-sync-history open/);
  assert.doesNotMatch(f.ui.render(), /data-source-sync-details="run:older" open/);
  f.switchAccount(); assert.equal(f.ui.ownsRequest('older:source'), false); assert.equal(f.ui.render(), '');
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
  f.setRuns([stale]); await f.ui.load(); assert.equal(f.ui.isBusy(), false); assert.match(f.ui.render(), /新增 1 项/);
  f.setAPI(() => ({contract_version: 'source-sync/unknown', runs: []}));
  assert.equal(await f.ui.load(), false); assert.match(f.ui.render(), /版本不匹配/); assert.match(f.ui.render(), /新增 1 项/);
});

check('optional error formatting keeps technical recovery details folded', async () => {
  const f = fixture();
  f.host.describeError = cause => ({kind: 'crawler_dependency', message: '缺少教学网采集组件，请安装后重新采集。', details: cause.message.includes('crawler') ? '在 crawler/ 安装依赖（pnpm install）。' : ''});
  f.setAPI(() => { throw new Error('crawler dependency unavailable'); });
  await f.ui.start();
  const html = f.ui.render();
  assert.match(html, /缺少教学网采集组件/); assert.match(html, /<details class="import-error-details"><summary>查看原因/);
  assert.doesNotMatch(html, /<details class="import-error-details"[^>]*\bopen\b/);
});
