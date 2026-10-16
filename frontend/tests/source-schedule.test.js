import test from "node:test";
import assert from "node:assert/strict";

globalThis.window = new EventTarget();
const { session } = await import("../src/team-store.js");
const { createSyncScheduleUI } = await import("../src/source-schedule-ui.js");
const response = value => ({ ok: true, json: async () => structuredClone(value) });
const settings = (extra = {}) => ({ contract_version: "source-sync/0.1", enabled: false,
  available: true, revision: 0, interval_minutes: 180, next_run_at: null, error: null, ...extra });

function fixture(t, extra = {}) {
  session.user = { id: "synthetic" };
  session.changing = false;
  let saved = settings(extra), connected = true, busy = false, running = false, stopping = false, starts = 0;
  const requests = [], messages = [], events = [];
  const fetch = async (path, options) => {
    assert.equal(path, "/api/sources/sync-schedule");
    if (options.method === "GET") return response(saved);
    const body = JSON.parse(options.body);
    requests.push(body); events.push("save");
    saved = settings({ ...saved, ...body, revision: saved.revision + 1, next_run_at: body.enabled ? 1800000000 : null });
    return response(saved);
  };
  globalThis.fetch = fetch;
  const ui = createSyncScheduleUI({
    escapeHtml: value => String(value ?? ""), render() {},
    isDemo: () => false, isConnected: () => connected, isBusy: () => busy, isRunning: () => running,
    canStop: () => running, isStopping: () => stopping,
    getProgressLabel: () => running ? "正在识别，已处理 1 / 3 条通知" : "",
    async start() { starts++; events.push("start"); return true; }, toast: message => messages.push(message),
  });
  t.after(() => ui.reset());
  return { ui, requests, messages, events, fetch, get starts() { return starts; },
    field: (key, value) => ui.input({ target: { dataset: { syncScheduleField: key }, value, checked: value } }),
    disconnect() { connected = false; }, setBusy(value) { busy = value; running = value; },
    setReading(value) { busy = value; running = false; },
    setStopping(value) { stopping = value; },
  };
}

test("single recognition uses the shared action without creating a schedule or a dialog", async t => {
  const f = fixture(t); await f.ui.load();
  const html = f.ui.render();
  assert.match(html, /value="once" selected/);
  assert.match(html, /开始识别/);
  assert.doesNotMatch(html, /自动同步|data-import-form|设置|type="checkbox"/);
  await f.ui.start();
  assert.equal(f.requests.length, 0); assert.equal(f.starts, 1);
});

test("choosing a frequency is local; explicit consent starts immediately and saves the recurring interval", async t => {
  const f = fixture(t); await f.ui.load();
  f.field("interval", "15");
  assert.equal(f.requests.length, 0);
  assert.match(f.ui.render(), /启动并识别|关闭网页后仍会运行/);
  await f.ui.start(); assert.equal(f.starts, 0); assert.equal(f.requests.length, 0);
  assert.match(f.ui.render(), /请先同意/);
  f.field("consent", true); await f.ui.start();
  assert.deepEqual(f.requests[0], { contract_version: "source-sync/0.1", revision: 0,
    enabled: true, interval_minutes: 15, sources: ["teaching_web"], consent: true });
  assert.deepEqual(f.events, ["save", "start"]);
  assert.match(f.ui.render(), /停止同步/);
  assert.doesNotMatch(f.ui.render(), /type="checkbox"/);
  await f.ui.start();
  assert.equal(f.requests[1].enabled, false); assert.equal(f.requests[1].consent, false);
  assert.equal(f.starts, 1); assert.deepEqual(f.messages, ["定时同步已停止"]);
  assert.match(f.ui.render(), /value="15" selected/);
  assert.match(f.ui.render(), /启动并识别/);
  assert.doesNotMatch(f.ui.render(), /id="teaching-consent"[^>]*checked/);
  await f.ui.load();
  assert.match(f.ui.render(), /value="15" selected/);
  await f.ui.start(); assert.equal(f.requests.length, 2); assert.equal(f.starts, 1);
  f.field("consent", true); await f.ui.start();
  assert.equal(f.requests[2].interval_minutes, 15); assert.equal(f.requests[2].enabled, true);
  assert.equal(f.starts, 2);
});

