import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { taskEditSnapshot } from '../src/task-model.js';

let instance = 0;
const clone = value => structuredClone(value);
const reply = (value, status = 200) => ({ ok: status < 400, status, json: async () => clone(value) });
const task = (id, extra = {}) => ({ id, title: '合成任务 ' + id, description: '合成说明', course: null,
  content_type: 'task', status: 'not_started', completedAt: '', due: null, plans: [], subtasks: [], ...extra });
const candidate = id => ({ id, state: 'pending', version: 1, candidate: { title: '合成候选 ' + id,
  description: '合成核对说明', sources: [{ platform: 'manual' }] } });
const createBody = item => ({ action: 'create', version: item.version, reviewed: true,
  draft: { title: item.candidate.title, description: item.candidate.description, course: null, content_type: 'task', due: null } });
function deferred() {
  let resolve;
  return { promise: new Promise(done => { resolve = done; }), resolve: () => resolve() };
}
function replaceGlobal(t, name, descriptor) {
  const original = Object.getOwnPropertyDescriptor(globalThis, name);
  Object.defineProperty(globalThis, name, { configurable: true, ...descriptor });
  t.after(() => {
    if (original) Object.defineProperty(globalThis, name, original);
    else delete globalThis[name];
  });
}
function installBrowser(t, capabilities = {}) {
  const secure = Object.hasOwn(capabilities, 'secure') ? capabilities.secure : false;
  const { missingSubtle = true, missingIndexedDB = false } = capabilities;
  const accesses = { indexedDB: 0, localStorage: 0, sessionStorage: 0 };
  const httpCrypto = { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) };
  replaceGlobal(t, 'isSecureContext', { value: secure });
  replaceGlobal(t, 'crypto', { value: missingSubtle ? httpCrypto : webcrypto });
  replaceGlobal(t, 'indexedDB', { value: missingIndexedDB ? undefined : { open() {
    accesses.indexedDB++;
    throw new Error('This test forbids opening browser operation storage');
  } } });
  for (const name of ['localStorage', 'sessionStorage']) replaceGlobal(t, name, { get() {
    accesses[name]++;
    throw new Error('This test forbids plaintext operation storage');
  } });
  replaceGlobal(t, 'window', { value: new EventTarget() });
  if (!globalThis.CustomEvent) replaceGlobal(t, 'CustomEvent', { value: class extends Event { constructor(type) { super(type); } } });
  return accesses;
}
async function fixture(t, options = {}) {
  const accesses = installBrowser(t, options), ownerPrefix = 'online-owner-' + ++instance;
  let owner = ownerPrefix, hold = null, drop = null, forcedStatus = null, workspaceOwner = null;
  const databases = new Map(), requests = [], reconciliations = [], authRequests = [], gates = [];
  const makeDatabase = id => ({ tasks: [task(id === ownerPrefix ? 'existing' : 'other-account-task')],
    version: 1, generation: 0, candidates: new Map(['a', 'b'].map(id => [id, candidate(id)])), receipts: new Map(), applied: 0 });
  const dbFor = id => {
    if (!databases.has(id)) databases.set(id, makeDatabase(id));
    return databases.get(id);
  };
  dbFor(owner);
  const states = (db, ids = [...db.candidates.keys()]) => ids.map(id => {
    const item = db.candidates.get(id);
    return item ? { id, state: item.state, version: item.version, ...(item.taskId ? { taskId: item.taskId } : {}) }
      : { id, state: 'missing', version: 0 };
  });
  const response = (db, operation, replayed = false) => operation.intent.kind === 'review'
    ? { operationId: operation.id, replayed, generation: db.generation, tasks: { value: db.tasks, version: db.version }, candidates: states(db, [operation.intent.candidateId]) }
    : { operationId: operation.id, replayed, version: db.version, ...(replayed ? { value: db.tasks } : {}) };
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  t.after(() => { for (const gate of gates) gate.resolve(); });
  globalThis.fetch = async (path, request = {}) => {
    const body = request.body ? JSON.parse(request.body) : undefined, requestOwner = owner, db = dbFor(requestOwner);
    if (path === '/api/auth/me') return reply({ user: { id: requestOwner, name: '合成账号' } });
    if (path === '/api/auth/login' || path === '/api/auth/register') {
      authRequests.push({ path, body: clone(body) }); owner = body.email; dbFor(owner); return reply({});
    }
    if (path === '/api/auth/logout') { authRequests.push({ path }); return reply({}); }
    if (path === '/api/workspace') return reply({ taskWriteOwner: workspaceOwner || requestOwner,
      taskWriteGeneration: db.generation, tasks: { value: db.tasks, version: db.version } });
    if (path === '/api/tasks/reconcile') {
      reconciliations.push(clone(body));
      if (body.owner !== requestOwner || body.operations.some(operation => operation.owner !== requestOwner))
        return reply({ error: '账号操作不匹配' }, 409);
      if (body.operations.some(operation => operation.intent.workspaceGeneration !== db.generation))
        return reply({ error: '账号数据已清空，不能恢复清空前的操作' }, 409);
      return reply({ applied: body.operations.filter(operation => db.receipts.has(operation.id)).map(operation => operation.id),
        tasks: { value: db.tasks, version: db.version }, generation: db.generation,
        candidates: states(db, [...new Set(body.operations.filter(operation => operation.intent.kind === 'review').map(operation => operation.intent.candidateId))]) });
    }
    if (path !== '/api/data/tasks' && !path.startsWith('/api/recognition/candidates/')) throw new Error('Unexpected API: ' + path);
    const operation = body.operation, intent = operation.intent, waiting = hold;
    hold = null;
    requests.push({ path, body: clone(body), owner: requestOwner });
    if (waiting?.phase === 'before') { waiting.entered.resolve(); await waiting.gate.promise; }
    if (drop === 'before') { drop = null; throw new TypeError('synthetic disconnection before commit'); }
    if (forcedStatus) { const status = forcedStatus; forcedStatus = null; return reply({ error: '合成数据冲突' }, status); }
    if (operation.owner !== requestOwner || intent.workspaceGeneration !== db.generation)
      return reply({ error: '账号或清空代次不匹配' }, 409);
    if (db.receipts.has(operation.id)) {
      assert.deepEqual(operation, db.receipts.get(operation.id));
      return reply(response(db, operation, true));
    }
    if (path === '/api/data/tasks') {
      if (body.version !== db.version) return reply({ error: '任务已在其他页面变化' }, 409);
      db.tasks = clone(body.value); db.version++;
    } else {
      const item = db.candidates.get(intent.candidateId);
      if (!item || item.version !== body.version || item.state !== (body.action === 'reopen' ? 'ignored' : 'pending'))
        return reply({ error: '候选已在其他页面变化' }, 409);
      assert.equal(body.version, intent.expectedCandidateVersion);
      assert.equal(body.action, intent.action);
      if (body.action === 'create') {
        if (body.tasks_version !== db.version) return reply({ error: '目标任务版本已变化' }, 409);
        assert.equal(db.tasks.some(value => value.id === intent.newTaskId), false);
        db.tasks.push(task(intent.newTaskId, { ...clone(body.draft), source: '文本导入', createdAt: intent.createdAt,
          status: body.complete ? 'completed' : 'not_started', completedAt: body.complete ? intent.completedAt : '',
          recognitionHistory: [{ storageId: item.id, action: 'create' }] }));
        item.taskId = intent.newTaskId; db.version++;
      } else assert.ok(['ignore', 'reopen'].includes(body.action));
      item.state = body.action === 'ignore' ? 'ignored' : body.action === 'reopen' ? 'pending' : 'applied'; item.version++;
    }
    db.receipts.set(operation.id, clone(operation)); db.applied++;
    if (waiting?.phase === 'after') { waiting.entered.resolve(); await waiting.gate.promise; }
    if (drop === 'after') { drop = null; throw new TypeError('synthetic response lost after commit'); }
    return reply(response(db, operation));
  };
  const store = await import('../src/team-store.js?online-store=' + instance);
  await store.initSession(); store.acceptStoredCandidates([...dbFor(owner).candidates.values()]);
  return {
    store, accesses, requests, reconciliations, authRequests, databases,
    get owner() { return owner; }, get tasks() { return dbFor(owner).tasks; }, get version() { return dbFor(owner).version; },
    get applied() { return dbFor(owner).applied; }, get candidates() { return dbFor(owner).candidates; },
    holdNext(phase = 'after') {
      const gate = deferred(), entered = deferred(); gates.push(gate); hold = { phase, gate, entered };
      return { entered: entered.promise, release: gate.resolve };
    },
    dropNext(phase) { drop = phase; }, failNext(status) { forcedStatus = status; },
    switchAccount() { owner += '-other'; dbFor(owner); },
    clear() { const db = dbFor(owner); db.generation++; db.version++; db.tasks = []; db.candidates.clear(); },
    changeTask(values) { const db = dbFor(owner); Object.assign(db.tasks[0], values); db.version++; },
    changeCandidate(id, values) { Object.assign(dbFor(owner).candidates.get(id), values); },
    mismatchWorkspaceOwner(value) { workspaceOwner = value; },
  };
}
function assertNoStorage(f) {
  assert.deepEqual(f.accesses, { indexedDB: 0, localStorage: 0, sessionStorage: 0 });
}
function write(f, kind, id = 'a') {
  if (kind === 'review') {
    const item = candidate(id);
    return f.store.resolveStoredCandidate(item, { ...createBody(item), complete: true });
  }
  const current = f.store.readProjectedStore().tasks.find(value => value.id === 'existing');
  return f.store.setStoredTaskStatus(current.id, { status: 'done', completedAt: '2026-10-25' }, { status: current.status, completedAt: current.completedAt || '' });
}
function assertSaved(f, kind, update) {
  assert.equal(f.store.session.sync, 'saved');
  assert.equal(f.applied, 1); assert.equal(f.version, 2);
  if (kind === 'ordinary') assert.equal(f.store.readProjectedStore().tasks.find(value => value.id === 'existing').status, 'done');
  else {
    assert.equal(f.tasks.filter(value => value.id === update.taskId).length, 1);
    assert.equal(f.store.readProjectedStore().tasks.find(value => value.id === update.taskId).status, 'done');
    assert.equal(f.store.readStoredCandidates([candidate('a')])[0].state, 'applied');
  }
  assertNoStorage(f);
}

