import test from 'node:test';
import assert from 'node:assert/strict';
import 'fake-indexeddb/auto';
import { taskEditSnapshot } from '../src/task-model.js';
import { readOperations } from '../src/task-journal.js';

let instance = 0;
const clone = value => structuredClone(value);
const reply = (value, status = 200) => ({ ok: status < 400, status, json: async () => clone(value) });
const task = (id, extra = {}) => ({ id, title: '任务 ' + id, description: '', course: null,
  content_type: 'task', status: 'not_started', completedAt: '', due: null, plans: [], subtasks: [], ...extra });
const snapshot = value => ({ title: value.title || '', description: value.description || '', course: value.course || null,
  content_type: value.content_type || 'task', due: value.due || null, status: value.status || 'not_started', completedAt: value.completedAt || '' });
const makeItem = id => ({ id, state: 'pending', version: 1, candidate: { title: '候选 ' + id,
  description: '核对说明', sources: [{ platform: 'teaching_web' }] } });
const createBody = (item, fields = {}) => ({ action: 'create', version: item.version, reviewed: true,
  draft: { title: item.candidate.title, description: item.candidate.description, course: null,
    content_type: 'task', due: null }, ...fields });
async function freshStore() {
  globalThis.window = new EventTarget();
  globalThis.CustomEvent ||= class extends Event { constructor(type) { super(type); } };
  return import('../src/team-store.js?review-store=' + ++instance);
}
async function fixture(t, { hold = false, holdReply = false, drop = null, initialTasks = [task('b')], items = [makeItem('a'), makeItem('c')] } = {}) {
  let owner = 'review-owner-' + instance, tasks = clone(initialTasks), version = 1, generation = 0, first = true;
  const candidates = new Map(items.map(item => [item.id, clone(item)])), receipts = new Set(), requests = [];
  let release, entered, releaseReply, enterCommit;
  const gate = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { entered = resolve; });
  const replyGate = new Promise(resolve => { releaseReply = resolve; });
  const committed = new Promise(resolve => { enterCommit = resolve; });
  t.after(release);
  t.after(releaseReply);
  function states(ids = [...candidates.keys()]) {
    return ids.map(id => { const item = candidates.get(id); return item
      ? { id, state: item.state, version: item.version, ...(item.taskId ? { taskId: item.taskId } : {}) }
      : { id, state: 'missing', version: 0 }; });
  }
  function response(operation, replayed = false) {
    return { operationId: operation.id, tasks: { value: tasks, version }, generation,
      candidates: states([operation.intent.candidateId]), replayed };
  }
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    if (path === '/api/auth/me') return reply({ user: { id: owner, name: owner } });
    if (path === '/api/workspace') return reply({ taskWriteOwner: owner, taskWriteGeneration: generation, tasks: { value: tasks, version } });
    if (path === '/api/tasks/reconcile') {
      if (body.operations.some(operation => operation.intent.workspaceGeneration !== generation))
        return reply({ error: '账号数据已清空，不能恢复清空前的操作' }, 409);
      return reply({ applied: body.operations.filter(op => receipts.has(op.id)).map(op => op.id),
        tasks: { value: tasks, version }, generation,
        candidates: states([...new Set(body.operations.filter(op => op.intent.kind === 'review').map(op => op.intent.candidateId))]) });
    }
    if (path === '/api/data/tasks') {
      requests.push({ path, body });
      assert.equal(body.version, version);
      assert.equal(body.operation.intent.kind === 'review', false);
      assert.equal(body.value.some(value => value.id.startsWith('review:')), false);
      tasks = clone(body.value); version++; receipts.add(body.operation.id);
      return reply({ version, operationId: body.operation.id });
    }
    if (path.startsWith('/api/recognition/candidates/')) {
      requests.push({ path, body });
      const operation = body.operation, intent = operation.intent;
      if (first) { first = false; entered(); if (hold) await gate; }
      if (receipts.has(operation.id)) return reply(response(operation, true));
      if (drop === 'before') { drop = null; throw new Error('合成离线'); }
      if (intent.workspaceGeneration !== generation) return reply({ error: '清空代次冲突' }, 409);
      const item = candidates.get(intent.candidateId);
      if (!item || item.state !== (body.action === 'reopen' ? 'ignored' : 'pending') || item.version !== body.version)
        return reply({ error: '候选已变化，请重新读取' }, 409);
      assert.equal(intent.expectedCandidateVersion, body.version);
      assert.equal(body.action, intent.action);
      if (!['ignore', 'reopen'].includes(body.action)) {
        if (body.tasks_version !== version) return reply({ error: '任务已变化，请重新核对' }, 409);
        let target = tasks.find(value => value.id === body.target_id);
        if (body.action === 'create') {
          target = task(intent.newTaskId, { ...body.draft, source: '教学网', createdAt: intent.createdAt,
            sourceRequirements: ['服务端来源要求'], serverMetadata: { retained: true } });
          assert.equal(tasks.some(value => value.id === target.id), false); tasks.push(target);
        } else {
          if (!target || JSON.stringify(snapshot(target)) !== JSON.stringify(intent.expectedTarget))
            return reply({ error: '目标任务已变化，请重新核对' }, 409);
          if (body.action === 'cancel') target.status = 'cancelled';
          else if (body.action === 'postpone') target.due = clone(body.draft.due);
          else Object.assign(target, body.draft);
        }
        if (body.complete) { target.status = 'completed'; target.completedAt = intent.completedAt; }
        target.recognitionHistory = [...(target.recognitionHistory || []), { storageId: item.id, action: body.action }];
        item.taskId = target.id; version++;
      }
      item.state = body.action === 'reopen' ? 'pending' : body.action === 'ignore' ? 'ignored' : 'applied'; item.version++;
      receipts.add(operation.id);
      enterCommit();
      if (drop === 'after') { drop = null; throw new Error('合成响应丢失'); }
      if (holdReply) await replyGate;
      return reply(response(operation));
    }
    throw new Error('Unexpected API: ' + path);
  };
  const store = await freshStore(); await store.initSession(); store.acceptStoredCandidates(items);
  return { store, requests, candidates, started, release, committed, releaseReply, get tasks() { return tasks; },
    get version() { return version; }, get owner() { return owner; },
    switchAccount() { owner += '-other'; tasks = [task('other-account-task')]; version = 1; },
    clear() { generation++; tasks = []; version++; candidates.clear(); },
    changeTarget(id, values) { Object.assign(tasks.find(value => value.id === id), values); version++; } };
}

