import test from "node:test";
import assert from "node:assert/strict";
import { createReprocessingUI } from "../src/reprocessing-ui.js";

const ROOT = "/api/recognition/reprocessing/";
const VERSION = "reprocessing/0.1";
const ROW_STATES = ["queued", "running", "succeeded", "failed", "cancelled", "interrupted", "superseded"];
const records = [
  { input_id: "保存/中文 1", label: "课程报告通知", course_or_project: "概率论", result_status: "success", policy_version: "text-policy-v1" },
  { input_id: "保存/中文 2", label: "课程安排通知", course_or_project: "英语", result_status: "no_tasks", policy_version: "text-policy-v1" },
];
const source = { source_account_id: "source:a", label: "教学网来源 1", records };
function run(state = "queued", revision = 1, ids = records.map((item) => item.input_id)) {
  const rows = ids.map((input_id) => ({
    input_id, state: state === "stopped" ? "cancelled" : state === "succeeded" ? "succeeded" : state,
    previous_available: true, old_result_retained: true, decision_summary: { unresolved: 0 },
    policy_version: state === "succeeded" ? "text-policy-v1" : null,
    error: state === "failed" ? { message: "识别服务暂不可用" } : null,
  }));
  return { contract_version: VERSION, run_id: "run:one", source_account_id: "source:a", revision, state, created_at: "2026-10-14T12:00:00Z", records: rows,
    counts: { total: rows.length, ...Object.fromEntries(ROW_STATES.map((name) => [name, rows.filter((item) => item.state === name).length])) } };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
const toggle = (open) => ({ target: { open, isConnected: true, matches: (value) => value === "[data-reprocess-panel]" } });
const click = (action, id) => ({ dataset: { reprocessAction: action, reprocessRun: id } });
const select = (id, checked = true) => ({ target: { dataset: { reprocessRecord: id }, checked } });

function fixture(t, scenario = {}) {
  let epoch = 1, demo = false, authenticated = true, importBusy = false;
  let account = "A", sources = [structuredClone(source)], runs = [], postFailures = 0;
  let refreshes = 0, renders = 0, nextTimer = 0;
  const requests = [], timers = new Map(), order = [];
  const host = {
    accountVersion: () => epoch, isDemo: () => demo, isAuthenticated: () => authenticated,
    isImportBusy: () => importBusy, render: () => renders++,
    flushStore: async () => { order.push("flush"); if (scenario.flushError) throw new Error(scenario.flushError); },
    onFinished: async (isCurrent) => {
      if (scenario.finished) await scenario.finished(isCurrent);
      if (isCurrent()) { refreshes++; order.push("refresh-batches", "refresh-tasks"); }
    },
    setTimer(fn, delay) { assert.equal(delay, 2000); const id = ++nextTimer; timers.set(id, fn); return id; },
    clearTimer(id) { timers.delete(id); },
    async api(path, body, method = "GET", options = {}) {
      requests.push({ path, body: structuredClone(body), method, account, signal: options.signal });
      if (scenario.intercept) {
        const value = await scenario.intercept(path, body, method);
        if (value !== undefined) return structuredClone(value);
      }
      if (path.startsWith(ROOT + "sources")) return { contract_version: VERSION, accounts: structuredClone(sources), next_cursor: null };
      if (path === ROOT + "runs" && body === undefined) return { contract_version: VERSION, runs: structuredClone(runs), next_cursor: null };
      if (path === ROOT + "runs" && method === "POST") {
        order.push("start");
        if (postFailures++ < (scenario.failStarts || 0)) throw new Error("网络连接中断");
        const result = run("queued", 1, body.input_ids);
        result.operation_id = body.operation_id;
        runs = [result]; return structuredClone(result);
      }
      if (path === ROOT + "runs/run%3Aone/stop") {
        order.push("stop");
        runs = [run("stopped", 3, runs[0].records.map((item) => item.input_id))];
        return structuredClone(runs[0]);
      }
      throw new Error("unexpected " + path);
    },
  };
  const ui = createReprocessingUI(host);
  t.after(() => ui.reset());
  return {
    ui, host, requests, order, timers,
    get refreshes() { return refreshes; }, get renders() { return renders; },
    setRuns(value) { runs = structuredClone(value); }, setSources(value) { sources = structuredClone(value); },
    setDemo(value) { demo = value; }, setAuthenticated(value) { authenticated = value; }, setImportBusy(value) { importBusy = value; },
    switchAccount() { account = "B"; epoch++; sources = [{ ...source, label: "B 账号来源", records: [{ ...records[0], input_id: "B-notice", label: "B 私有通知" }] }]; runs = []; ui.reset(); },
    async poll() { const entry = timers.entries().next().value; assert.ok(entry, "an active expanded run schedules polling"); timers.delete(entry[0]); entry[1](); await tick(); },
  };
}

test("the compact entry stays folded and rendering never fetches records or runs a model", async (t) => {
  const f = fixture(t);
  const html = f.ui.render();
  assert.match(html, /<details[^>]*data-reprocess-panel >.*<summary[^>]*>重新识别已有通知<\/summary>/);
  assert.match(html, /按当前规则重新识别下方已保存的通知/);
  assert.doesNotMatch(html, /暂不支持此入口/);
  assert.doesNotMatch(html, /v0\.2|v0\.4/);
  assert.equal(f.requests.length, 0);
  await f.ui.toggle(toggle(true));
  assert.equal(f.requests.length, 2);
  assert.ok(f.requests.every((item) => item.body === undefined));
  await f.ui.toggle(toggle(true));
  assert.equal(f.requests.length, 2);
});

test("success and no-task saved records can both be explicitly rerun after queued task writes finish", async (t) => {
  const f = fixture(t);
  await f.ui.toggle(toggle(true));
  records.forEach((item) => f.ui.input(select(item.input_id)));
  await f.ui.click(click("start"));
  const created = f.requests.find((item) => item.method === "POST");
  assert.deepEqual(Object.keys(created.body).sort(), ["contract_version", "input_ids", "operation_id", "source_account_id"]);
  assert.equal(created.path, ROOT + "runs");
  assert.equal(created.body.contract_version, VERSION);
  assert.equal(created.body.source_account_id, "source:a");
  assert.deepEqual(created.body.input_ids, records.map((item) => item.input_id));
  assert.match(created.body.operation_id, /^[A-Za-z0-9_.:-]{1,160}$/);
  assert.deepEqual(f.order.slice(0, 2), ["flush", "start"]);
  assert.equal(f.ui.isBusy(), true);
  f.setRuns([run("succeeded", 2)]);
  await f.poll();
  assert.equal(f.ui.isBusy(), false);
  assert.equal(f.refreshes, 1);
  assert.deepEqual(f.order.slice(-2), ["refresh-batches", "refresh-tasks"]);
  assert.match(f.ui.render(), /已完成/);
  await f.ui.load();
  assert.equal(f.refreshes, 1, "the terminal callback must not repeat for an unchanged run");
});

test("native teaching records and teaching policy versions reuse the same reprocessing envelope", async (t) => {
  const policy = "teaching-policy-v1:" + "a".repeat(64);
  const nativeRecord = {
    input_id: "教学网/课程:通知 1", label: "原生教学网通知", course_or_project: "测试课程",
    result_status: "success", policy_version: policy,
  };
  const nativeSource = { source_account_id: "native:campus", label: "当前教学网账号", records: [nativeRecord] };
  const nativeRun = (state, revision) => {
    const result = run(state, revision, [nativeRecord.input_id]);
    result.source_account_id = nativeSource.source_account_id;
    result.records[0].policy_version = state === "succeeded" ? policy : null;
    result.records[0].outcome = state === "succeeded" ? "tasks" : null;
    result.records[0].old_result_retained = false;
    result.records[0].decision_summary = { preserved: 0, replaced: 1, new_pending: 0, unresolved: 0 };
    return result;
  };
  const f = fixture(t, { intercept(path, body, method) {
    if (path === ROOT + "runs" && method === "POST") {
      return { ...nativeRun("queued", 1), operation_id: body.operation_id };
    }
  } });
  f.setSources([nativeSource]);
  await f.ui.toggle(toggle(true));
  assert.match(f.ui.render(), /原生教学网通知/);
  assert.doesNotMatch(f.ui.render(), /暂不支持|class="error"/);
  f.ui.input(select(nativeRecord.input_id));
  await f.ui.click(click("start"));
  const created = f.requests.find((item) => item.method === "POST");
  assert.deepEqual(Object.keys(created.body).sort(), ["contract_version", "input_ids", "operation_id", "source_account_id"]);
  assert.equal(created.body.contract_version, VERSION);
  assert.equal(created.body.source_account_id, nativeSource.source_account_id);
  assert.deepEqual(created.body.input_ids, [nativeRecord.input_id]);
  assert.equal(Object.hasOwn(created.body, "policy_version"), false, "the backend owns the teaching execution policy");
  f.setRuns([nativeRun("succeeded", 2)]);
  await f.poll();
  assert.equal(f.ui.isBusy(), false);
  assert.equal(f.refreshes, 1, "native automatic task creation still refreshes tasks and candidates");
  assert.match(f.ui.render(), /任务与识别结果已刷新/);
  assert.doesNotMatch(f.ui.render(), /请在待核对列表查看/);
  assert.equal(f.requests.filter((item) => item.method === "POST").length, 1, "native results must not trigger a second client write");
  assert.equal(f.requests.some((item) => item.path.endsWith("/resolve")), false);
  await f.ui.load();
  assert.equal(f.refreshes, 1);
});

test("older run revisions cannot replace newer state or repeat completion refresh", async (t) => {
  const f = fixture(t);
  await f.ui.toggle(toggle(true));
  f.ui.input(select(records[0].input_id));
  await f.ui.click(click("start"));
  f.setRuns([run("running", 3, [records[0].input_id])]);
  await f.poll();
  f.setRuns([run("queued", 1, [records[0].input_id])]);
  await f.poll();
  assert.match(f.ui.render(), /识别中/);
  assert.doesNotMatch(f.ui.render(), /<p class="status-note">排队中/);
  f.setRuns([run("succeeded", 4, [records[0].input_id])]);
  await f.poll();
  f.setRuns([run("running", 3, [records[0].input_id])]);
  await f.ui.load();
  assert.equal(f.ui.isBusy(), false);
  assert.equal(f.refreshes, 1);
});

test("stopping uses the persistent run endpoint while folding only pauses browser polling", async (t) => {
  const f = fixture(t);
  await f.ui.toggle(toggle(true));
  f.ui.input(select(records[0].input_id));
  await f.ui.click(click("start"));
  assert.equal(f.timers.size, 1);
  await f.ui.toggle(toggle(false));
  assert.equal(f.timers.size, 0);
  assert.equal(f.requests.some((item) => item.path.endsWith("/stop")), false);
  assert.equal(f.ui.isBusy(), true);
  await f.ui.toggle(toggle(true));
  await f.ui.click(click("stop", "run:one"));
  const stopped = f.requests.find((item) => item.path.endsWith("/stop"));
  assert.equal(stopped.path, ROOT + "runs/run%3Aone/stop");
  assert.deepEqual(stopped.body, { contract_version: VERSION });
  assert.equal(f.ui.isBusy(), false);
  assert.equal(f.refreshes, 1);
  assert.match(f.ui.render(), /已停止/);
  assert.match(f.ui.render(), /未处理/);
});

test("failed starts retain selection and reuse the operation identity on network retry", async (t) => {
  const f = fixture(t, { failStarts: 1 });
  await f.ui.toggle(toggle(true));
  f.ui.input(select(records[1].input_id));
  await f.ui.click(click("start"));
  assert.equal(f.ui.isBusy(), false);
  assert.match(f.ui.render(), /网络连接中断/);
  await f.ui.click(click("start"));
  const created = f.requests.filter((item) => item.method === "POST");
  assert.equal(created.length, 2);
  assert.equal(created[0].body.operation_id, created[1].body.operation_id);
  assert.deepEqual(created[1].body.input_ids, [records[1].input_id]);
  f.setRuns([run("failed", 2, [records[1].input_id])]);
  await f.poll();
  assert.match(f.ui.render(), /识别服务暂不可用/);
  assert.equal(f.refreshes, 1);
});

test("save conflicts block creation and concurrent importing prevents another run", async (t) => {
  const f = fixture(t, { flushError: "任务保存冲突，请重新读取" });
  await f.ui.toggle(toggle(true));
  f.ui.input(select(records[0].input_id));
  await f.ui.click(click("start"));
  assert.equal(f.requests.some((item) => item.method === "POST"), false);
  assert.match(f.ui.render(), /任务保存冲突/);
  const other = fixture(t);
  await other.ui.toggle(toggle(true));
  other.ui.input(select(records[0].input_id));
  other.setImportBusy(true);
  await other.ui.click(click("start"));
  assert.equal(other.requests.some((item) => item.method === "POST"), false);
  assert.match(other.ui.render(), /等待当前导入完成/);
});

test("demo and logged-out entry points never send reprocessing requests", async (t) => {
  const f = fixture(t);
  f.setDemo(true);
  await f.ui.toggle(toggle(true));
  await f.ui.click(click("load"));
  await f.ui.click(click("start"));
  assert.equal(f.requests.length, 0);
  assert.match(f.ui.render(), /示例预览不发送/);
  f.setDemo(false); f.setAuthenticated(false);
  await f.ui.load();
  assert.equal(f.requests.length, 0);
  assert.match(f.ui.render(), /先登录账号/);
});

test("pagination combines a source's records without sending labels or credentials", async (t) => {
  const f = fixture(t, { intercept(path) {
    if (path === ROOT + "sources") return { contract_version: VERSION, accounts: [{ ...source, records: [records[0]] }], next_cursor: "下一页/1" };
    if (path === ROOT + "sources?cursor=" + encodeURIComponent("下一页/1")) return { contract_version: VERSION, accounts: [{ ...source, records: [records[1]] }], next_cursor: null };
  } });
  await f.ui.toggle(toggle(true));
  assert.match(f.ui.render(), /课程报告通知/);
  assert.match(f.ui.render(), /课程安排通知/);
  assert.match(f.ui.render(), /2 条可选通知/);
  records.forEach((item) => f.ui.input(select(item.input_id)));
  await f.ui.click(click("start"));
  const body = f.requests.find((item) => item.method === "POST").body;
  assert.equal(Object.hasOwn(body, "password"), false);
  assert.equal(Object.hasOwn(body, "text"), false);
});

test("late source responses and terminal callbacks cannot expose a previous account", async (t) => {
  let release, entered;
  const waiting = new Promise((resolve) => { release = resolve; });
  const ready = new Promise((resolve) => { entered = resolve; });
  let slow = true;
  const f = fixture(t, { async intercept(path) {
    if (slow && path === ROOT + "sources") {
      const result = { contract_version: VERSION, accounts: [source], next_cursor: null };
      entered(); await waiting; return result;
    }
  } });
  const loading = f.ui.toggle(toggle(true));
  await ready;
  f.switchAccount(); slow = false; release();
  await loading;
  assert.doesNotMatch(f.ui.render(), /课程报告通知|source:a/);
  await f.ui.toggle(toggle(true));
  assert.match(f.ui.render(), /B 私有通知/);
  assert.equal(f.refreshes, 0);
});

test("reset clears client state but does not stop an already created backend run", async (t) => {
  const f = fixture(t);
  await f.ui.toggle(toggle(true));
  f.ui.input(select(records[0].input_id));
  await f.ui.click(click("start"));
  f.ui.reset();
  assert.equal(f.timers.size, 0);
  assert.equal(f.ui.isBusy(), false);
  assert.equal(f.requests.some((item) => item.path.endsWith("/stop")), false);
  await f.ui.toggle(toggle(true));
  assert.equal(f.ui.isBusy(), true, "the persisted active run is recovered by reading history");
});

test("an account change during terminal refresh invalidates its callback and success notice", async (t) => {
  let entered, release;
  const ready = new Promise((resolve) => { entered = resolve; });
  const waiting = new Promise((resolve) => { release = resolve; });
  const f = fixture(t, { async finished(isCurrent) {
    entered(); await waiting;
    assert.equal(isCurrent(), false);
  } });
  await f.ui.toggle(toggle(true));
  f.ui.input(select(records[0].input_id));
  await f.ui.click(click("start"));
  f.setRuns([run("succeeded", 2, [records[0].input_id])]);
  const refreshing = f.ui.load({ directory: false });
  await ready;
  f.switchAccount(); release();
  await refreshing;
  assert.equal(f.refreshes, 0);
  assert.doesNotMatch(f.ui.render(), /识别结果已刷新|课程报告通知/);
});

test("leaving the import page pauses reprocessing polls and returning refreshes completion", async t => {
  const f = fixture(t);
  await f.ui.toggle(toggle(true));
  f.ui.input(select(records[0].input_id));
  await f.ui.click(click("start"));
  assert.equal(f.timers.size, 1);
  f.ui.setVisible(false);
  assert.equal(f.timers.size, 0);
  assert.equal(f.ui.isBusy(), true, "hiding the page must not stop the backend run");
  f.setRuns([run("succeeded", 2, [records[0].input_id])]);
  assert.equal(f.refreshes, 0);
  await f.ui.setVisible(true);
  assert.equal(f.refreshes, 1);
  assert.equal(f.ui.isBusy(), false);
  assert.equal(f.requests.filter(request => request.method === "POST").length, 1);
});

test("returning retries a completion refresh interrupted by leaving the import page", async t => {
  for (const failWhileHidden of [false, true]) {
    let enter, release, attempts = 0;
    const entered = new Promise(resolve => { enter = resolve; });
    const waiting = new Promise(resolve => { release = resolve; });
    t.after(release);
    const f = fixture(t, { async finished() {
      if (++attempts === 1) {
        enter(); await waiting;
        if (failWhileHidden) throw new Error("temporary refresh failure");
      }
    } });
    await f.ui.toggle(toggle(true));
    f.ui.input(select(records[0].input_id));
    await f.ui.click(click("start"));
    f.setRuns([run("succeeded", 2, [records[0].input_id])]);
    const refreshing = f.ui.load({ directory: false });
    await entered;
    f.ui.setVisible(false); release(); await refreshing;
    assert.equal(f.refreshes, 0, "the interrupted callback did not apply both result lists");
    await f.ui.setVisible(true);
    assert.equal(attempts, 2, "returning retries the same completed run");
    assert.equal(f.refreshes, 1);
    assert.match(f.ui.render(), /任务与识别结果已刷新/);
    await f.ui.load();
    assert.equal(f.refreshes, 1, "a successfully applied refresh is consumed only once");
    assert.equal(f.requests.filter(request => request.method === "POST").length, 1);
  }
});

test("a stale completion callback cannot consume a newer refresh after returning", async t => {
  let enter, release, attempts = 0;
  const entered = new Promise(resolve => { enter = resolve; });
  const waiting = new Promise(resolve => { release = resolve; });
  t.after(release);
  const f = fixture(t, { async finished() {
    if (++attempts === 1) { enter(); await waiting; }
  } });
  await f.ui.toggle(toggle(true));
  f.ui.input(select(records[0].input_id));
  await f.ui.click(click("start"));
  f.setRuns([run("succeeded", 2, [records[0].input_id])]);
  const older = f.ui.load({ directory: false });
  await entered; f.ui.setVisible(false);
  await f.ui.setVisible(true);
  assert.equal(f.refreshes, 1, "the new read applies results while the older callback is still pending");
  release(); await older; await f.ui.load();
  assert.equal(attempts, 2);
  assert.equal(f.refreshes, 1);
  assert.equal(f.ui.isBusy(), false);
  assert.equal(f.requests.filter(request => request.method === "POST").length, 1);
});