for (const kind of ['ordinary', 'review']) {
  test(`plain HTTP ${kind} writes wait for the server receipt without projecting or accessing storage`, async t => {
    const f = await fixture(t), gate = f.holdNext();
    assert.equal(f.store.taskWritesAreOnlineOnly(), true);
    assert.equal(typeof crypto.getRandomValues, 'function');
    assert.equal(crypto.subtle, undefined); assert.equal(crypto.randomUUID, undefined);
    let staged = false, changed = 0;
    window.addEventListener('niuma:review-store', () => changed++);
    const update = write(f, kind); update.staged.then(() => { staged = true; }).catch(() => {});
    await gate.entered;
    assert.equal(f.applied, 1, 'the server has committed while its receipt is held');
    assert.equal(staged, false); assert.equal(changed, 0);
    assert.equal(f.store.readProjectedStore().tasks.length, 1);
    assert.equal(f.store.readProjectedStore().tasks[0].status, 'todo');
    if (kind === 'review') {
      f.store.acceptStoredCandidates([...f.candidates.values()]);
      assert.equal(f.store.readStoredCandidates([candidate('a')])[0].state, 'pending');
      assert.equal(f.store.isTaskPendingCreate(update.taskId), false);
      assert.deepEqual(f.store.pendingStoredReviewIds(), []);
    }
    assertNoStorage(f);
    gate.release(); await update; const view = await update.staged;
    assert.equal(staged, true); assert.equal(changed, 1); assertSaved(f, kind, update);
    assert.equal(view.tasks.find(value => value.id === (update.taskId || 'existing')).status, 'done');
  });
}