test('review, ordinary edit and ignore share one ordered queue with durable optimistic state', async t => {
  const f = await fixture(t, { hold: true }); const { store } = f;
  const a = makeItem('a'), c = makeItem('c');
  const confirming = store.resolveStoredCandidate(a, createBody(a, { complete: true }));
  await confirming.staged; await f.started;
  const originalB = store.readProjectedStore().tasks.find(value => value.id === 'b');
  const editing = store.saveStoredTask({ ...originalB, title: '排队编辑 B' }, taskEditSnapshot(originalB));
  await editing.staged;
  const ignoring = store.resolveStoredCandidate(c, { action: 'ignore', version: 1 }); await ignoring.staged;
  assert.equal(f.requests.length, 1);
  assert.equal(store.readProjectedStore().tasks.find(value => value.id === confirming.taskId).status, 'done');
  assert.equal(store.readProjectedStore().tasks.find(value => value.id === confirming.taskId).pendingReviewCandidateId, 'a');
  assert.equal(store.readProjectedStore().tasks.find(value => value.id === 'b').title, '排队编辑 B');
  assert.equal(store.readStoredCandidates([c])[0].state, 'ignored');
  f.release(); await Promise.all([confirming, editing, ignoring]);
  assert.deepEqual(f.requests.map(request => request.body.operation.intent.kind), ['review', 'save', 'review']);
  assert.equal(f.requests[0].body.tasks_version, 1); assert.equal(f.requests[1].body.version, 2);
  assert.equal(f.tasks.find(value => value.id === 'b').title, '排队编辑 B');
  assert.equal((await readOperations(f.owner)).length, 0);
});

