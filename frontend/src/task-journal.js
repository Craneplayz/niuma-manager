// Local operations and recovered drafts share the account encryption key.
// IndexedDB transaction completion is the durability boundary before an optimistic UI update.
const DATABASE = 'niuma-task-journal-v1';
let database;
function open() {
  if (!globalThis.indexedDB || !globalThis.crypto?.subtle)
    return Promise.reject(new Error('无法安全暂存操作：请使用 HTTPS 或 localhost，并允许浏览器本地存储。任务尚未修改。'));
  return database ||= new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 2);
    request.onupgradeneeded = ({ oldVersion }) => {
      const db = request.result;
      if (oldVersion === 0) {
        db.createObjectStore('keys');
        db.createObjectStore('operations', { keyPath: 'id' }).createIndex('owner', 'owner');
      }
      db.createObjectStore('quickDrafts', { keyPath: 'id' }).createIndex('owner', 'owner');
    };
    request.onerror = () => { database = null; reject(request.error); };
    request.onblocked = () => { database = null; reject(new Error('本地暂存被其他页面阻止，请关闭旧页面后重试')); };
    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => { db.close(); database = null; };
      resolve(db);
    };
  });
}
async function transaction(stores, mode, action) {
  const db = await open();
  return new Promise((resolve, reject) => {
    let result, failure;
    const tx = db.transaction(stores, mode, mode === 'readwrite' ? { durability: 'strict' } : {});
    tx.oncomplete = () => resolve(result);
    tx.onerror = tx.onabort = () => reject(failure || tx.error || new Error('本地暂存事务失败'));
    action(tx, value => { result = value; }, error => { failure = error; tx.abort(); });
  });
}
async function key(owner) {
  const existing = await transaction(['keys'], 'readonly', (tx, done) => {
    tx.objectStore('keys').get(owner).onsuccess = e => done(e.target.result);
  });
  if (existing) return existing;
  const generated = { generation: crypto.randomUUID(), secret: await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']) };
  return transaction(['keys'], 'readwrite', (tx, done) => {
    const store = tx.objectStore('keys');
    store.get(owner).onsuccess = e => {
      const value = e.target.result || generated;
      if (!e.target.result) store.put(value, owner);
      done(value);
    };
  });
}
function storageError(cause) {
  const error = new Error('无法保存本地待办操作，请检查浏览器存储权限和剩余空间后重试。未暂存的操作不会修改任务。', { cause });
  error.code = 'task_journal_unavailable';
  return error;
}
export async function appendOperation(operation) {
  try {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = new TextEncoder().encode(JSON.stringify(operation));
    const vault = await key(operation.owner);
    const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv,
      additionalData: new TextEncoder().encode(operation.owner + ':' + operation.id) }, vault.secret, data);
    await transaction(['operations', 'keys'], 'readwrite', (tx) => {
      const store = tx.objectStore('operations');
      tx.objectStore('keys').get(operation.owner).onsuccess = e => {
        if (e.target.result?.generation !== vault.generation) { tx.abort(); return; }
        store.index('owner').count(operation.owner).onsuccess = e => {
          if (e.target.result >= 500) { tx.abort(); return; }
          store.add({ id: operation.id, owner: operation.owner, iv, ciphertext });
        };
      };
    });
  } catch (error) { throw storageError(error); }
}
export const readOperations = owner => readRecords(owner, 'operations');
export const readQuickDrafts = owner => readRecords(owner, 'quickDrafts');
async function readRecords(owner, storeName) {
  // Browsing is still possible when secure local storage is unavailable; writes fail closed.
  if (!globalThis.indexedDB || !globalThis.crypto?.subtle) return [];
  try {
    const records = await transaction([storeName], 'readonly', (tx, done) => {
      tx.objectStore(storeName).index('owner').getAll(owner).onsuccess = e => done(e.target.result);
    });
    if (!records.length) return [];
    const { secret } = await key(owner);
    const operations = await Promise.all(records.map(async record => {
      const bytes = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: record.iv,
        additionalData: new TextEncoder().encode(owner + ':' + record.id) }, secret, record.ciphertext);
      const operation = JSON.parse(new TextDecoder().decode(bytes));
      if (operation.owner !== owner || operation.id !== record.id || operation.format !== 1)
        throw new Error('无法读取待保存操作的版本或账号');
      return operation;
    }));
    return operations.sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  } catch (error) { throw storageError(error); }
}
// Move the existing ciphertext in one transaction. A failed move leaves the journal intact.
export async function archiveQuickDrafts(owner, ids) {
  await transaction(['operations', 'quickDrafts'], 'readwrite', tx => {
    const active = tx.objectStore('operations');
    for (const id of ids) active.get(id).onsuccess = ({target}) => {
      if (target.result?.owner !== owner) return;
      tx.objectStore('quickDrafts').put(target.result);
      active.delete(id);
    };
  });
}
export const removeOperations = (owner, ids) => removeRecords(owner, ids, 'operations');
export const removeQuickDraft = (owner, id) => removeRecords(owner, [id], 'quickDrafts');
async function removeRecords(owner, ids, storeName) {
  if (!ids.length) return;
  await transaction([storeName], 'readwrite', tx => {
    const store = tx.objectStore(storeName);
    for (const id of ids) store.get(id).onsuccess = e => {
      if (e.target.result?.owner === owner) store.delete(id);
    };
  });
}
export async function clearJournal(owner, discard = false) {
  if (!globalThis.indexedDB || !globalThis.crypto?.subtle) return;
  await transaction(['operations', 'quickDrafts', 'keys'], 'readwrite', (tx, _done, fail) => {
    const store = tx.objectStore('operations');
    store.index('owner').getAllKeys(owner).onsuccess = e => {
      if (e.target.result.length && !discard) { fail(new Error('其他页面仍有待保存操作，请先保存后再退出或切换账号。')); return; }
      for (const id of e.target.result) store.delete(id);
      const drafts = tx.objectStore('quickDrafts');
      drafts.index('owner').getAllKeys(owner).onsuccess = ({target}) => {
        if (!discard && target.result.length) return;
        for (const id of target.result) drafts.delete(id);
        tx.objectStore('keys').delete(owner);
      };
    };
  });
}
