import test from "node:test";
import assert from "node:assert/strict";

globalThis.window = new EventTarget();
globalThis.CustomEvent ||= class extends Event {
  constructor(type) { super(type); }
};
const {
  api,
  authenticate,
  initSession,
  readStore,
  readCancelledTasks,
  readSavedTasks,
  saveStore,
  trashStoredTasks,
  restoreStoredTasks,
  setStoredTaskDeadline,
  flushStore,
  refreshStore,
  reloadStore,
  retrySave,
  session,
  accountVersion,
  getTaskVersion,
  logout,
} = await import("../src/team-store.js");

const reply = value => ({ ok: true, json: async () => structuredClone(value) });
const task = (id, overrides = {}) => ({
  id, title: "任务 " + id, description: "", course: null,
  status: "not_started", due: null, plans: [], subtasks: [], ...overrides,
});
function fixture(user, initialTasks, otherRows = {}) {
  let tasks = structuredClone(initialTasks), version = 1;
  const writes = [];
  globalThis.fetch = async (path, options = {}) => {
    if (path === "/api/auth/me") return reply({ user: { id: user, name: "测试" } });
    if (path === "/api/workspace") return reply({ ...otherRows, tasks: { value: tasks, version } });
    if (path === "/api/data/tasks" && options.method === "PUT") {
      const body = JSON.parse(options.body);
      assert.equal(body.version, version);
      for (const item of body.value) {
        assert.equal(typeof item.id, "string");
        assert.equal(typeof item.title, "string");
        assert.ok(Array.isArray(item.plans));
        assert.ok(Array.isArray(item.subtasks));
      }
      writes.push(body);
      tasks = structuredClone(body.value);
      return reply({ version: ++version });
    }
    throw new Error("unexpected request " + path);
  };
  return { writes, get tasks() { return tasks; } };
}

test("current task edits preserve server metadata and never write unrelated workspace rows", async () => {
  const original = [
    task("active", {
      title: "报告", description: "原文说明", course: "课程", source: "教学网",
      due: { date: "2026-09-30", precision: "date" },
      sourceUrl: "https://example.test/course", sourceRequirements: ["提交 PDF"],
      recognitionHistory: [{ candidateId: "c1" }],
      serverMetadata: { opaque: true },
      plans: [{ id: "server-entry", retained: true }],
      subtasks: [{ id: "server-step", title: "服务端字段", status: "pending", retained: true }],
    }),
    task("cancelled", { status: "cancelled", reason: "由通知取消" }),
  ];
  const f = fixture("edit", original, {
    courses: { value: [{ id: "course", name: "不由前端编辑" }], version: 4 },
    materials: { value: [{ id: "material", name: "不由前端编辑" }], version: 3 },
  });
  const state = await initSession();
  assert.deepEqual(Object.keys(state).sort(), ["tasks", "trash"]);
  assert.equal(state.tasks.length, 1);
  assert.deepEqual(readCancelledTasks(), [original[1]]);
  readCancelledTasks()[0].reason = "caller-only";
  assert.equal(getTaskVersion(), 1);
  assert.equal(Object.hasOwn(session.user, "display_name"), false);
  // Mutating a returned view must not alter the authoritative server task cache.
  state.tasks[0].serverMetadata.opaque = false;
  state.tasks[0].recognitionHistory[0].candidateId = "caller-only";
  state.tasks[0].plans[0].retained = false;
  state.tasks[0].subtasks[0].retained = false;
  Object.assign(state.tasks[0], { title: "更新后的报告", notes: "更新说明", course: "新课程", due: "2026-10-01", dueTime: "18:30" });
  assert.equal(saveStore(state), true);
  await flushStore();
  assert.equal(f.writes.length, 1);
  assert.equal(getTaskVersion(), 2);
  const saved = f.tasks.find(item => item.id === "active");
  assert.equal(saved.title, "更新后的报告");
  assert.equal(saved.description, "更新说明");
  assert.equal(saved.course, "新课程");
  assert.deepEqual(saved.due, { date: "2026-10-01", time: "18:30", precision: "datetime" });
  for (const name of ["source", "sourceUrl", "sourceRequirements", "recognitionHistory", "serverMetadata", "plans", "subtasks"]) {
    assert.deepEqual(saved[name], original[0][name]);
  }
  assert.deepEqual(f.tasks.find(item => item.id === "cancelled"), original[1]);
});