for (const kind of ['ordinary', 'review']) for (const drop of ['before', 'after']) for (const recovery of ['retry', 'reconnect']) {
  test(`plain HTTP ${kind} ${drop}-commit disconnection recovers by ${recovery} with its original identity`, async t => {
    const f = await fixture(t); f.dropNext(drop);
    const update = write(f, kind);
    await assert.rejects(update, /无法连接服务/); await assert.rejects(update.staged, /无法连接服务/);
    assert.equal(f.store.session.sync, 'error');
    assert.equal(f.store.readProjectedStore().tasks.length, 1); assert.equal(f.store.readProjectedStore().tasks[0].status, 'todo');
    if (kind === 'review') assert.equal(f.store.readStoredCandidates([candidate('a')])[0].state, 'pending');
    const identity = clone(f.requests[0].body.operation);
    if (recovery === 'retry') await f.store.retrySave();
    else { await f.store.reconnectStore(); await f.store.flushStore(); }
    assert.ok(f.reconciliations.some(body => body.operations.some(operation => operation.id === identity.id)));
    assert.ok(f.reconciliations.every(body => body.operations.every(operation => operation.owner === f.owner)));
    assert.equal(f.requests.length, drop === 'before' ? 2 : 1);
    for (const request of f.requests) assert.deepEqual(request.body.operation, identity);
    if (kind === 'review') assert.equal(identity.intent.newTaskId, update.taskId);
    assertSaved(f, kind, update);
  });
}

