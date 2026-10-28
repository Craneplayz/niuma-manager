import test from "node:test";
import assert from "node:assert/strict";
import {
  COMPANION_STORAGE_KEY,
  readCompanionPreferences,
  writeCompanionPreferences,
  countRemainingTasks,
  selectCompanionMessage,
} from "../src/completion-companion.js";

function memoryStorage(initial = null) {
  const values = new Map(initial ? [[COMPANION_STORAGE_KEY, initial]] : []);
  return {
    values,
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

test("companion preferences store only explicit visual choices and tolerate corrupt data", () => {
  const storage = memoryStorage();
  assert.deepEqual(readCompanionPreferences(storage), { enabled: true, animated: true });
  writeCompanionPreferences({ enabled: false, animated: true, title: "私人任务", user: "账号" }, storage);
  assert.deepEqual(readCompanionPreferences(storage), { enabled: false, animated: true });
  assert.deepEqual(JSON.parse(storage.values.get(COMPANION_STORAGE_KEY)), { enabled: false, animated: true });
  for (const corrupt of ["{", "null", "[]", '"string"', '{"enabled":"false","animated":0}']) {
    storage.values.set(COMPANION_STORAGE_KEY, corrupt);
    assert.deepEqual(readCompanionPreferences(storage), { enabled: true, animated: true });
  }
});

test("blocked browser storage cannot prevent completion feedback or preference changes", () => {
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.deepEqual(readCompanionPreferences(blocked), { enabled: true, animated: true });
  assert.doesNotThrow(() => writeCompanionPreferences({ enabled: false, animated: false }, blocked));
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  try {
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("security"); } });
    assert.doesNotThrow(() => readCompanionPreferences());
    assert.doesNotThrow(() => writeCompanionPreferences({ enabled: true, animated: false }));
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
    else delete globalThis.localStorage;
  }
});

test("remaining task count includes hidden actionable drafts but excludes notices, completed and removed work", () => {
  const tasks = [
    { id: "active", status: "todo" },
    { id: "in-progress", status: "in_progress" },
    { id: "review", status: "todo", reviewCandidateId: "pending-draft" },
    { id: "done-client", status: "done" },
    { id: "done-server", status: "completed" },
    { id: "cancelled", status: "cancelled" },
    { id: "trash", status: "todo", recycleBin: true },
    { id: "notice", status: "todo", content_type: "notice" },
  ];
  assert.equal(countRemainingTasks(tasks), 3);
  assert.equal(countRemainingTasks([]), 0);
});

test("all-done copy wins over merged count and never sends the user to another task", () => {
  for (const character of ["cow", "horse"]) {
    for (const count of [1, 3]) {
      const seen = new Set();
      for (let index = 0; index < 8; index++) {
        const message = selectCompanionMessage({ character, count, remaining: 0, index });
        assert.equal(message.character, character);
        assert.equal(message.count, count);
        assert.equal(message.mode, "all-done");
        assert.ok(message.line.length > 0);
        assert.doesNotMatch(message.line, /要不要.*下一|再.*下一|下一.*看看|下一个目标/);
        seen.add(message.id);
      }
      assert.ok(seen.size > 1);
    }
  }
});

test("normal and merged feedback keep the selected character and vary the authored dialogue", () => {
  for (const character of ["cow", "horse"]) {
    const ordinary = selectCompanionMessage({ character, count: 1, remaining: 4, index: 0 });
    const next = selectCompanionMessage({ character, count: 1, remaining: 4, index: 1 });
    const merged = selectCompanionMessage({ character, count: 3, remaining: 2, index: 0 });
    assert.equal(ordinary.mode, "ordinary");
    assert.equal(merged.mode, "batch");
    assert.equal(merged.count, 3);
    assert.equal(merged.character, character);
    assert.notEqual(ordinary.id, next.id);
    for (const message of [ordinary, next, merged]) {
      assert.doesNotMatch(message.line, /偷懒|才完成|别人都|终于|拖延|艰巨/);
      assert.ok([...message.line].length <= 24);
      assert.doesNotMatch(message.line, /\{[^}]+\}/);
    }
  }
});