test("a new task supplies only the required task arrays without client scheduling fields", async () => {
  const f = fixture("new", []);
  const state = await initSession();
  state.tasks.push({ id: "new", title: "新任务", notes: "说明", course: "", due: "2026-10-14", dueTime: "", status: "todo", source: "手动添加" });
  saveStore(state);
  await flushStore();
  const saved = f.tasks[0];
  assert.deepEqual(saved.plans, []);
  assert.deepEqual(saved.subtasks, []);
  assert.deepEqual(saved.due, { date: "2026-10-14", precision: "date" });
  assert.equal(saved.source, "手动添加");
  assert.match(saved.createdAt, /^\d{4}-\d{2}-\d{2}T/);
  for (const name of ["hours", "estimatedMinutes", "dependsOn", "location", "owner"]) assert.equal(Object.hasOwn(saved, name), false);
  assert.deepEqual(readStore(), { tasks: [], trash: [] });
});

test("notice edits retain type and explicit dates through consecutive saves and conversion", async () => {
 const f=fixture('notice',[{...task('one'),content_type:'notice',planningExcluded:true}]);
 const state=await initSession();state.tasks[0].due='2026-10-23';saveStore(state);await flushStore();
 state.tasks[0].notes='第二次编辑';saveStore(state);await flushStore();assert.equal(f.tasks[0].content_type,'notice');assert.equal(f.tasks[0].planningExcluded,true);
 state.tasks[0].content_type='task';saveStore(state);await flushStore();assert.equal(f.tasks[0].planningExcluded,false);
 state.tasks[0].notes='转换后编辑';saveStore(state);await flushStore();assert.equal(f.tasks[0].planningExcluded,false);
 assert.equal(f.tasks[0].due.date,'2026-10-23');
});

test("editing and recycling notices retains content type and existing status without read or archive fields", async () => {
  const original = task("notice", { content_type: "notice", status: "completed", planningExcluded: true, completedAt: "2026-10-12" });
  const f = fixture("notice-preservation", [original]);
  const state = await initSession();
  state.tasks[0].title = "修改通知标题";
  saveStore(state); await flushStore();
  assert.equal(f.tasks[0].content_type, "notice");
  assert.equal(f.tasks[0].status, "completed");
  assert.equal(f.tasks[0].completedAt, original.completedAt);
  await trashStoredTasks(["notice"]); await restoreStoredTasks(["notice"]);
  assert.equal(f.tasks[0].content_type, "notice");
  assert.equal(f.tasks[0].status, "completed");
  for (const field of ["readAt", "archivedAt", "isRead"]) assert.equal(Object.hasOwn(f.tasks[0], field), false);
});

test("completion dates survive save and reload and clear when the task reopens", async () => {
  const f = fixture("completion", [task("task")]);
  let state = await initSession();
  state.tasks[0].status = "done";
  saveStore(state);
  await flushStore();
  state = await initSession();
  assert.equal(state.tasks[0].status, "done");
  assert.match(state.tasks[0].completedAt, /^\d{4}-\d{2}-\d{2}$/);
  state.tasks[0].status = "todo";
  saveStore(state);
  await flushStore();
  state = await initSession();
  assert.equal(state.tasks[0].completedAt, "");
  state.tasks[0].status = "done";
  state.tasks[0].completedAt = "2026-09-20";
  saveStore(state);
  await flushStore();
  assert.equal(f.tasks[0].completedAt, "2026-09-20");
});

test("notice read status persists across refreshes and can be reversed without changing its type or source", async () => {
  const original = task("readable-notice", { content_type: "notice", planningExcluded: true, source: "教学网", sourceUrl: "https://example.test/notice", recognitionHistory: [{ candidateId: "notice-source" }] });
  const f = fixture("notice-read-status", [original]);
  let state = await initSession();
  assert.equal(state.tasks[0].status, "todo");
  state.tasks[0].status = "done";
  saveStore(state); await flushStore();
  state = await refreshStore();
  assert.equal(state.tasks[0].status, "done");
  assert.equal(f.tasks[0].status, "completed");
  assert.match(f.tasks[0].completedAt, /^\d{4}-\d{2}-\d{2}$/);
  state.tasks[0].title = "已阅读后编辑通知";
  saveStore(state); await flushStore();
  assert.equal(f.tasks[0].status, "completed");
  state.tasks[0].status = "todo";
  saveStore(state); await flushStore();
  state = await refreshStore();
  assert.equal(state.tasks[0].status, "todo");
  assert.equal(state.tasks[0].completedAt, "");
  assert.equal(f.tasks[0].status, "not_started");
  for (const field of ["content_type", "planningExcluded", "source", "sourceUrl", "recognitionHistory"]) assert.deepEqual(f.tasks[0][field], original[field]);
  for (const field of ["readAt", "isRead"]) assert.equal(Object.hasOwn(f.tasks[0], field), false);
});

