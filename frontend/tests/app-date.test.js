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
  async emit(type, target = {}, values = {}) {
    const event = { type, target, ...values, preventDefault() {}, stopImmediatePropagation() { this.stopped = true; } };
    for (const listener of this.listeners.get(type) || []) {
      await listener(event);
      if (event.stopped) break;
    }
  }
}

class Element extends Surface {
  innerHTML = "";
  dataset = {};
  open = false;
  isConnected = true;
  classList = { add() {}, remove() {} };
  style = {};
  offsetWidth = 250;
  offsetHeight = 60;
  append() {}
  remove() {}
  contains() { return true; }
  setPointerCapture() {}
  hasPointerCapture() { return false; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 20 }; }
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
async function fixture(t, { undated = false, undatedNotice = false, failWrite = false, deadlineTime = "", noticeTime = "", noticeRead = false, url = "http://synthetic.test/" } = {}) {
  const instanceId = ++instance;
  t.mock.timers.enable({ apis: ["Date", "setTimeout"], now: new Date("2026-10-15T23:59:59") });
  const app = new Element("app"), dialog = new Element("dialog"), drawer = new Element("task-drawer");
  const topDate = new Element(), results = new Element("task-results"), count = new Element("result-count");
  const elements = new Map([app, dialog, drawer, results, count, new Element("toast"), new Element("sidebar-tooltip"), new Element("page-title")].map(el => ["#" + el.id, el]));
  elements.set(".main-date", topDate);
  elements.set(".account-entry", new Element());
  drawer.querySelector = () => new Element();
  const undo = new Element();
  elements.get("#toast").querySelector = () => undo;
  const doc = new Surface();
  doc.body = new Element();
  doc.documentElement = { style: { removeProperty() {} } };
  doc.activeElement = doc.body;
  doc.visibilityState = "visible";
  doc.querySelector = selector => elements.get(selector) || null;
  doc.getElementById = id => elements.get("#" + id) || null;
  doc.querySelectorAll = () => [];
  doc.createElement = () => new Element();
  const win = new Surface();
  win.scrollTo = () => {};
  Object.assign(globalThis, {
    document: doc, window: win,
    location: new URL(url),
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    CustomEvent: class { constructor(type) { this.type = type; } },
  });
  const entries = [location.href];
  let position = 0;
  globalThis.history = {
    replaceState(_state, _title, href) {
      globalThis.location = new URL(href);
      entries[position] = location.href;
    },
    pushState(_state, _title, href) {
      globalThis.location = new URL(href);
      entries.splice(++position, Infinity, location.href);
    },
    async go(delta) {
      position += delta;
      globalThis.location = new URL(entries[position]);
      await win.emit("popstate");
      await new Promise(setImmediate);
    },
  };
  const tasks = [
    { id: "task", title: "合成任务", status: "not_started", due: { date: "2026-10-16", precision: deadlineTime ? "datetime" : "date", ...(deadlineTime ? { time: deadlineTime } : {}) }, plans: [], subtasks: [] },
    { id: "notice", title: "合成通知", content_type: "notice", planningExcluded: true, status: noticeRead ? "completed" : "not_started", due: { date: "2026-10-23", precision: noticeTime ? "datetime" : "date", ...(noticeTime ? { time: noticeTime } : {}) }, plans: [], subtasks: [] },
  ];
  if (undated) tasks.push({ id: "undated", title: "合成无日期任务", status: "not_started", due: null, plans: [], subtasks: [] });
  if (undatedNotice) tasks.push({ id: "undated-notice", title: "合成无日期通知", content_type: "notice", planningExcluded: true, status: "not_started", due: null, plans: [], subtasks: [] });
  const writes = [];
  globalThis.fetch = async (path, options = {}) => {
    let value = {};
    if (path === "/api/auth/me") value = { user: { id: "synthetic-" + instanceId, name: "合成账号" } };
    else if (path === "/api/workspace") value = { tasks: { version: 1, value: tasks } };
    else if (path === "/api/data/tasks") {
      if (failWrite) return { ok: false, status: 503, json: async () => ({ error: "合成保存失败" }) };
      writes.push(JSON.parse(options.body)); value = { version: writes.length + 1 };
    }
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/recognition/batches") value = [];
    else if (path === "/api/recognition/text-jobs") value = { contract_version: "text-import-jobs/0.1", jobs: [] };
    else if (path === "/api/sources/sync-runs") value = { contract_version: "source-sync/0.1", runs: [] };
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, interval_minutes: 180, revision: 0 };
    else if (path === "/api/materials/jobs") value = [];
    else if (path === "/api/materials/limits") value = {};
    else assert.ok(["/api/version", "/api/sources/credentials", "/api/recognition/task-evidence"].includes(path), "unexpected synthetic request: " + path);
    return { ok: true, json: async () => structuredClone(value) };
  };
  await import(`../src/app.js?date-test=${instanceId}`);
  await new Promise(setImmediate);
  const click = dataset => doc.emit("click", { closest: () => ({ dataset }) });
  const drag = async (date = "2026-10-20", id = "undated") => {
    const source = new Element();
    source.dataset.calendarDragTask = id;
    source.closest = () => source;
    const cell = new Element();
    cell.dataset.calendarDropDate = date;
    cell.closest = () => cell;
    doc.elementFromPoint = () => cell;
    const pointer = { pointerId: 1, pointerType: "mouse", button: 0, buttons: 1, isPrimary: true, clientX: 20, clientY: 30 };
    await doc.emit("pointerdown", source, pointer);
    await doc.emit("pointermove", source, { ...pointer, clientX: 100 });
    await doc.emit("pointerup", source, { ...pointer, clientX: 100 });
    await doc.emit("click", { closest: () => ({ dataset: { taskOpen: id } }) }, { detail: 1 });
    await new Promise(setImmediate);
  };
  return { app, dialog, drawer, results, topDate, doc, win, writes, click, drag, undo, entries, toast: elements.get("#toast") };
}