test('plain HTTP reconnect retains the whole in-memory ordinary and review queue in order', async t => {
  const f = await fixture(t), gate = f.holdNext(); f.dropNext('after');
  const completing = write(f, 'ordinary'); await gate.entered;
  const confirming = write(f, 'review');
  let staged = false; confirming.staged.then(() => { staged = true; }).catch(() => {});
  assert.equal(staged, false); assert.equal(f.requests.length, 1);
  assert.equal(f.store.readStoredCandidates([candidate('a')])[0].state, 'pending');
  gate.release(); await assert.rejects(completing); await assert.rejects(confirming);
  await assert.rejects(completing.staged); await assert.rejects(confirming.staged);
  const ordinaryId = f.requests[0].body.operation.id;
  await f.store.reconnectStore(); await f.store.flushStore();
  const pending = f.reconciliations[0].operations;
  assert.deepEqual(pending.map(operation => operation.intent.kind), ['status', 'review']);
  assert.equal(pending[0].id, ordinaryId);
  assert.equal(f.requests.length, 2); assert.equal(f.requests[1].body.operation.id, pending[1].id);
  assert.equal(f.requests[1].body.operation.intent.newTaskId, confirming.taskId);
  assert.equal(f.requests[1].body.tasks_version, 2);
  assert.equal(f.tasks.find(value => value.id === 'existing').status, 'completed');
  assert.equal(f.tasks.find(value => value.id === confirming.taskId).status, 'completed');
  assert.equal(f.applied, 2); assert.equal(f.version, 3); assertNoStorage(f);
});

test('plain HTTP blocks another action for the same unacknowledged candidate', async t => {
  const f = await fixture(t), gate = f.holdNext('before'), item = candidate('a');
  const confirming = f.store.resolveStoredCandidate(item, createBody(item)); await gate.entered;
  for (const body of [createBody(item), { action: 'ignore', version: 1 }]) {
    const duplicate = f.store.resolveStoredCandidate(item, body);
    await assert.rejects(duplicate, /正在保存这项待核对/); await assert.rejects(duplicate.staged, /正在保存这项待核对/);
  }
  assert.equal(f.requests.length, 1); assert.equal(f.store.readProjectedStore().tasks.length, 1);
  gate.release(); await confirming; await confirming.staged;
  assert.equal(f.tasks.filter(value => value.id === confirming.taskId).length, 1); assertNoStorage(f);
});