test("moving tasks to trash and restoring only changes their visibility and saved status", async () => {
  const original = [
    task("active", { status: "in_progress", source: "教学网", serverMetadata: { opaque: true }, plans: [{ id: "untouched" }], subtasks: [{ id: "untouched-step" }] }),
    task("done", { status: "completed", completedAt: "2026-10-01" }),
    task("other", { dependsOn: "active" }),
  ];
  const f = fixture("trash", original);
  const stale = await initSession();
  await trashStoredTasks(["active", "done"]);
  let state = await refreshStore();
  assert.deepEqual(state.tasks.map(item => item.id), ["other"]);
  assert.deepEqual(state.trash.map(item => item.status), ["doing", "done"]);
  const archived = f.tasks.find(item => item.id === "active");
  assert.deepEqual(Object.keys(archived.recycleBin).sort(), ["deletedAt", "status"]);
  assert.deepEqual(archived.plans, original[0].plans);
  assert.deepEqual(archived.subtasks, original[0].subtasks);
  assert.deepEqual(f.tasks.find(item => item.id === "other"), original[2]);
  stale.tasks.find(item => item.id === "other").title = "正常编辑";
  saveStore(stale);
  await flushStore();
  assert.ok(f.tasks.find(item => item.id === "active").recycleBin);
  await restoreStoredTasks(["active", "done"]);
  state = await refreshStore();
  assert.deepEqual(state.trash, []);
  for (const id of ["active", "done"]) assert.deepEqual(f.tasks.find(item => item.id === id), original.find(item => item.id === id));
  await assert.rejects(restoreStoredTasks(["active"]), /回收站任务不存在/);
});

test("save snapshots are serialized with the newest task version", async () => {
  const f = fixture("queue", [task("task")]);
  const state = await initSession();
  state.tasks[0].title = "第一次保存";
  saveStore(state);
  state.tasks[0].title = "第二次保存";
  saveStore(state);
  await flushStore();
  assert.deepEqual(f.writes.map(item => item.version), [1, 2]);
  assert.deepEqual(f.writes.map(item => item.value[0].title), ["第一次保存", "第二次保存"]);
  assert.equal(getTaskVersion(), 3);
  saveStore(state);
  await flushStore();
  assert.equal(f.writes.length, 2);
  assert.equal(getTaskVersion(), 3);
  assert.equal(session.sync, "saved");
});

test("an account reset aborts pending requests and rejects late results from the old account", async () => {
  fixture("old-account", [task("old-task"), task("old-cancelled", { status: "cancelled" })]);
  await initSession();
  assert.equal(readCancelledTasks().length, 1);
  const epoch = accountVersion();
  let release, started, signal;
  const waiting = new Promise(resolve => { release = resolve; });
  const entered = new Promise(resolve => { started = resolve; });
  globalThis.fetch = async (path, options = {}) => {
    if (path === "/api/slow") {
      signal = options.signal; started(); await waiting;
      return reply({ secret: "旧账号材料" });
    }
    if (path === "/api/auth/logout") return reply({ ok: true });
    throw new Error(path);
  };
  const late = api("/api/slow");
  await entered;
  const next = await logout();
  assert.deepEqual(next, { tasks: [], trash: [] });
  assert.deepEqual(readCancelledTasks(), []);
  assert.equal(getTaskVersion(), 0);
  assert.equal(accountVersion(), epoch + 1);
  assert.equal(signal.aborted, true);
  const rejected = assert.rejects(late, /账号已切换/);
  release();
  await rejected;
  fixture("new-account", [task("new-task")]);
  const state = await initSession();
  assert.equal(session.user.id, "new-account");
  assert.deepEqual(state.tasks.map(item => item.id), ["new-task"]);
});