for (const source of ["text", "teaching", "files"]) test(`startup restores the ${source} import source after session loading`, async t => {
  const f = await fixture(t, { url: `http://synthetic.test/?page=import&importSource=${source}` });
  assert.match(f.app.innerHTML, new RegExp(`data-import-source="${source}" aria-pressed="true"`));
  assert.equal(location.searchParams.get("page"), "import");
  assert.equal(f.entries.length, 1);
});

for (const scope of ["notices", "near", "done"]) test(`startup restores the ${scope} task category`, async t => {
  const f = await fixture(t, { url: `http://synthetic.test/?scope=${scope}` });
  assert.match(f.app.innerHTML, new RegExp(`data-scope="${scope}"[^>]*aria-current="page"`));
});

test("startup restores the calendar range, selected date and undated pane", async t => {
  const f = await fixture(t, { url: "http://synthetic.test/?view=calendar&mode=week&anchor=2026-12-24&date=2026-12-25&pane=undated" });
  assert.match(f.app.innerHTML, /data-calendar-mode="week" aria-pressed="true"/);
  assert.match(f.app.innerHTML, /data-calendar-day="2026-12-25"[^>]*aria-pressed="true"/);
  assert.match(f.app.innerHTML, /data-calendar-pane="undated"[^>]*aria-pressed="true"/);
});

test("navigation writes history once per change and back/forward restores the import source", async t => {
  const f = await fixture(t);
  await f.click({ scope: "notices" });
  await f.click({ page: "import" });
  await f.click({ importSource: "teaching" });
  await new Promise(setImmediate);
  assert.equal(location.searchParams.get("importSource"), "teaching");
  const count = f.entries.length;
  await f.click({ importSource: "teaching" });
  assert.equal(f.entries.length, count);
  await history.go(-1);
  assert.match(f.app.innerHTML, /data-import-source="text" aria-pressed="true"/);
  await history.go(-1);
  assert.match(f.app.innerHTML, /data-scope="notices"[^>]*aria-current="page"/);
  await history.go(1);
  await history.go(1);
  assert.match(f.app.innerHTML, /data-import-source="teaching" aria-pressed="true"/);
  assert.equal(f.entries.length, count);
});

test("calendar changes update the URL and back restores its previous month", async t => {
  const f = await fixture(t, { url: "http://synthetic.test/?view=calendar&anchor=2026-12-01&date=2026-12-24" });
  await f.click({ action: "calendar-next" });
  assert.equal(location.searchParams.get("anchor"), "2027-01-01");
  assert.equal(location.searchParams.get("date"), "2027-01-01");
  await history.go(-1);
  assert.match(f.app.innerHTML, /value="2026-12-24"/);
  assert.equal(location.searchParams.get("anchor"), "2026-12-01");
  await f.click({ calendarMode: "week" });
  await f.click({ action: "calendar-next" });
  assert.equal(location.searchParams.get("date"), "2026-12-31");
  await f.click({ calendarPane: "undated" });
  assert.equal(location.searchParams.get("pane"), "undated");
  await f.click({ calendarDay: "2026-12-30" });
  assert.equal(location.searchParams.get("pane"), "day");
  assert.equal(location.searchParams.get("date"), "2026-12-30");
  await f.doc.emit("change", { id: "calendar-jump", value: "2027-02-01", dataset: {} });
  assert.equal(location.searchParams.get("anchor"), "2027-02-01");
  await f.click({ action: "calendar-today" });
  assert.equal(location.searchParams.get("date"), "2026-10-15");
});