test('plain HTTP keeps a failed candidate identity reserved until the original save is retried', async t => {
  const f = await fixture(t), item = candidate('a'); f.dropNext('before');
  const ignoring = f.store.resolveStoredCandidate(item, { action: 'ignore', version: 1 });
  await assert.rejects(ignoring); await assert.rejects(ignoring.staged);
  const duplicate = f.store.resolveStoredCandidate(item, { action: 'ignore', version: 1 });
  await assert.rejects(duplicate, /正在保存这项待核对/); await assert.rejects(duplicate.staged);
  await f.store.retrySave();
  assert.equal(f.requests.length, 2); assert.equal(f.requests[1].body.operation.id, f.requests[0].body.operation.id);
  const ignored = f.store.readStoredCandidates([item])[0]; assert.equal(ignored.state, 'ignored'); assert.equal(ignored.version, 2);
  const reopening = f.store.resolveStoredCandidate(ignored, { action: 'reopen', version: ignored.version });
  await reopening; await reopening.staged;
  assert.equal(f.store.readStoredCandidates([item])[0].state, 'pending'); assert.equal(f.candidates.get('a').version, 3); assertNoStorage(f);
});

for (const kind of ['ordinary', 'review']) {
  test(`plain HTTP account reconnect never replays another account's failed ${kind} operation`, async t => {
    const f = await fixture(t); f.dropNext('before'); const update = write(f, kind);
    await assert.rejects(update); await assert.rejects(update.staged);
    await assert.rejects(f.store.authenticate('login', { email: 'other', password: 'synthetic' }), /无法连接服务/);
    assert.equal(f.authRequests.length, 0, 'an explicit account switch cannot abandon an unresolved write');
    const originalOwner = f.owner, epoch = f.store.accountVersion(); f.switchAccount();
    const view = await f.store.reconnectStore(); await f.store.flushStore();
    assert.equal(f.store.session.user.id, f.owner); assert.notEqual(f.owner, originalOwner); assert.ok(f.store.accountVersion() > epoch);
    assert.deepEqual(view.tasks.map(value => value.id), ['other-account-task']);
    assert.equal(f.requests.length, 1); assert.equal(f.requests[0].body.operation.owner, originalOwner);
    assert.equal(f.reconciliations.length, 0); assert.equal(f.applied, 0); assertNoStorage(f);
  });
}

test('plain HTTP authentication waits for an old account receipt and clears no browser storage', async t => {
  const f = await fixture(t), gate = f.holdNext(), update = write(f, 'review'); await gate.entered;
  const oldOwner = f.owner, switching = f.store.authenticate('login', { email: 'other-account', password: 'synthetic' });
  await Promise.resolve(); assert.equal(f.store.session.changing, true); assert.equal(f.authRequests.length, 0);
  gate.release(); await update; await switching;
  assert.equal(f.store.session.user.id, 'other-account');
  assert.deepEqual(f.store.readProjectedStore().tasks.map(value => value.id), ['other-account-task']);
  assert.equal(f.databases.get(oldOwner).tasks.filter(value => value.id === update.taskId).length, 1);
  assert.equal(f.tasks.some(value => value.id === update.taskId), false);
  await f.store.logout(); assert.equal(f.store.session.user, null); assertNoStorage(f);
});

for (const kind of ['ordinary', 'review']) {
  test(`plain HTTP clearing account data prevents a failed ${kind} operation from being resurrected`, async t => {
    const f = await fixture(t); f.dropNext('before'); const update = write(f, kind);
    await assert.rejects(update); await assert.rejects(update.staged); f.clear();
    const view = await f.store.reconnectStore();
    assert.equal(view.tasks.length, 0); assert.equal(f.store.session.conflict, true); assert.equal(f.store.session.sync, 'error');
    assert.equal(f.requests.length, 1); assert.equal(f.reconciliations[0].operations[0].intent.workspaceGeneration, 0);
    await assert.rejects(f.store.retrySave(), /其他页面/);
    await f.store.reloadStore();
    assert.equal(f.store.session.conflict, false); assert.equal(f.store.readProjectedStore().tasks.length, 0);
    const creating = f.store.saveStoredTask({ id: 'after-clear', title: '清空后新建', notes: '', course: '', due: '', dueTime: '', status: 'todo' });
    await creating; await creating.staged;
    assert.equal(f.requests.length, 2); assert.equal(f.requests[1].body.operation.intent.workspaceGeneration, 1);
    assert.deepEqual(f.tasks.map(value => value.id), ['after-clear']); assertNoStorage(f);
  });
}