test("saved task snapshots are isolated from callers and pending or failed drafts, and clear on account reset", async () => {
  const original = task("saved-snapshot", {
    due: { date: "2026-10-29", precision: "date" },
    plans: [{ id: "kept-plan", title: "已保存安排" }],
  });
  fixture("saved-snapshot-user", [original]);
  const state = await initSession();
  const expected = structuredClone(state.tasks);
  assert.deepEqual(readSavedTasks(), expected);
  const snapshot = readSavedTasks();
  snapshot[0].title = "调用者修改";
  snapshot[0].due = "2099-01-01";
  snapshot[0].plans[0].title = "调用者修改嵌套字段";
  snapshot.push(task("caller-only"));
  assert.deepEqual(readSavedTasks(), expected);

  const currentFetch = globalThis.fetch;
  let enter, release;
  const entered = new Promise(resolve => { enter = resolve; });
  const waiting = new Promise(resolve => { release = resolve; });
  globalThis.fetch = async (path, options = {}) => {
    if (path === "/api/data/tasks" && options.method === "PUT") {
      enter(); await waiting;
      return { ok: false, status: 503, json: async () => ({ error: "合成保存失败" }) };
    }
    if (path === "/api/auth/logout") return reply({});
    return currentFetch(path, options);
  };
  state.tasks[0].status = "done";
  state.tasks[0].completedAt = "2026-10-29";
  assert.equal(saveStore(state), true);
  await entered;
  assert.deepEqual(readSavedTasks(), expected);
  release();
  await assert.rejects(flushStore(), /合成保存失败/);
  assert.deepEqual(readSavedTasks(), expected);
  await reloadStore();
  await logout();
  assert.deepEqual(readSavedTasks(), []);
});

test("explicit reload recovers a conflict and never resurrects the discarded failed draft", async () => {
  let tasks = [
      {
        id: "reload-task",
        title: "原任务",
        status: "not_started",
        due: null,
        subtasks: [],
        plans: [],
      },
    ],
    version = 1;
  const writes = [];
  globalThis.fetch = async (path, options = {}) => {
    if (path === "/api/auth/me")
      return {
        ok: true,
        json: async () => ({ user: { id: "reload-conflict", name: "测试" } }),
      };
    if (path === "/api/workspace")
      return {
        ok: true,
        json: async () => ({
          tasks: { value: structuredClone(tasks), version },
        }),
      };
    if (path === "/api/data/tasks") {
      const body = JSON.parse(options.body);
      writes.push(body);
      if (writes.length === 1)
        return {
          ok: false,
          status: 409,
          json: async () => ({ error: "其他页面已修改" }),
        };
      assert.equal(body.version, version);
      tasks = body.value;
      return { ok: true, json: async () => ({ version: ++version }) };
    }
    throw new Error(path);
  };
  let state = await initSession();
  const epoch = accountVersion();
  state.tasks[0].title = "冲突的旧草稿";
  saveStore(state);
  await assert.rejects(flushStore(), /其他页面/);
  assert.equal(session.conflict, true);
  await assert.rejects(refreshStore(), /其他页面/);
  tasks[0].title = "另一个页面保存的任务";
  version = 2;
  state = await reloadStore();
  assert.equal(state.tasks[0].title, "另一个页面保存的任务");
  assert.equal(accountVersion(), epoch);
  assert.equal(session.conflict, false);
  assert.equal(session.sync, "saved");
  await retrySave();
  assert.equal(writes.length, 1);
  state.tasks[0].title = "重新读取后的新修改";
  saveStore(state);
  await flushStore();
  assert.equal(writes.length, 2);
  assert.equal(writes[1].version, 2);
  assert.equal(writes[1].value[0].title, "重新读取后的新修改");
  assert.equal(
    tasks.some((task) => task.title === "冲突的旧草稿"),
    false,
  );
});