test("account reset keeps safe navigation while clearing the import draft", async t => {
  const f = await fixture(t, { url: "http://synthetic.test/?page=import&importSource=text" });
  await f.doc.emit("input", { id: "import-text", value: "私有的未提交通知" });
  await f.win.emit("niuma:account-reset");
  assert.match(f.app.innerHTML, /data-import-source="text" aria-pressed="true"/);
  assert.doesNotMatch(f.app.innerHTML, /私有的未提交通知/);
  assert.doesNotMatch(location.href, /私有|query|text=/);
});

for (const mode of ["month", "week"]) test(`${mode} calendar drag writes a date without a time, suppresses detail opening and undo restores undated`, async t => {
  const date = mode === "week" ? "2026-10-16" : "2026-10-20";
  const f = await fixture(t, { undated: true });
  await f.click({ view: "calendar" });
  await f.click({ calendarMode: mode });
  await f.click({ calendarPane: "undated" });
  assert.match(f.results.innerHTML, new RegExp(`data-calendar-drop-date="${date}"`));
  await f.drag(date);
  assert.equal(f.writes.length, 1);
  assert.deepEqual(f.writes[0].value.find(task => task.id === "undated").due, { date, precision: "date" });
  assert.equal(f.drawer.open, false);
  assert.match(f.toast.innerHTML, new RegExp(`截止日期已设为 ${date}`));
  assert.doesNotMatch(f.app.innerHTML, /data-task-id="undated"/);
  await f.undo.onclick();
  assert.equal(f.writes.at(-1).value.find(task => task.id === "undated").due, null);
  assert.match(f.app.innerHTML, /data-calendar-drag-task="undated"/);
  await f.doc.emit("pointerdown", { closest: () => null }, { pointerType: "mouse", isPrimary: true, button: 0 });
  await f.click({ taskOpen: "undated" });
  assert.equal(f.drawer.open, true);
});

for (const mode of ["month", "week"]) for (const deadlineTime of ["", "18:30"])
test(`${mode} calendar reschedules a dated task, retains ${deadlineTime || "date-only precision"} and can undo`, async t => {
  const f = await fixture(t, { deadlineTime });
  const date = mode === "week" ? "2026-10-17" : "2026-10-20";
  await f.click({ view: "calendar" });
  await f.click({ calendarMode: mode });
  await f.click({ calendarDay: "2026-10-16" });
  await f.drag(date, "task");
  const due = { date, precision: deadlineTime ? "datetime" : "date", ...(deadlineTime ? { time: deadlineTime } : {}) };
  assert.equal(f.writes.length, 1);
  assert.deepEqual(f.writes[0].value.find(task => task.id === "task").due, due);
  assert.equal(f.drawer.open, false);
  assert.match(f.toast.innerHTML, new RegExp(`截止日期已改为 ${date}${deadlineTime ? ` ${deadlineTime}` : ""}`));
  assert.match(f.app.innerHTML, new RegExp(`data-calendar-day="${date}"[^>]*aria-pressed="true"`));
  await f.undo.onclick();
  assert.deepEqual(f.writes.at(-1).value.find(task => task.id === "task").due, { ...due, date: "2026-10-16" });
});

test("dropping on the original date does not save, open details or offer undo", async t => {
  const f = await fixture(t, { deadlineTime: "18:30" });
  await f.click({ view: "calendar" });
  await f.drag("2026-10-16", "task");
  assert.equal(f.writes.length, 0);
  assert.equal(f.drawer.open, false);
  assert.doesNotMatch(f.toast.innerHTML, /截止日期已/);
});

test("a failed reschedule keeps task and notice original dates and times", async t => {
  const f = await fixture(t, { deadlineTime: "18:30", noticeTime: "20:15", failWrite: true });
  await f.click({ view: "calendar" });
  await f.drag("2026-10-20", "task");
  assert.equal(f.writes.length, 0);
  assert.match(f.toast.innerHTML, /未更改截止日期：合成保存失败/);
  await f.click({ taskOpen: "task" });
  assert.match(f.drawer.innerHTML, /2026-10-16|明天/);
  assert.match(f.drawer.innerHTML, /18:30/);
  await f.click({ action: "drawer-close" });
  await f.drag("2026-10-20", "notice");
  assert.equal(f.writes.length, 0);
  assert.match(f.toast.innerHTML, /未更改关键日期：合成保存失败/);
  await f.click({ taskOpen: "notice" });
  assert.match(f.drawer.innerHTML, /10 月 23 日/);
  assert.match(f.drawer.innerHTML, /20:15/);
});

