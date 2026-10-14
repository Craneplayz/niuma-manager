import test from "node:test";
import assert from "node:assert/strict";

globalThis.window = new EventTarget();
globalThis.CustomEvent ||= class extends Event {
  constructor(type) { super(type); }
};
const {
  api,
  initSession,
  readStore,
  saveStore,
  trashStoredTasks,
  restoreStoredTasks,
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
  for (const name of ["hours", "estimatedMinutes", "planningExcluded", "dependsOn", "location", "owner"]) assert.equal(Object.hasOwn(saved, name), false);
  assert.deepEqual(readStore(), { tasks: [], trash: [] });
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
  fixture("old-account", [task("old-task")]);
  await initSession();
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
