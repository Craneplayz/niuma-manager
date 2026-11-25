import test from 'node:test';
import assert from 'node:assert/strict';
import 'fake-indexeddb/auto';
import { taskEditSnapshot } from '../src/task-model.js';

globalThis.window = new EventTarget();
globalThis.CustomEvent = class extends Event {
  constructor(type, options = {}) { super(type); this.detail = options.detail; }
};
const clone = value => structuredClone(value);
const reply = value => ({ ok: true, json: async () => clone(value) });
let serial = 0;
const fresh = () => import('../src/team-store.js?bulk-test=' + ++serial);
const gate = () => { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; };
function fixture({ block, loseResponse = false, offline = false } = {}) {
  const owner = 'bulk-owner-' + serial;
  let tasks = ['a', 'b'].map(id => ({ id, title: id, description: '', course: null, content_type: 'task',
    status: 'not_started', due: null, plans: [], subtasks: [] })), version = 1, first = true;
  const calls = [], receipts = new Set(), entered = gate();
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    if (path === '/api/auth/me') return reply({ user: { id: owner } });
    if (path === '/api/workspace') return reply({ taskWriteOwner: owner, tasks: { value: tasks, version } });
    if (path === '/api/tasks/reconcile') return reply({ applied: body.operations.filter(op => receipts.has(op.id)).map(op => op.id),
      tasks: { value: tasks, version }, candidates: [], generation: 0,
      bulkResults: body.operations.filter(op => receipts.has(op.id) && op.intent.kind === 'bulk').map(op => ({
        operationId: op.id, undo: { kind: 'bulk', action: 'undo', undoOf: op.id, workspaceGeneration: 0 },
      })) });
    calls.push({ path, body });
    if (path === '/api/tasks/bulk') {
      entered.release();
      if (block) await block.promise;
      if (offline && first) { first = false; throw new TypeError('offline'); }
      assert.equal(body.version, version);
      tasks[0].status = 'completed'; tasks[0].completedAt = body.operation.intent.completedAt;
      receipts.add(body.operation.id); version++;
      if (loseResponse && first) { first = false; throw new TypeError('response lost'); }
      return reply({ operationId: body.operation.id, value: tasks, version, candidates: [], batches: [],
        undo: { kind: 'bulk', action: 'undo', undoOf: body.operation.id, workspaceGeneration: 0 } });
    }
    if (path === '/api/data/tasks') {
      assert.equal(body.version, version);
      tasks = clone(body.value); version++; receipts.add(body.operation.id);
      return reply({ operationId: body.operation.id, version });
    }
    throw new Error('unexpected path: ' + path);
  };
  return { calls, entered: entered.promise, get tasks() { return tasks; } };
}

test('a slow bulk acknowledgement preserves a later unrelated staged edit and saves it next', async t => {
  const store = await fresh(), block = gate(); t.after(block.release);
  const f = fixture({ block }), state = await store.initSession();
  const bulk = store.bulkStoredTasks('complete', [state.tasks[0]]);
  await f.entered;
  assert.equal(store.pendingBulkTaskIds().has('a'), true);
  const b = state.tasks[1], edit = store.saveStoredTask({ ...b, title: 'later edit' }, taskEditSnapshot(b));
  await edit.staged;
  block.release();
  const result = await bulk;
  assert.equal(result.tasks.find(task => task.id === 'b').title, 'later edit');
  await edit;
  assert.equal(f.tasks[0].status, 'completed');
  assert.equal(f.tasks[1].title, 'later edit');
  assert.deepEqual(f.calls.map(call => call.path), ['/api/tasks/bulk', '/api/data/tasks']);
  assert.equal(f.calls[1].body.version, 2);
  assert.equal(store.pendingBulkTaskIds().size, 0);
});

test('a lost bulk response is reconciled without sending a second batch', async () => {
  const store = await fresh(), f = fixture({ loseResponse: true });
  const events = [];
  const collect = event => events.push(event.detail);
  window.addEventListener('niuma:bulk-saved', collect);
  const state = await store.initSession();
  await assert.rejects(store.bulkStoredTasks('complete', [state.tasks[0]]));
  assert.equal(store.pendingBulkTaskIds().has('a'), true);
  await store.retrySave();
  assert.equal(f.calls.length, 1);
  assert.equal(store.readProjectedStore().tasks[0].status, 'done');
  assert.equal(store.pendingBulkTaskIds().size, 0);
  assert.equal(events.at(-1).undo.undoOf, f.calls[0].body.operation.id);
  assert.ok(events.at(-1).affectedIds.includes('a'));
  window.removeEventListener('niuma:bulk-saved', collect);
});

test('a candidate change cannot queue behind a bulk write to its target', async t => {
  const store = await fresh(), block = gate(); t.after(block.release);
  const f = fixture({ block }), state = await store.initSession();
  const saving = store.bulkStoredTasks('complete', [state.tasks[0]]); await f.entered;
  await assert.rejects(store.resolveStoredCandidate({ id: 'change', version: 1 }, { action: 'postpone', target_id: 'a' }), /正在批量保存/);
  assert.equal(f.calls.length, 1);
  block.release(); await saving;
});

test('an unsent bulk operation survives a new store instance with its original identity', async () => {
  const store = await fresh(), f = fixture({ offline: true });
  const state = await store.initSession();
  await assert.rejects(store.bulkStoredTasks('complete', [state.tasks[0]]));
  const original = f.calls[0].body.operation;
  const reopened = await fresh(); await reopened.initSession(); await reopened.flushStore();
  assert.deepEqual(f.calls[1].body.operation, original);
  assert.equal(f.tasks[0].status, 'completed');
  assert.equal(reopened.pendingBulkTaskIds().size, 0);
});

test('bulk rejects change suggestions and oversized selections before staging any request', async () => {
  const store = await fresh(), f = fixture(); await store.initSession();
  await assert.rejects(store.bulkStoredTasks('complete', [{ id: 'review:x', reviewChange: true }]), /重新选择/);
  await assert.rejects(store.bulkStoredTasks('complete', Array.from({ length: 501 }, (_, i) => ({ id: String(i) }))), /500/);
  assert.equal(f.calls.length, 0);
  assert.equal(store.pendingBulkTaskIds().size, 0);
});