test("failed reload keeps the pending draft available for a normal save retry", async () => {
  let tasks = [
      {
        id: "retry-task",
        title: "原任务",
        status: "not_started",
        due: null,
        subtasks: [],
        plans: [],
      },
    ],
    version = 1,
    failSave = true,
    failRead = false;
  globalThis.fetch = async (path, options = {}) => {
    if (path === "/api/auth/me")
      return {
        ok: true,
        json: async () => ({ user: { id: "reload-failure", name: "测试" } }),
      };
    if (path === "/api/workspace")
      return failRead
        ? { ok: false, status: 503, json: async () => ({ error: "读取失败" }) }
        : {
            ok: true,
            json: async () => ({
              tasks: { value: structuredClone(tasks), version },
            }),
          };
    if (path === "/api/data/tasks") {
      if (failSave)
        return {
          ok: false,
          status: 503,
          json: async () => ({ error: "保存失败" }),
        };
      const body = JSON.parse(options.body);
      assert.equal(body.version, version);
      tasks = body.value;
      return { ok: true, json: async () => ({ version: ++version }) };
    }
    throw new Error(path);
  };
  const state = await initSession();
  state.tasks[0].title = "需要保留的待保存内容";
  saveStore(state);
  await assert.rejects(flushStore(), /保存失败/);
  failRead = true;
  await assert.rejects(reloadStore(), /读取失败/);
  failSave = false;
  await retrySave();
  assert.equal(tasks[0].title, "需要保留的待保存内容");
  assert.equal(session.sync, "saved");
});

test("explicit reload waits for an in-flight write before reading the workspace", async () => {
  let tasks = [
      {
        id: "waiting-task",
        title: "原任务",
        status: "not_started",
        due: null,
        subtasks: [],
        plans: [],
      },
    ],
    version = 1,
    reads = 0,
    release,
    started;
  const waiting = new Promise((resolve) => (release = resolve)),
    entered = new Promise((resolve) => (started = resolve));
  globalThis.fetch = async (path, options = {}) => {
    if (path === "/api/auth/me")
      return {
        ok: true,
        json: async () => ({ user: { id: "reload-wait", name: "测试" } }),
      };
    if (path === "/api/workspace") {
      reads++;
      return {
        ok: true,
        json: async () => ({
          tasks: { value: structuredClone(tasks), version },
        }),
      };
    }
    if (path === "/api/data/tasks") {
      const body = JSON.parse(options.body);
      started();
      await waiting;
      tasks = body.value;
      return { ok: true, json: async () => ({ version: ++version }) };
    }
    throw new Error(path);
  };
  const state = await initSession();
  state.tasks[0].title = "先完成的保存";
  saveStore(state);
  await entered;
  const reloading = reloadStore();
  await Promise.resolve();
  assert.equal(reads, 1);
  release();
  const next = await reloading;
  assert.equal(reads, 2);
  assert.equal(next.tasks[0].title, "先完成的保存");
  assert.equal(session.sync, "saved");
});

test("a late workspace response cannot roll back an edit saved during refresh", async () => {
  const f = fixture("late-workspace", [task("task")]);
  const state = await initSession();
  const originalFetch = globalThis.fetch;
  let release, started, hold = true;
  const waiting = new Promise(resolve => { release = resolve; });
  const entered = new Promise(resolve => { started = resolve; });
  globalThis.fetch = async (path, options) => {
    const response = await originalFetch(path, options);
    if (path === "/api/workspace" && hold) {
      hold = false;
      const snapshot = await response.json();
      started(); await waiting;
      return reply(snapshot);
    }
    return response;
  };
  const refreshing = refreshStore();
  await entered;
  state.tasks[0].title = "刷新期间保存的新标题";
  saveStore(state); await flushStore();
  release();
  const next = await refreshing;
  assert.equal(next.tasks[0].title, "刷新期间保存的新标题");
  assert.equal(getTaskVersion(), 2);
  next.tasks[0].notes = "后续修改仍能保存";
  saveStore(next); await flushStore();
  assert.equal(f.tasks[0].title, "刷新期间保存的新标题");
  assert.equal(f.tasks[0].description, "后续修改仍能保存");
});