test('a newly confirmed task keeps its identity through an immediate edit, status change and deletion', async t => {
  const f = await fixture(t, { hold: true }); const { store } = f;
  const a = makeItem('a'), confirming = store.resolveStoredCandidate(a, createBody(a)); await confirming.staged; await f.started;
  const projected = store.readProjectedStore().tasks.find(value => value.id === confirming.taskId);
  const editing = store.saveStoredTask({ ...projected, title: '新任务继续编辑' }, taskEditSnapshot(projected)); await editing.staged;
  const completed = store.setStoredTaskStatus(projected.id, { status: 'done', completedAt: '2026-10-23' }, { status: 'todo', completedAt: '' }); await completed.staged;
  const deleted = store.trashStoredTasks([projected.id]); await deleted.staged;
  assert.equal(store.readProjectedStore().trash[0].id, projected.id);
  f.release(); await Promise.all([confirming, editing, completed, deleted]);
  const final = f.tasks.find(value => value.id === projected.id);
  assert.equal(final.title, '新任务继续编辑'); assert.equal(final.recycleBin.status, 'completed');
  assert.deepEqual(final.serverMetadata, { retained: true });
  assert.equal(Object.hasOwn(final, 'pendingReviewCandidateId'), false);
  assert.equal(store.isTaskPendingCreate(projected.id), false);
  assert.deepEqual(final.sourceRequirements, ['服务端来源要求']);
  assert.equal(final.recognitionHistory.filter(value => value.storageId === a.id).length, 1);
  assert.equal(f.tasks.filter(value => value.id === projected.id).length, 1);
});

test('a fresh candidate read ahead of its task receipt preserves the created task and following edit', async t => {
  const f = await fixture(t, { holdReply: true }), { store } = f, a = makeItem('a');
  const confirming = store.resolveStoredCandidate(a, createBody(a));
  await confirming.staged; await f.committed;
  // The GET observes the committed candidate while this browser still lacks its task receipt.
  store.acceptStoredCandidates([...f.candidates.values()]);
  const projected = store.readProjectedStore().tasks.find(value => value.id === confirming.taskId);
  assert.ok(projected, 'the durable created task must not disappear between independent responses');
  assert.equal(projected.pendingReviewCandidateId, a.id);
  assert.equal(store.readStoredCandidates([a])[0].state, 'applied');
  const editing = store.saveStoredTask({ ...projected, title: '回执等待期间继续编辑' }, taskEditSnapshot(projected));
  await editing.staged;
  store.acceptStoredCandidates([...f.candidates.values()]);
  assert.equal(store.readProjectedStore().tasks.find(value => value.id === confirming.taskId).title, '回执等待期间继续编辑');
  f.releaseReply(); await Promise.all([confirming, editing]);
  assert.equal(f.tasks.filter(value => value.id === confirming.taskId).length, 1);
  assert.equal(f.tasks.find(value => value.id === confirming.taskId).title, '回执等待期间继续编辑');
  assert.equal(f.version, 3);
  assert.equal((await readOperations(f.owner)).length, 0);
});

test('an independent conflicting candidate read cannot erase the local draft or bypass server validation', async t => {
  const f = await fixture(t, { hold: true }), { store } = f, a = makeItem('a');
  const confirming = store.resolveStoredCandidate(a, createBody(a));
  await confirming.staged; await f.started;
  Object.assign(f.candidates.get('a'), { state: 'ignored', version: 2 });
  store.acceptStoredCandidates([...f.candidates.values()]);
  assert.equal(store.readProjectedStore().tasks.some(value => value.id === confirming.taskId), true);
  f.release(); await assert.rejects(confirming, /候选已变化/);
  assert.equal(store.session.conflict, true);
  assert.equal(f.tasks.some(value => value.id === confirming.taskId), false);
  assert.equal((await readOperations(f.owner)).length, 1);
  await assert.rejects(store.retrySave(), /其他页面/);
  assert.equal(f.requests.length, 1);
});

