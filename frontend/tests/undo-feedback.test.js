import test from 'node:test';
import assert from 'node:assert/strict';
import { createUndoFeedback } from '../src/undo-feedback.js';
function fixture(t) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 10000 });
  const classes = new Set(), attrs = new Map(), handlers = {};
  const button = {};
  const root = { innerHTML: '', style: { removeProperty() {} },
    classList: { add: (...values) => values.forEach(v => classes.add(v)), remove: (...values) => values.forEach(v => classes.delete(v)), contains: v => classes.has(v) },
    setAttribute: (key,value) => attrs.set(key,value), removeAttribute: key => attrs.delete(key),
    querySelector: () => button, matches: () => false, contains: () => false,
    getBoundingClientRect: () => ({ left: 0, right: 44, bottom: 44 }) };
  const body = { append(el) { el.parentElement = body; } };
  root.parentElement = body;
  const doc = { body, visibilityState: 'visible', querySelector: key => key === '#toast' ? root : null,
    addEventListener: (key,listener) => { handlers[key] = listener; } };
  const old = { document: globalThis.document, window: globalThis.window };
  Object.assign(globalThis, { document: doc, window: { addEventListener() {} } });
  t.after(() => Object.assign(globalThis, old));
  let epoch = 1;
  const errors = [];
  const undo = createUndoFeedback({ accountVersion: () => epoch, onError: e => errors.push(e.message) });
  return { undo, root, button, classes, doc, handlers, errors, epoch: value => { epoch = value; } };
}
test('undo icon uses escaped operation text and only the latest callback can run', async t => {
  const f = fixture(t); let first=0, second=0;
  f.undo.show({ message: '<script>', undo: () => first++ });
  assert.match(f.root.innerHTML, /&lt;script&gt;/);
  assert.match(f.root.innerHTML, /aria-label="撤回最近一次操作"/);
  const staleClick = f.button.onclick;
  f.undo.show({ undo: () => second++ });
  await staleClick();
  // The stale closure cannot invoke a callback or invalidate the latest entry.
  assert.equal(first, 0); assert.equal(second, 0);
  assert.equal(f.classes.has('undo-toast'), true);
  await f.button.onclick(); assert.equal(second, 1);
});
test('undo is single-use and rejects a changed account', async t => {
  const f = fixture(t); let called=0;
  f.undo.show({ undo: () => called++ });
  const click=f.button.onclick;
  await click(); await click();
  assert.equal(called, 1);
  f.undo.show({ undo: () => called++ }); f.epoch(2);
  await f.button.onclick(); assert.equal(called, 1);
});
test('undo expiry pauses while focused or hidden without extending elapsed time', t => {
  const f = fixture(t);
  f.undo.show({ undo() {} }); t.mock.timers.tick(5000);
  f.root.onfocusin(); t.mock.timers.tick(20000);
  assert.equal(f.classes.has('undo-toast'), true);
  f.root.onpointerleave(); t.mock.timers.tick(1999);
  assert.equal(f.classes.has('undo-toast'), true);
  t.mock.timers.tick(1); assert.equal(f.classes.has('undo-toast'), false);
});
test('undo failures report recovery once and old-account failures stay silent', async t => {
  const f = fixture(t);
  f.undo.show({ undo: async () => { throw Error('请重试'); } });
  await f.button.onclick(); assert.deepEqual(f.errors, ['请重试']);
  f.undo.show({ undo: async () => { f.epoch(2); throw Error('旧账号'); } });
  await f.button.onclick(); assert.deepEqual(f.errors, ['请重试']);
});