test("switching an enabled service to single disables recurrence before starting one run", async t => {
  const f = fixture(t, { enabled: true, revision: 2, interval_minutes: 15 }); await f.ui.load();
  f.field("interval", "once"); await f.ui.start();
  assert.deepEqual(f.events, ["save", "start"]);
  assert.equal(f.requests[0].enabled, false); assert.equal(f.requests[0].revision, 2);
  assert.equal(f.requests[0].interval_minutes, 15);
});

test("custom intervals preserve server bounds and existing non-preset schedules", async t => {
  const f = fixture(t); await f.ui.load();
  f.field("interval", "custom"); f.field("consent", true);
  for (const n of ["14", "10081", "15.5", ""]) {
    f.field("minutes", n); await f.ui.start();
    assert.equal(f.requests.length, 0); assert.match(f.ui.render(), /请输入 15～10080/);
  }
  f.field("minutes", "47"); f.field("consent", true); await f.ui.start();
  assert.equal(f.requests[0].interval_minutes, 47);
  f.ui.reset(); await f.ui.load();
  assert.match(f.ui.render(), /value="custom" selected/); assert.match(f.ui.render(), /value="47"/);
});

test("unavailable recurrence still permits stopping an existing service and single recognition", async t => {
  const f = fixture(t, { enabled: true, available: false, revision: 2 }); await f.ui.load();
  assert.match(f.ui.render(), /定时同步暂不可用/);
  await f.ui.start();
  assert.equal(f.requests[0].enabled, false); assert.equal(f.starts, 0);
  f.field("interval", "once");
  await f.ui.start(); assert.equal(f.starts, 1);
  f.field("interval", "15"); f.field("consent", true); await f.ui.start();
  assert.equal(f.requests.length, 1); assert.equal(f.starts, 1);
});

test("conflicting settings never start a run and require a fresh revision", async t => {
  const f = fixture(t); await f.ui.load();
  f.field("interval", "60"); f.field("consent", true);
  globalThis.fetch = async () => ({ ok: false, status: 409, json: async () => ({ error: "conflict" }) });
  await f.ui.start(); await f.ui.start();
  assert.equal(f.starts, 0); assert.match(f.ui.render(), /重新读取/);
  globalThis.fetch = async (path, options) => options.method === "GET" ? response(settings({ revision: 3 })) : f.fetch(path, options);
  await f.ui.click({ dataset: { syncScheduleAction: "reload" } }); await f.ui.start();
  assert.equal(f.requests[0].revision, 3); assert.equal(f.starts, 1);
});

test("account reset invalidates pending reads and saves before recognition or notifications", async t => {
  const f = fixture(t); let release;
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  const reading = f.ui.start(); f.ui.reset(); session.user = { id: "another" };
  release(response(settings())); await reading;
  assert.equal(f.starts, 0);
  globalThis.fetch = f.fetch; await f.ui.load();
  f.field("interval", "15"); f.field("consent", true);
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  const saving = f.ui.start(); assert.equal(f.ui.isBusy(), true);
  f.ui.reset(); release(response(settings({ enabled: true, revision: 1 }))); await saving;
  assert.equal(f.starts, 0); assert.deepEqual(f.messages, []);
});

test("repeated clicks during a save cannot duplicate runs and active runs keep controls locked", async t => {
  const f = fixture(t); await f.ui.load();
  f.field("interval", "15"); f.field("consent", true);
  let release;
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  const saving = f.ui.start(); await f.ui.start();
  f.field("interval", "once");
  release(response(settings({ enabled: true, interval_minutes: 15, revision: 1 }))); await saving;
  assert.equal(f.starts, 1); assert.match(f.ui.render(), /value="15" selected/);
  f.setBusy(true);
  assert.match(f.ui.render(), /data-import-action="sync" >.*停止同步/);
  assert.match(f.ui.render(), /data-sync-schedule-field="interval" disabled/);
  await f.ui.start(); assert.equal(f.starts, 1);
  f.setBusy(false); f.disconnect(); await f.ui.start(); assert.equal(f.starts, 1);
});