test('a missing candidate tombstone survives a late old batch response', async t => {
  const f = await fixture(t), a = makeItem('a');
  f.store.acceptStoredCandidates([{ id: a.id, state: 'missing', version: 0 }], { authoritative: true });
  f.store.acceptStoredCandidates([a, { ...a, state: 'applied', version: 2 }]);
  assert.equal(f.store.readStoredCandidates([a])[0].state, 'missing');
  assert.equal(f.store.readStoredCandidates([a])[0].version, 0);
  assert.equal(f.requests.length, 0);
});

test('legacy whole-workspace saves reject pending or stale candidate projections without a PUT', async t => {
  const f = await fixture(t, { hold: true }), { store } = f, a = makeItem('a');
  const confirming = store.resolveStoredCandidate(a, createBody(a));
  await confirming.staged; await f.started;
  const projected = store.readProjectedStore();
  assert.equal(store.saveStore(projected), false);
  assert.match(store.session.error, /单条任务操作/);
  // Even a snapshot that omits the candidate task could carry a projected update to an existing target.
  assert.equal(store.saveStore({ tasks: [projected.tasks.find(value => value.id === 'b')], trash: [] }), false);
  assert.equal(f.requests.length, 1);
  f.release(); await confirming;
  assert.equal(store.saveStore(projected), false);
  assert.equal(store.saveStore({ tasks: [{ ...projected.tasks[0], id: 'review:stale' }], trash: [] }), false);
  assert.equal(store.saveStore({ tasks: [], trash: [{ ...projected.tasks[0], reviewCandidateId: 'a' }] }), false);
  await store.flushStore();
  assert.equal(f.requests.length, 1);
  assert.equal(f.tasks.filter(value => value.id === confirming.taskId).length, 1);
  assert.equal(Object.hasOwn(f.tasks.find(value => value.id === confirming.taskId), 'pendingReviewCandidateId'), false);
});

test('a committed review with a lost response is acknowledged after reopening without a second mutation', async t => {
  const f = await fixture(t, { drop: 'after' }); const a = makeItem('a');
  const confirming = f.store.resolveStoredCandidate(a, createBody(a)); await confirming.staged;
  await assert.rejects(confirming, /响应丢失/);
  assert.equal((await readOperations(f.owner)).length, 1);
  const restored = await freshStore(); const view = await restored.initSession(); await restored.flushStore();
  assert.equal(view.tasks.filter(value => value.id === confirming.taskId).length, 1);
  assert.equal(f.requests.length, 1); assert.equal(f.version, 2);
  assert.equal((await readOperations(f.owner)).length, 0);
});

test('an uncommitted review recovers with the original operation and task identities', async t => {
  const f = await fixture(t, { drop: 'before' }); const a = makeItem('a');
  const confirming = f.store.resolveStoredCandidate(a, createBody(a)); await confirming.staged;
  await assert.rejects(confirming, /离线/);
  const firstId = f.requests[0].body.operation.id;
  const restored = await freshStore(); await restored.initSession(); await restored.flushStore();
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[1].body.operation.id, firstId);
  assert.equal(f.requests[1].body.operation.intent.newTaskId, confirming.taskId);
  assert.equal(f.tasks.filter(value => value.id === confirming.taskId).length, 1);
  assert.equal((await readOperations(f.owner)).length, 0);
});

test('ignore, undo ignore and ignore again use projected candidate versions in one queue', async t => {
  const f = await fixture(t, { hold: true }); const { store } = f, c = makeItem('c');
  const ignoring = store.resolveStoredCandidate(c, { action: 'ignore', version: 1 }); await ignoring.staged;
  let projected = store.readStoredCandidates([c])[0]; assert.equal(projected.version, 2);
  const reopening = store.resolveStoredCandidate(projected, { action: 'reopen', version: projected.version }); await reopening.staged;
  projected = store.readStoredCandidates([c])[0]; assert.equal(projected.version, 3);
  const again = store.resolveStoredCandidate(projected, { action: 'ignore', version: projected.version }); await again.staged;
  f.release(); await Promise.all([ignoring, reopening, again]);
  assert.deepEqual(f.requests.map(request => request.body.version), [1, 2, 3]);
  assert.equal(f.candidates.get('c').state, 'ignored'); assert.equal(f.candidates.get('c').version, 4);
  assert.equal(f.version, 1); assert.equal((await readOperations(f.owner)).length, 0);
});