test("an older concurrent workspace read cannot replace a newer server version", async () => {
  fixture("concurrent-reads", [task("task")]);
  await initSession();
  let release, started, reads = 0;
  const waiting = new Promise(resolve => { release = resolve; });
  const entered = new Promise(resolve => { started = resolve; });
  globalThis.fetch = async path => {
    if (path === "/api/auth/me") return reply({ user: { id: "concurrent-reads" } });
    if (path === "/api/workspace") {
      if (++reads === 1) {
        started(); await waiting;
        return reply({ tasks: { value: [task("task", { title: "旧的读取" })], version: 1 } });
      }
      return reply({ tasks: { value: [task("task", { title: "新的服务端结果" })], version: 2 } });
    }
    throw new Error(path);
  };
  const older = refreshStore();
  await entered;
  const newer = await refreshStore();
  assert.equal(newer.tasks[0].title, "新的服务端结果");
  release();
  const next = await older;
  assert.equal(next.tasks[0].title, "新的服务端结果");
  assert.equal(getTaskVersion(), 2);
});

test("flush waits for task writes added while an earlier save is in flight", async () => {
  const f = fixture("flush-latest", [task("task")]);
  const state = await initSession(), originalFetch = globalThis.fetch;
  let startFirst, startSecond, releaseFirst, releaseSecond, writes = 0;
  const firstEntered = new Promise(resolve => { startFirst = resolve; });
  const secondEntered = new Promise(resolve => { startSecond = resolve; });
  const firstWait = new Promise(resolve => { releaseFirst = resolve; });
  const secondWait = new Promise(resolve => { releaseSecond = resolve; });
  globalThis.fetch = async (path, options) => {
    if (path === "/api/data/tasks") {
      if (++writes === 1) { startFirst(); await firstWait; }
      else { startSecond(); await secondWait; }
    }
    return originalFetch(path, options);
  };
  state.tasks[0].title = "第一次修改";
  saveStore(state); await firstEntered;
  let flushed = false;
  const flushing = flushStore().then(() => { flushed = true; });
  state.tasks[0].title = "第二次修改";
  saveStore(state); releaseFirst(); await secondEntered;
  await new Promise(resolve => setImmediate(resolve));
  const finishedEarly = flushed;
  releaseSecond(); await flushing; await flushStore();
  assert.equal(finishedEarly, false);
  assert.equal(f.tasks[0].title, "第二次修改");
});

test("saving an older task list cannot erase a task restored after that list was read", async () => {
  const f = fixture("restored-during-edit", [task("kept"), task("restored")]);
  await initSession();
  await trashStoredTasks(["restored"]);
  const older = await refreshStore();
  assert.deepEqual(older.tasks.map(item => item.id), ["kept"]);
  await restoreStoredTasks(["restored"]);
  older.tasks[0].title = "保留另一项任务的修改";
  saveStore(older); await flushStore();
  assert.ok(f.tasks.some(item => item.id === "restored" && !item.recycleBin));
  assert.equal(f.tasks.find(item => item.id === "kept").title, "保留另一项任务的修改");
});

test("retrying a failed trash write actually retries the pending deletion", async () => {
  const f = fixture("retry-trash", [task("task")]);
  await initSession();
  const originalFetch = globalThis.fetch;
  let fail = true;
  globalThis.fetch = async (path, options) => {
    if (path === "/api/data/tasks" && fail) {
      fail = false;
      return { ok: false, status: 503, json: async () => ({ error: "临时保存失败" }) };
    }
    return originalFetch(path, options);
  };
  await assert.rejects(trashStoredTasks(["task"]), /临时保存失败/);
  await assert.rejects(flushStore(), /临时保存失败/);
  await retrySave();
  assert.ok(f.tasks[0].recycleBin);
  assert.equal(f.tasks[0].status, "cancelled");
  assert.equal(session.sync, "saved");
});

test("retry restores the failed item before saving edits made after that failure", async () => {
  const f = fixture("retry-restore", [task("restored"), task("other")]);
  await initSession(); await trashStoredTasks(["restored"]);
  const older = await refreshStore(), originalFetch = globalThis.fetch;
  let fail = true;
  globalThis.fetch = async (path, options) => {
    if (path === "/api/data/tasks" && fail) {
      fail = false;
      return { ok: false, status: 503, json: async () => ({ error: "恢复暂时失败" }) };
    }
    return originalFetch(path, options);
  };
  await assert.rejects(restoreStoredTasks(["restored"]), /恢复暂时失败/);
  older.tasks[0].title = "后续标题修改";
  saveStore(older);
  await retrySave();
  assert.ok(f.tasks.some(item => item.id === "restored" && !item.recycleBin));
  assert.equal(f.tasks.find(item => item.id === "other").title, "后续标题修改");
});