test("a failed calendar drop retains the undated row and reports failure without a success toast", async t => {
  const f = await fixture(t, { undated: true, failWrite: true });
  await f.click({ view: "calendar" });
  await f.click({ calendarPane: "undated" });
  await f.drag();
  assert.equal(f.writes.length, 0);
  assert.match(f.app.innerHTML, /data-calendar-drag-task="undated"/);
  assert.match(f.toast.innerHTML, /未设置截止日期：合成保存失败/);
  assert.doesNotMatch(f.toast.innerHTML, /截止日期已设为/);
  assert.equal(f.drawer.open, false);
});

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
  await f.doc.emit("input", { id: "task-search", value: "合成任务" });
  await f.click({ view: "calendar" });
  await f.doc.emit("change", { id: "calendar-jump", value: "2026-12-01", dataset: {} });
  await f.click({ taskOpen: "task" });
  f.drawer.querySelector = () => ({ focus() {} });
  await f.click({ action: "drawer-edit" });
  f.drawer.innerHTML = "未保存的任务草稿";
  t.mock.timers.setTime(new Date("2026-10-18T09:00:00").getTime());
  await f.win.emit("focus");
  assert.match(f.app.innerHTML, /datetime="2026-10-18"/);
  assert.match(f.app.innerHTML, /value="2026-12-01"/);
  assert.doesNotMatch(f.app.innerHTML, /id="task-search"|class="query-toolbar"|id="advanced-filters"/);
  assert.equal(f.drawer.innerHTML, "未保存的任务草稿");
  await f.click({ action: "calendar-today" });
  assert.match(f.results.innerHTML, /data-calendar-day="2026-10-18"[^>]*aria-pressed="true"/);
  assert.match(f.results.innerHTML, /合成通知/);
  await f.click({ view: "list" });
  assert.match(f.app.innerHTML, /id="task-search"[^>]*value="合成任务"/);
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

for (const mode of ["month", "week"]) for (const noticeTime of ["", "18:30"])
test(`${mode} calendar moves a notice key date, retains ${noticeTime || "date-only precision"}, updates the URL and undoes`, async t => {
  const f = await fixture(t, { noticeTime, url: `http://synthetic.test/?view=calendar&mode=${mode}&anchor=2026-10-15&date=2026-10-23` });
  const date = mode === "week" ? "2026-10-17" : "2026-10-20";
  await f.click({ view: "calendar" });
  await f.click({ calendarMode: mode });
  await f.click({ calendarDay: "2026-10-23" });
  assert.match(f.app.innerHTML, /data-calendar-drag-task="notice"/);
  const historyLength = f.entries.length;
  await f.drag(date, "notice");
  const due = { date, precision: noticeTime ? "datetime" : "date", ...(noticeTime ? { time: noticeTime } : {}) };
  assert.equal(f.writes.length, 1);
  const saved = f.writes[0].value.find(task => task.id === "notice");
  assert.deepEqual(saved.due, due);
  assert.equal(saved.content_type, "notice");
  assert.equal(saved.planningExcluded, true);
  assert.equal(location.searchParams.get("date"), date);
  assert.equal(location.searchParams.get("mode"), mode);
  assert.equal(f.entries.length, historyLength);
  assert.equal(f.drawer.open, false);
  assert.match(f.toast.innerHTML, new RegExp(`关键日期已改为 ${date}${noticeTime ? ` ${noticeTime}` : ""}`));
  await f.undo.onclick();
  assert.deepEqual(f.writes.at(-1).value.find(task => task.id === "notice").due, { ...due, date: "2026-10-23" });
});

for (const mode of ["month", "week"])
test(`${mode} calendar assigns an undated notice key date and undo restores undated`, async t => {
  const f = await fixture(t, { undatedNotice: true });
  const date = mode === "week" ? "2026-10-16" : "2026-10-20";
  await f.click({ view: "calendar" });
  await f.click({ calendarMode: mode });
  await f.click({ calendarPane: "undated" });
  assert.match(f.results.innerHTML, /data-calendar-drag-task="undated-notice"/);
  await f.drag(date, "undated-notice");
  assert.deepEqual(f.writes[0].value.find(task => task.id === "undated-notice").due, { date, precision: "date" });
  assert.match(f.toast.innerHTML, new RegExp(`关键日期已设为 ${date}`));
  assert.equal(f.drawer.open, false);
  await f.undo.onclick();
  assert.equal(f.writes.at(-1).value.find(task => task.id === "undated-notice").due, null);
  assert.match(f.app.innerHTML, /data-calendar-drag-task="undated-notice"/);
});

test("read notices share completed task protection against calendar drag", async t => {
  const f = await fixture(t, { noticeRead: true });
  await f.click({ view: "calendar" });
  await f.click({ calendarDay: "2026-10-23" });
  assert.doesNotMatch(f.app.innerHTML, /data-calendar-drag-task="notice"/);
  await f.drag("2026-10-20", "notice");
  assert.equal(f.writes.length, 0);
  assert.doesNotMatch(f.toast.innerHTML, /关键日期已/);
});
