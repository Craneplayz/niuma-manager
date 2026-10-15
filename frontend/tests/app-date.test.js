import test from "node:test";
import assert from "node:assert/strict";

// A small DOM boundary for running the real controller with synthetic API data.
class Surface {
  listeners = new Map();
  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }
  dispatchEvent(event) {
    for (const listener of this.listeners.get(event.type) || []) listener(event);
    return true;
  }
  async emit(type, target = {}) {
    for (const listener of this.listeners.get(type) || [])
      await listener({ type, target, preventDefault() {} });
  }
}

class Element extends Surface {
  innerHTML = "";
  dataset = {};
  open = false;
  isConnected = true;
  classList = { add() {}, remove() {} };
  constructor(id = "") { super(); this.id = id; }
  close() { this.open = false; }
  showModal() { this.open = true; }
  focus() { document.activeElement = this; }
  setAttribute() {}
  removeAttribute() {}
  replaceChildren() { this.innerHTML = ""; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

let instance = 0;
async function fixture(t) {
  const instanceId = ++instance;
  t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: new Date("2026-10-15T23:59:59") });
  const app = new Element("app"), dialog = new Element("dialog"), drawer = new Element("task-drawer");
  const topDate = new Element(), results = new Element("task-results"), count = new Element("result-count");
  const elements = new Map([app, dialog, drawer, results, count, new Element("toast"), new Element("sidebar-tooltip"), new Element("page-title")].map(el => ["#" + el.id, el]));
  elements.set(".main-date", topDate);
  elements.set(".account-entry", new Element());
  drawer.querySelector = () => new Element();
  const doc = new Surface();
  doc.body = new Element();
  doc.activeElement = doc.body;
  doc.visibilityState = "visible";
  doc.querySelector = selector => elements.get(selector) || null;
  doc.getElementById = id => elements.get("#" + id) || null;
  doc.querySelectorAll = () => [];
  const win = new Surface();
  win.scrollTo = () => {};
  Object.assign(globalThis, {
    document: doc, window: win,
    location: new URL("http://synthetic.test/"),
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    CustomEvent: class { constructor(type) { this.type = type; } },
  });
  const tasks = [
    { id: "task", title: "合成任务", status: "not_started", due: { date: "2026-10-16", precision: "date" }, plans: [], subtasks: [] },
    { id: "notice", title: "合成通知", content_type: "notice", status: "not_started", due: { date: "2026-10-23", precision: "date" }, plans: [], subtasks: [] },
  ];
  const writes = [];
  globalThis.fetch = async (path, options = {}) => {
    let value = {};
    if (path === "/api/auth/me") value = { user: { id: "synthetic-" + instanceId, name: "合成账号" } };
    else if (path === "/api/workspace") value = { tasks: { version: 1, value: tasks } };
    else if (path === "/api/data/tasks") { writes.push(JSON.parse(options.body)); value = { version: writes.length + 1 }; }
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/recognition/batches") value = [];
    else if (path === "/api/sources/sync-runs") value = { contract_version: "source-sync/0.1", runs: [] };
    else assert.ok(["/api/version", "/api/sources/credentials", "/api/recognition/task-evidence"].includes(path), "unexpected synthetic request: " + path);
    return { ok: true, json: async () => structuredClone(value) };
  };
  await import(`../src/app.js?date-test=${instanceId}`);
  await new Promise(setImmediate);
  const click = dataset => doc.emit("click", { closest: () => ({ dataset }) });
  return { app, dialog, drawer, results, topDate, doc, win, writes, click };
}

test("midnight updates deadline groups, near notices and open read details", async t => {
  const f = await fixture(t);
  await f.click({ scope: "near" });
  assert.match(f.app.innerHTML, /明天截止/);
  assert.doesNotMatch(f.app.innerHTML, /合成通知/);
  await f.click({ taskOpen: "task" });
  assert.match(f.drawer.innerHTML, /明天/);
  t.mock.timers.tick(2000);
  assert.match(f.app.innerHTML, /datetime="2026-10-16"/);
  assert.match(f.app.innerHTML, /class="task-group-heading">今天<span>/);
  assert.match(f.app.innerHTML, /合成通知/);
  assert.match(f.drawer.innerHTML, /<strong>今天<\/strong>/);
});

test("an action after sleeping past midnight saves the actual completion and read day", async t => {
  const f = await fixture(t);
  t.mock.timers.setTime(new Date("2026-10-17T01:00:00").getTime());
  await f.click({ taskComplete: "task" });
  await f.doc.emit("change", { dataset: { noticeRead: "notice" } });
  await new Promise(setImmediate);
  assert.equal(f.writes.length, 2);
  for (const task of f.writes.at(-1).value) {
    assert.equal(task.status, "completed");
    assert.equal(task.completedAt, "2026-10-17");
  }
});

test("returning after a date change preserves filters, calendar position and an editor draft", async t => {
  const f = await fixture(t);
  await f.click({ view: "calendar" });
  await f.doc.emit("change", { id: "calendar-jump", value: "2026-12-01", dataset: {} });
  await f.doc.emit("input", { id: "task-search", value: "合成任务" });
  await f.click({ taskOpen: "task" });
  f.drawer.querySelector = () => ({ focus() {} });
  await f.click({ action: "drawer-edit" });
  f.drawer.innerHTML = "未保存的任务草稿";
  t.mock.timers.setTime(new Date("2026-10-18T09:00:00").getTime());
  await f.win.emit("focus");
  assert.match(f.app.innerHTML, /datetime="2026-10-18"/);
  assert.match(f.app.innerHTML, /value="2026-12-01"/);
  assert.match(f.app.innerHTML, /id="task-search"[^>]*value="合成任务"/);
  assert.equal(f.drawer.innerHTML, "未保存的任务草稿");
  await f.click({ action: "calendar-today" });
  assert.match(f.results.innerHTML, /data-calendar-day="2026-10-18"[^>]*aria-pressed="true"/);
});

test("visible restoration updates the date without replacing import or dialog drafts", async t => {
  const f = await fixture(t);
  await f.click({ page: "import" });
  await new Promise(setImmediate);
  f.app.innerHTML = "未提交的导入内容";
  f.dialog.innerHTML = "未提交的核对草稿";
  f.dialog.open = true;
  t.mock.timers.setTime(new Date("2026-10-19T09:00:00").getTime());
  await f.doc.emit("visibilitychange");
  assert.equal(f.topDate.dateTime, "2026-10-19");
  assert.match(f.topDate.textContent, /10月19日/);
  assert.equal(f.app.innerHTML, "未提交的导入内容");
  assert.equal(f.dialog.innerHTML, "未提交的核对草稿");
  t.mock.timers.tick(24 * 60 * 60 * 1000);
  assert.equal(f.topDate.dateTime, "2026-10-20");
  assert.equal(f.app.innerHTML, "未提交的导入内容");
  f.doc.visibilityState = "hidden";
  t.mock.timers.setTime(new Date("2026-10-21T09:00:00").getTime());
  await f.doc.emit("visibilitychange");
  assert.equal(f.topDate.dateTime, "2026-10-20");
  f.doc.visibilityState = "visible";
  await f.win.emit("pageshow");
  assert.equal(f.topDate.dateTime, "2026-10-21");
  assert.equal(f.app.innerHTML, "未提交的导入内容");
  assert.equal(f.dialog.innerHTML, "未提交的核对草稿");
});