test("stopping disables the service even while recognition is active", async t => {
  const f = fixture(t, { enabled: true, interval_minutes: 15 }); await f.ui.load(); f.setBusy(true);
  assert.equal(await f.ui.stop(), true);
  assert.equal(f.requests[0].enabled, false); assert.equal(f.starts, 0);
  assert.match(f.ui.render(), /value="15" selected/);
});

test("stopping preserves custom interval selection and minutes", async t => {
  const f = fixture(t, { enabled: true, interval_minutes: 47 }); await f.ui.load();
  await f.ui.stop(); await f.ui.load();
  assert.equal(f.requests[0].enabled, false);
  assert.match(f.ui.render(), /value="custom" selected/);
  assert.match(f.ui.render(), /id="teaching-interval"[^>]*value="47"/);
  assert.match(f.ui.render(), /启动并识别/);
});

test("single import exposes an enabled primary stop action; pending stop keeps its position and locks it", async t => {
  const f = fixture(t); await f.ui.load(); f.setBusy(true);
  assert.match(f.ui.render(), /data-import-action="sync" >.*停止导入/);
  f.setStopping(true);
  assert.match(f.ui.render(), /data-import-action="sync" disabled>.*正在停止…/);
});

test("the active action combines a spinning arc with its stop icon and stays clickable until stopping", async t => {
  const f=fixture(t);await f.ui.load();f.setBusy(true);
  let html=f.ui.render(),button=html.match(/<button[^>]*data-import-action="sync"[^>]*>.*?<\/button>/)[0];
  assert.match(button,/aria-busy="true"/);assert.match(button,/teaching-progress-ring/);
  assert.match(button,/teaching-stop-icon/);assert.match(button,/停止导入/);assert.doesNotMatch(button,/ disabled/);
  assert.match(html,/id="teaching-run-status" class="sr-only" role="status">正在识别，已处理 1 \/ 3 条通知/);
  f.setStopping(true);html=f.ui.render();
  assert.match(html,/data-import-action="sync" disabled>.*teaching-progress-ring.*正在停止…/);
  f.setStopping(false);f.setBusy(false);html=f.ui.render();
  assert.doesNotMatch(html,/teaching-progress-ring|teaching-run-status/);assert.match(html,/开始识别/);
});

test("scheduled waiting and polling use a static stop icon, with rotation only during the actual run", async t => {
  const f=fixture(t,{enabled:true,interval_minutes:15});await f.ui.load();
  assert.match(f.ui.render(),/停止同步/);assert.match(f.ui.render(),/teaching-stop-icon/);
  assert.doesNotMatch(f.ui.render(),/teaching-progress-ring/);
  f.setBusy(true);assert.match(f.ui.render(),/teaching-progress-ring/);assert.match(f.ui.render(),/停止同步/);
  f.setReading(true);assert.doesNotMatch(f.ui.render(),/teaching-progress-ring/);
  f.setReading(false);assert.match(f.ui.render(),/停止同步/);assert.doesNotMatch(f.ui.render(),/teaching-progress-ring/);
});

test("an active scheduled run uses the saved service state even when the draft was changed", async t => {
  const f = fixture(t, {enabled: true, interval_minutes: 15}); await f.ui.load();
  f.field("interval", "once"); f.setBusy(true);
  assert.match(f.ui.render(), /data-import-action="sync" >.*停止同步/);
});

test("editing an existing custom interval changes the action and asks for new consent without losing the field identity", async t => {
  const f = fixture(t, { enabled: true, interval_minutes: 47 }); await f.ui.load();
  assert.match(f.ui.render(), /停止同步/);
  f.field("minutes", "48");
  assert.match(f.ui.render(), /启动并识别/); assert.match(f.ui.render(), /id="teaching-interval"/);
  assert.match(f.ui.render(), /type="checkbox"/);
  await f.ui.start(); assert.equal(f.requests.length, 0);
  f.field("consent", true); await f.ui.start();
  assert.equal(f.requests[0].interval_minutes, 48); assert.equal(f.starts, 1);
});