test("explicit rereading discards a failed mutation instead of replaying it later", async () => {
  const f = fixture("discard-failed-trash", [task("task")]);
  await initSession();
  const originalFetch = globalThis.fetch;
  let fail = true;
  globalThis.fetch = async (path, options) => {
    if (path === "/api/data/tasks" && fail) {
      fail = false;
      return { ok: false, status: 503, json: async () => ({ error: "删除暂时失败" }) };
    }
    return originalFetch(path, options);
  };
  await assert.rejects(trashStoredTasks(["task"]), /删除暂时失败/);
  const state = await reloadStore();
  assert.equal(state.tasks.length, 1);
  await retrySave();
  assert.equal(f.writes.length, 0);
  assert.equal(f.tasks[0].recycleBin, undefined);
});

test('manual file tasks display their actual origin while preserving canonical server metadata',async()=>{
 const history=[{action:'create',requestId:'material:synthetic',storageId:'candidate:file'}];
 const f=fixture('file-origin',[task('file',{source:'微信',recognitionHistory:history}),task('chat',{source:'微信',recognitionHistory:[{action:'create',requestId:'wechat:synthetic'}]})]);
 const state=await initSession();
 assert.equal(state.tasks[0].source,'图片与文件');assert.equal(state.tasks[1].source,'微信');
 state.tasks[0].title='编辑后的文件任务';saveStore(state);await flushStore();
 assert.equal(f.tasks[0].source,'微信');assert.deepEqual(f.tasks[0].recognitionHistory,history);
 assert.equal((await refreshStore()).tasks[0].source,'图片与文件');
});


test("registration forwards the email challenge while login keeps its original request shape", async () => {
  const calls = [];
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ path, body });
    if (path === "/api/auth/register" || path === "/api/auth/login") return reply({});
    if (path === "/api/auth/me") return reply({ user: { id: "verified-registration", email: "synthetic@example.test", emailVerified: true } });
    if (path === "/api/workspace") return reply({ tasks: { value: [], version: 1 } });
    throw new Error("unexpected request " + path);
  };
  const credentials = { email: "synthetic@example.test", password: "Synthetic-2026!", displayName: "合成账号", challengeId: "synthetic-challenge", code: "123456" };
  await authenticate("register", credentials);
  assert.deepEqual(calls.find(call => call.path === "/api/auth/register").body, {
    email: credentials.email, password: credentials.password, name: credentials.displayName, challengeId: credentials.challengeId, code: credentials.code,
  });
  assert.equal(session.user.emailVerified, true);
  await authenticate("login", credentials);
  assert.deepEqual(calls.find(call => call.path === "/api/auth/login").body, { email: credentials.email, password: credentials.password });
});

test("API preserves top-level email error codes and retry intervals", async () => {
  globalThis.fetch = async () => ({ ok: false, status: 429, json: async () => ({ error: "验证码请求较多", code: "email_code_rate_limited", retryAfter: 120 }) });
  await assert.rejects(api("/api/auth/email-code", { purpose: "register", email: "synthetic@example.test" }), error => {
    assert.equal(error.code, "email_code_rate_limited");
    assert.equal(error.status, 429);
    assert.equal(error.retryAfter, 120);
    return true;
  });
});

test("calendar assignment and undo save only the date while preserving task metadata", async () => {
  const original = task("calendar", { description: "原文", sourceUrl: "https://example.test/course", plans: [{ id: "existing-plan" }], serverMetadata: { untouched: true } });
  const f = fixture("calendar-assign", [original]);
  await initSession();
  const empty = { due: "", dueTime: "" }, assigned = { due: "2026-10-20", dueTime: "" };
  let result = await setStoredTaskDeadline("calendar", assigned, empty);
  assert.equal(result.tasks[0].due, assigned.due);
  assert.deepEqual(f.tasks[0].due, { date: "2026-10-20", precision: "date" });
  for (const field of Object.keys(original).filter(key => key !== "due"))
    assert.deepEqual(f.tasks[0][field], original[field]);
  result.tasks[0].notes = "设置日期后补充的说明";
  saveStore(result); await flushStore();
  result = await setStoredTaskDeadline("calendar", empty, assigned);
  assert.equal(result.tasks[0].due, "");
  assert.equal(f.tasks[0].due, null);
  assert.equal(f.tasks[0].description, "设置日期后补充的说明");
});