test('a conflicting candidate target remains on the server and its local review stays recoverable', async t => {
  const f = await fixture(t, { hold: true }); const c = makeItem('c');
  const update = f.store.resolveStoredCandidate(c, { action: 'update', version: 1, target_id: 'b', reviewed: true,
    draft: { title: '本地更新', description: '', course: null, content_type: 'task', due: null } });
  await update.staged; await f.started; f.changeTarget('b', { title: '其他窗口更新' }); f.release();
  await assert.rejects(update, /任务已变化/);
  assert.equal(f.tasks[0].title, '其他窗口更新'); assert.equal(f.store.session.conflict, true);
  assert.equal((await readOperations(f.owner)).length, 1);
});

test('a review cannot change the visible state before browser storage commits', async t => {
  const f = await fixture(t); const previous = globalThis.indexedDB;
  try {
    globalThis.indexedDB = undefined;
    const update = f.store.resolveStoredCandidate(makeItem('a'), createBody(makeItem('a')));
    await assert.rejects(update.staged, /本地|暂存/); await assert.rejects(update);
    assert.equal(f.requests.length, 0); assert.equal(f.store.readProjectedStore().tasks.length, 1);
    assert.equal(f.store.readStoredCandidates([makeItem('a')])[0].state, 'pending');
  } finally { globalThis.indexedDB = previous; }
});

test('another account never receives the first account durable review operations', async t => {
  const f = await fixture(t, { drop: 'before' }); const a = makeItem('a');
  const update = f.store.resolveStoredCandidate(a, createBody(a)); await update.staged; await assert.rejects(update);
  const originalOwner = f.owner; f.switchAccount();
  const restored = await freshStore(); const view = await restored.initSession(); await restored.flushStore();
  assert.deepEqual(view.tasks.map(value => value.id), ['other-account-task']);
  assert.equal(f.requests.length, 1); assert.equal((await readOperations(originalOwner)).length, 1);
});

test('clearing account data prevents an old journal from recreating confirmed or ignored candidates', async t => {
  const f = await fixture(t, { drop: 'before' }); const c = makeItem('c');
  const update = f.store.resolveStoredCandidate(c, { action: 'ignore', version: 1 }); await update.staged; await assert.rejects(update);
  f.clear(); const restored = await freshStore(); const view = await restored.initSession();
  assert.equal(view.tasks.length, 0); assert.equal(restored.session.conflict, true);
  assert.equal(f.requests.length, 1); assert.equal((await readOperations(f.owner)).length, 1);
  await restored.reloadStore(); assert.equal((await readOperations(f.owner)).length, 0);
});


test('update, postpone and cancel derive later target snapshots and workspace versions from the ordered queue', async t => {
  const items = ['u','p','x'].map(makeItem);
  const f = await fixture(t, { hold: true, items }); const { store } = f;
  const update = store.resolveStoredCandidate(items[0], { action: 'update', version: 1, target_id: 'b', reviewed: true,
    draft: { title: '先更新标题', description: '新说明', course: '课程', content_type: 'task', due: null } }); await update.staged;
  const postpone = store.resolveStoredCandidate(items[1], { action: 'postpone', version: 1, target_id: 'b', reviewed: true,
    draft: { title: '先更新标题', description: '新说明', course: '课程', content_type: 'task',
      due: { date: '2026-11-01', precision: 'date' } } }); await postpone.staged;
  const cancel = store.resolveStoredCandidate(items[2], { action: 'cancel', version: 1, target_id: 'b', reviewed: true }); await cancel.staged;
  assert.equal(store.readProjectedStore().tasks.length, 0);
  assert.equal(store.readCancelledTasks()[0].due.date, '2026-11-01');
  f.release(); await Promise.all([update, postpone, cancel]);
  assert.deepEqual(f.requests.map(request => request.body.tasks_version), [1,2,3]);
  assert.equal(f.tasks[0].title, '先更新标题'); assert.equal(f.tasks[0].due.date, '2026-11-01');
  assert.equal(f.tasks[0].status, 'cancelled'); assert.equal((await readOperations(f.owner)).length, 0);
});