for (const kind of ['ordinary', 'review']) {
  test(`plain HTTP ${kind} conflicts require an explicit reread before another write`, async t => {
    const f = await fixture(t); f.failNext(409); const update = write(f, kind);
    await assert.rejects(update, /合成数据冲突/); await assert.rejects(update.staged);
    assert.equal(f.store.session.conflict, true); assert.equal(f.store.readProjectedStore().tasks[0].status, 'todo');
    await assert.rejects(f.store.retrySave(), /其他页面/); assert.equal(f.requests.length, 1);
    f.changeTask({ title: '其他页面保存的任务' });
    const view = await f.store.reloadStore();
    assert.equal(view.tasks[0].title, '其他页面保存的任务'); assert.equal(f.store.session.conflict, false);
    assert.equal(f.tasks.length, 1); assert.equal(f.applied, 0); assertNoStorage(f);
  });
}

test('plain HTTP workspace owner mismatch never installs another account snapshot', async t => {
  const f = await fixture(t); f.mismatchWorkspaceOwner('different-owner');
  await assert.rejects(f.store.reconnectStore(), /账号已切换/);
  assert.deepEqual(f.store.readProjectedStore().tasks.map(value => value.id), ['existing']);
  assert.equal(f.requests.length, 0); assertNoStorage(f);
});

for (const secure of [true, undefined]) for (const capability of ['subtle', 'indexedDB']) {
  test(`secure-context=${String(secure)} missing ${capability} fails closed rather than using online HTTP fallback`, async t => {
    const f = await fixture(t, { secure, missingSubtle: capability === 'subtle', missingIndexedDB: capability === 'indexedDB' });
    assert.equal(f.store.taskWritesAreOnlineOnly(), false);
    const ordinary = write(f, 'ordinary');
    await assert.rejects(ordinary.staged, /本地|暂存/); await assert.rejects(ordinary, /本地|暂存/);
    const review = write(f, 'review');
    await assert.rejects(review.staged, /本地|暂存/); await assert.rejects(review, /本地|暂存/);
    assert.equal(f.requests.length, 0); assert.equal(f.store.readProjectedStore().tasks[0].status, 'todo');
    assert.equal(f.store.readStoredCandidates([candidate('a')])[0].state, 'pending');
    assertNoStorage(f);
  });
}

test('a secure context with available WebCrypto and blocked IndexedDB still fails closed', async t => {
  // Browsing may report the storage failure; it must never downgrade to plaintext or online-only writes.
  const accesses = installBrowser(t, { secure: true, missingSubtle: false });
  const requests = [];
  const originalFetch = globalThis.fetch; t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async path => {
    if (path === '/api/auth/me') return reply({ user: { id: 'secure-blocked-idb' } });
    if (path === '/api/workspace') return reply({ tasks: { value: [task('existing')], version: 1 } });
    requests.push(path); throw new Error('Writes are forbidden when encrypted storage is unavailable');
  };
  const store = await import('../src/team-store.js?online-store=' + ++instance);
  await assert.rejects(store.initSession(), /本地|暂存/);
  assert.equal(store.taskWritesAreOnlineOnly(), false);
  const current = store.readProjectedStore().tasks[0];
  const saving = store.saveStoredTask({ ...current, title: '不可降级保存' }, taskEditSnapshot(current));
  await assert.rejects(saving.staged, /本地|暂存/); await assert.rejects(saving, /本地|暂存/);
  assert.equal(requests.length, 0); assert.ok(accesses.indexedDB > 0);
  assert.equal(accesses.localStorage, 0); assert.equal(accesses.sessionStorage, 0);
  assert.equal(store.readProjectedStore().tasks[0].title, '合成任务 existing');
});