test("a failed calendar drop stays undated and is never replayed by retry or a later edit", async () => {
  const f = fixture("calendar-failure", [task("calendar")]);
  const state = await initSession(), originalFetch = globalThis.fetch;
  let fail = true;
  globalThis.fetch = async (path, options) => {
    if (fail && path === "/api/data/tasks") {
      fail = false;
      return { ok: false, status: 503, json: async () => ({ error: "合成保存失败" }) };
    }
    return originalFetch(path, options);
  };
  await assert.rejects(setStoredTaskDeadline("calendar", { due: "2026-10-20", dueTime: "" }, { due: "", dueTime: "" }), /合成保存失败/);
  assert.equal(f.tasks[0].due, null);
  await retrySave();
  assert.equal(f.writes.length, 0);
  state.tasks[0].notes = "稍后修改说明";
  saveStore(state); await flushStore();
  assert.equal(f.tasks[0].due, null);
  await setStoredTaskDeadline("calendar", { due: "2026-10-21", dueTime: "" }, { due: "", dueTime: "" });
  assert.equal(f.tasks[0].due.date, "2026-10-21");
});

test("calendar changes reject stale dates, missing records and invalid dates without writing", async () => {
  const f = fixture("calendar-stale", [task("calendar", { due: { date: "2026-10-21", time: "18:00", precision: "datetime" } })]);
  await initSession();
  const empty = { due: "", dueTime: "" };
  await assert.rejects(setStoredTaskDeadline("calendar", empty, { due: "2026-10-20", dueTime: "" }), /任务日期已变化/);
  await assert.rejects(setStoredTaskDeadline("missing", { due: "2026-10-20", dueTime: "" }, empty), /任务日期已变化/);
  await assert.rejects(setStoredTaskDeadline("calendar", { due: "2026-02-30", dueTime: "" }, empty), /无效/);
  assert.equal(f.writes.length, 0);
});

test("a calendar version conflict requires reload and never queues the rejected drop", async () => {
  const f = fixture("calendar-conflict", [task("calendar")]);
  await initSession();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (path, options) => path === "/api/data/tasks"
    ? { ok: false, status: 409, json: async () => ({ error: "其他页面已修改" }) }
    : originalFetch(path, options);
  await assert.rejects(setStoredTaskDeadline("calendar", { due: "2026-10-20", dueTime: "" }, { due: "", dueTime: "" }), /其他页面/);
  assert.equal(session.conflict, true);
  await assert.rejects(retrySave(), /其他页面/);
  globalThis.fetch = originalFetch;
  const restored = await reloadStore();
  assert.equal(restored.tasks[0].due, "");
  assert.equal(f.writes.length, 0);
});

test("notice key date assignment, rescheduling and undo preserve notice and source metadata", async () => {
  const original = task("calendar-notice", {
    content_type: "notice", planningExcluded: true, source: "教学网", sourceUrl: "https://example.test/notice",
    recognitionHistory: [{ candidateId: "notice-source" }], description: "原文说明", serverMetadata: { untouched: true },
  });
  const f = fixture("calendar-notice", [original]);
  await initSession();
  const empty = { due: "", dueTime: "" }, assigned = { due: "2026-10-20", dueTime: "18:30" }, moved = { ...assigned, due: "2026-10-22" };
  await setStoredTaskDeadline(original.id, assigned, empty);
  let result = await setStoredTaskDeadline(original.id, moved, assigned);
  assert.equal(result.tasks[0].due, moved.due);
  assert.equal(result.tasks[0].dueTime, moved.dueTime);
  assert.deepEqual(f.tasks[0].due, { date: moved.due, time: "18:30", precision: "datetime" });
  for (const field of Object.keys(original).filter(key => key !== "due"))
    assert.deepEqual(f.tasks[0][field], original[field]);
  result = await setStoredTaskDeadline(original.id, assigned, moved);
  assert.equal(result.tasks[0].due, assigned.due);
  await setStoredTaskDeadline(original.id, empty, assigned);
  assert.equal(f.tasks[0].due, null);
  assert.equal(f.tasks[0].content_type, "notice");
  assert.equal(f.tasks[0].planningExcluded, true);
});
