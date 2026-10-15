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
  let saved = settings(extra), html = "", dialogOpen = false, opens = 0, connected = true;
  const requests = [], messages = [];
  globalThis.fetch = async (path, options) => {
    assert.equal(path, "/api/sources/sync-schedule");
    if (options.method === "GET") return response(saved);
    const body = JSON.parse(options.body);
    requests.push(body);
    saved = settings({ ...body, revision: saved.revision + 1, next_run_at: body.enabled ? 1800000000 : null });
    return response(saved);
  };
  const ui = createSyncScheduleUI({
    escapeHtml: value => String(value ?? ""), icon: () => "", render() {},
    isDemo: () => false, isConnected: () => connected,
    isDialogOpen: () => dialogOpen,
    openDialog(value) { html = value; dialogOpen = true; opens++; },
    closeDialog() { dialogOpen = false; }, toast: message => messages.push(message),
  });
  t.after(() => ui.reset());
  return { ui, requests, messages, get html() { return html; }, get opens() { return opens; },
    open: () => ui.click({ dataset: { syncScheduleAction: "open" } }),
    field: (key, value) => ui.input({ target: { dataset: { syncScheduleField: key }, value, checked: value } }),
    save: () => ui.submit({ dataset: { importForm: "schedule" } }),
    replaceDialog() { dialogOpen = false; }, disconnect() { connected = false; },
  };
}

test("main page shows only a compact status and settings entry; interval and consent stay in the dialog", async t => {
  const f = fixture(t);
  await f.ui.load();
  assert.match(f.ui.render(), /自动同步.*未开启/);
  assert.doesNotMatch(f.ui.render(), /<input|<select|微信|识别服务|来源|重新读取/);
  await f.open();
  assert.match(f.html, /role="switch"/);
  assert.match(f.html, /同步频率/);
  assert.match(f.html, /data-schedule-consent hidden/);
});

test("enabling requires explicit consent and saves source, interval and current revision", async t => {
  const f = fixture(t);
  await f.open();
  f.field("enabled", true); f.field("interval", "60");
  await f.save();
  assert.equal(f.requests.length, 0);
  assert.match(f.html, /请先同意/);
  f.field("consent", true);
  await f.save();
  assert.deepEqual(f.requests[0], { contract_version: "source-sync/0.1", revision: 0,
    enabled: true, interval_minutes: 60, sources: ["teaching_web"], consent: true });
  assert.match(f.ui.render(), /每小时.*下次/);
  assert.deepEqual(f.messages, ["自动同步已开启"]);
});

test("custom intervals respect the server bounds and survive failed validation", async t => {
  const f = fixture(t);
  await f.open();
  f.field("enabled", true); f.field("consent", true); f.field("interval", "custom");
  for (const minutes of ["14", "10081", "15.5", ""]) {
    f.field("minutes", minutes); await f.save();
    assert.equal(f.requests.length, 0);
    assert.match(f.html, /请输入 15～10080/);
  }
  f.field("minutes", "47"); await f.save();
  assert.equal(f.requests[0].interval_minutes, 47);
  await f.open();
  assert.match(f.html, /value="custom" selected/);
  assert.match(f.html, /value="47"/);
});

test("unavailable service still allows an existing schedule to be disabled without consent", async t => {
  const f = fixture(t, { enabled: true, available: false, revision: 2 });
  await f.open();
  assert.match(f.html, /自动同步暂未开放/);
  f.field("enabled", false); await f.save();
  assert.equal(f.requests[0].enabled, false);
  assert.equal(f.requests[0].consent, false);
  assert.equal(f.requests[0].revision, 2);
});

test("conflict preserves the draft and requires a fresh revision before retrying", async t => {
  const f = fixture(t);
  await f.open();
  f.field("enabled", true); f.field("interval", "60"); f.field("consent", true);
  globalThis.fetch = async () => ({ ok: false, status: 409, json: async () => ({ error: "conflict" }) });
  await f.save();
  assert.match(f.html, /设置已发生变化/);
  assert.match(f.html, /value="60" selected/);
  globalThis.fetch = async (path, options) => {
    if (options.method === "GET") return response(settings({ revision: 3 }));
    f.requests.push(JSON.parse(options.body));
    return response(settings({ revision: 4, enabled: true, interval_minutes: 60 }));
  };
  await f.ui.click({ dataset: { syncScheduleAction: "reload" } });
  f.field("enabled", true); f.field("interval", "60"); f.field("consent", true);
  await f.save();
  assert.equal(f.requests[0].revision, 3);
});

test("late reads and saves cannot reopen a closed or replaced dialog", async t => {
  const f = fixture(t);
  let release;
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  const reading = f.open(), firstOpens = f.opens;
  f.replaceDialog(); release(response(settings())); await reading;
  assert.equal(f.opens, firstOpens);
  globalThis.fetch = async () => response(settings());
  await f.open();
  f.field("enabled", true); f.field("consent", true);
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  const saving = f.save(), lastOpens = f.opens;
  f.replaceDialog(); release(response(settings({ enabled: true, revision: 1 }))); await saving;
  assert.equal(f.opens, lastOpens);
  assert.match(f.ui.render(), /每 3 小时/);
});

test("switching accounts discards an in-flight save and its notification", async t => {
  const f = fixture(t);
  await f.open();
  f.field("enabled", true); f.field("consent", true);
  let release;
  globalThis.fetch = () => new Promise(resolve => { release = resolve; });
  const saving = f.save();
  f.ui.reset(); session.user = { id: "another-synthetic" };
  release(response(settings({ enabled: true, revision: 1 }))); await saving;
  assert.deepEqual(f.messages, []);
  globalThis.fetch = async () => response(settings());
  await f.ui.load();
  f.disconnect();
  assert.match(f.ui.render(), /连接后可开启/);
  assert.doesNotMatch(f.ui.render(), /下次/);
});
