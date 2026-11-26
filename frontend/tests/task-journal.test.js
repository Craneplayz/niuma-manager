import test from 'node:test';
import assert from 'node:assert/strict';
import 'fake-indexeddb/auto';
import { appendOperation, archiveQuickDrafts, readQuickDrafts, readOperations, removeOperations, clearJournal } from '../src/task-journal.js';
import example from '../../contracts/examples/task-writes-v01.json' with { type: 'json' };

const operation = (owner, id = crypto.randomUUID()) => ({ format: 1, id, owner, order: Date.now(),
  intent: { kind: 'save', draft: { title: '仅本账号可恢复的合成草稿', notes: '合成私密说明' } } });
test('upgrading a v1 journal preserves its encrypted draft through logout and isolates accounts', async () => {
  const op = operation('upgrade');
  const secret = await crypto.subtle.generateKey({name:'AES-GCM', length:256}, false, ['encrypt','decrypt']);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt({name:'AES-GCM', iv,
    additionalData:new TextEncoder().encode(op.owner + ':' + op.id)}, secret, new TextEncoder().encode(JSON.stringify(op)));
  await new Promise((resolve, reject) => {
    const request = indexedDB.open('niuma-task-journal-v1', 1);
    request.onupgradeneeded = () => {
      const keys = request.result.createObjectStore('keys');
      keys.put({generation:'old-key', secret}, op.owner);
      const operations = request.result.createObjectStore('operations', {keyPath:'id'});
      operations.createIndex('owner', 'owner');
      operations.add({id:op.id, owner:op.owner, iv, ciphertext});
    };
    request.onerror = () => reject(request.error);
    request.onsuccess = () => { request.result.close(); resolve(); };
  });
  assert.deepEqual(await readOperations(op.owner), [op]);
  await archiveQuickDrafts(op.owner, [op.id]);
  await clearJournal(op.owner);
  assert.deepEqual(await readOperations(op.owner), []);
  assert.deepEqual(await readQuickDrafts(op.owner), [op]);
  assert.deepEqual(await readQuickDrafts('other'), []);
  await clearJournal(op.owner, true);
  assert.deepEqual(await readQuickDrafts(op.owner), []);
});
test('the shared operation identity survives encryption and recovery without mutation', async () => {
  const record = { ...example.operation, format: 1, order: 1 };
  await appendOperation(record);
  assert.deepEqual(await readOperations(record.owner), [record]);
  await clearJournal(record.owner, true);
});
test('durable encrypted records recover after module reload and isolate accounts', async () => {
  const first = operation('alice'), second = operation('bob');
  await appendOperation(first); await appendOperation(second);
  const fresh = await import('../src/task-journal.js?fresh');
  assert.deepEqual(await fresh.readOperations('alice'), [first]);
  assert.deepEqual(await fresh.readOperations('bob'), [second]);
  const raw = await new Promise(resolve => {
    const request = indexedDB.open('niuma-task-journal-v1');
    request.onsuccess = () => {
      const db = request.result, tx = db.transaction(['operations', 'keys']);
      tx.objectStore('operations').get(first.id).onsuccess = e => resolve(e.target.result);
      tx.oncomplete = () => db.close();
    };
  });
  assert.equal(Object.hasOwn(raw, 'intent'), false);
  assert.equal(new TextDecoder().decode(raw.ciphertext).includes('合成私密'), false);
  await removeOperations('bob', [first.id]);
  assert.deepEqual(await readOperations('alice'), [first]);
  await assert.rejects(clearJournal('alice')); // Another window's unsent changes cannot be discarded by logout.
  await removeOperations('alice', [first.id]); await clearJournal('alice');
  assert.deepEqual(await readOperations('alice'), []);
  assert.deepEqual(await readOperations('bob'), [second]);
  await clearJournal('bob', true);
});
test('unavailable secure storage rejects staging instead of accepting an in-memory-only change', async () => {
  const original = globalThis.indexedDB;
  globalThis.indexedDB = undefined;
  try { await assert.rejects(appendOperation(operation('blocked')), /浏览器存储/); }
  finally { globalThis.indexedDB = original; }
  assert.deepEqual(await readOperations('blocked'), []);
});
