import { newId } from "./id.js";

const API = "/api/recognition/reprocessing/";
const VERSION = "reprocessing/0.1";
const ACTIVE = new Set(["queued", "running", "stopping"]);
const LABELS = {
  queued: "排队中", running: "识别中", stopping: "正在停止", succeeded: "已完成",
  partial_success: "部分完成", failed: "识别失败", stopped: "已停止", interrupted: "已中断",
  cancelled: "未处理", superseded: "已被新运行替代",
};
const RESULT_LABELS = { success: "已识别", no_tasks: "无任务", failed: "识别失败", unknown: "尚未识别" };
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

/** Folded saved-record reprocessing. Every network dependency belongs to the host. */
export function createReprocessingUI(host) {
  const esc = host.escapeHtml || escape;
  const setTimer = host.setTimer || globalThis.setTimeout;
  const clearTimer = host.clearTimer || globalThis.clearTimeout;
  let expanded = false, loaded = false, loading = false, writing = false, visible = true;
  let accounts = [], runs = [], source = "", selected = new Set(), error = "", notice = "";
  let generation = 0, account = host.accountVersion(), readSequence = 0;
  let readController = null, writeController = null, timer = null, pendingOperation = null;
  const tracked = new Set(), reported = new Set();

  const active = () => runs.some((run) => ACTIVE.has(run.state));
  const isBusy = () => { syncAccount(); return loading || writing || active(); };
  const stamp = () => ({ generation, account: host.accountVersion() });
  const current = (value) => value.generation === generation && value.account === host.accountVersion() && !host.isDemo() && host.isAuthenticated();
  const redraw = () => host.render();
  const currentRecords = () => accounts.find((item) => item.source_account_id === source)?.records || [];

  function clearPoll() {
    if (timer !== null) clearTimer(timer);
    timer = null;
  }
  function cancelRead() {
    readSequence++;
    readController?.abort();
    readController = null;
    loading = false;
    clearPoll();
  }
  function reset() {
    generation++;
    cancelRead();
    writeController = null;
    account = host.accountVersion();
    expanded = loaded = writing = false;
    accounts = []; runs = []; source = ""; selected.clear();
    tracked.clear(); reported.clear(); pendingOperation = null;
    error = notice = "";
  }
  function syncAccount() {
    if (account !== host.accountVersion()) reset();
  }
  function allowed() {
    syncAccount();
    if (host.isDemo()) error = "示例预览不发送重识别请求，请登录后操作。";
    else if (!host.isAuthenticated()) error = "请先登录账号，再选择已保存通知。";
    else return true;
    redraw();
    return false;
  }
  function poll() {
    clearPoll();
    if (visible && expanded && active() && !loading && !writing && current(stamp())) {
      timer = setTimer(() => { timer = null; void load({ directory: false }); }, 2000);
    }
  }
  async function pages(path, key, signal, valid) {
    let cursor = null;
    const items = [], seen = new Set();
    do {
      const page = await host.api(API + path + (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""), undefined, "GET", { signal });
      if (!valid()) return null;
      if (page.contract_version !== VERSION || !Array.isArray(page[key])) throw new Error("重识别记录暂时无法读取，请刷新重试。");
      items.push(...page[key]);
      cursor = page.next_cursor;
      if (cursor && seen.has(cursor)) throw new Error("重识别记录分页未完成，请刷新重试。");
      if (cursor) seen.add(cursor);
    } while (cursor);
    return items;
  }
  function acceptAccounts(entries) {
    const merged = new Map();
    for (const entry of entries) {
      const previous = merged.get(entry.source_account_id);
      if (previous) previous.records.push(...entry.records);
      else merged.set(entry.source_account_id, { ...entry, records: [...entry.records] });
    }
    accounts = [...merged.values()];
    if (!accounts.some((item) => item.source_account_id === source)) {
      source = accounts[0]?.source_account_id || "";
      selected.clear();
    }
    const available = new Set(currentRecords().map((item) => item.input_id));
    selected = new Set([...selected].filter((id) => available.has(id)));
  }
  function acceptRuns(incoming) {
    const merged = new Map(runs.map((run) => [run.run_id, run]));
    for (const run of incoming) {
      const previous = merged.get(run.run_id);
      if (!previous || run.revision >= previous.revision) merged.set(run.run_id, run);
    }
    runs = [...merged.values()].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)) || String(b.run_id).localeCompare(String(a.run_id)));
    for (const run of runs) if (ACTIVE.has(run.state)) tracked.add(run.run_id);
  }
  async function finished(value, valid = () => current(value)) {
    const terminal = runs.filter((run) => !ACTIVE.has(run.state) && tracked.has(run.run_id) && !reported.has(run.run_id));
    if (!visible || !terminal.length || !valid()) return;
    try {
      await host.onFinished(valid);
      if (valid()) {
        terminal.forEach((run) => reported.add(run.run_id));
        notice = "任务与识别结果已刷新。";
      }
    } catch (cause) {
      if (valid()) {
        error = "运行已结束，刷新结果失败，请点刷新记录重试。";
      }
    }
  }
  async function load({ directory = true } = {}) {
    if (!allowed() || writing || loading) return false;
    clearPoll();
    const value = stamp(), sequence = ++readSequence;
    const controller = new AbortController();
    readController = controller; loading = true; error = ""; redraw();
    const valid = () => current(value) && sequence === readSequence;
    try {
      const results = await Promise.all([
        pages("runs", "runs", controller.signal, valid),
        directory ? pages("sources", "accounts", controller.signal, valid) : Promise.resolve(null),
      ]);
      if (!valid()) return false;
      acceptRuns(results[0]);
      if (directory) { acceptAccounts(results[1]); loaded = true; }
      await finished(value, valid);
      return valid();
    } catch (cause) {
      controller.abort();
      if (valid()) error = cause.message || "无法读取重识别记录，请刷新重试。";
      return false;
    } finally {
      if (valid()) {
        loading = false; readController = null; redraw();
        if (!error) poll();
      }
    }
  }
  async function start() {
    if (!allowed() || writing || loading || active()) return;
    if (host.isImportBusy()) { error = "请先等待当前导入完成，再重新识别。"; redraw(); return; }
    if (!selected.size) { error = "请先选择通知。"; redraw(); return; }
    if (selected.size > 100) { error = "每次最多选择 100 条通知。"; redraw(); return; }
    cancelRead();
    const value = stamp(), signature = JSON.stringify([source, [...selected].sort()]);
    if (pendingOperation?.signature !== signature) pendingOperation = { signature, id: newId() };
    const body = { contract_version: VERSION, operation_id: pendingOperation.id, source_account_id: source, input_ids: [...selected] };
    writing = true; error = notice = ""; writeController = new AbortController(); redraw();
    let succeeded = false;
    try {
      await host.flushStore();
      if (!current(value)) return;
      const run = await host.api(API + "runs", body, "POST", { signal: writeController.signal });
      if (!current(value)) return;
      if (run.contract_version !== VERSION || !run.run_id) throw new Error("运行状态暂时无法读取，请刷新记录。");
      tracked.add(run.run_id); acceptRuns([run]); pendingOperation = null;
      notice = "已开始重新识别所选通知。"; succeeded = true;
      await finished(value);
    } catch (cause) {
      if (current(value)) error = cause.message || "重新识别未能开始，请重试。";
    } finally {
      if (current(value)) { writing = false; writeController = null; redraw(); poll(); }
    }
    if (succeeded && current(value)) await load({ directory: false });
  }
  async function stop(id) {
    if (!allowed() || writing) return;
    const run = runs.find((item) => item.run_id === id);
    if (!run || !ACTIVE.has(run.state) || run.state === "stopping") return;
    cancelRead();
    const value = stamp();
    writing = true; error = ""; writeController = new AbortController(); redraw();
    try {
      const stopped = await host.api(API + "runs/" + encodeURIComponent(id) + "/stop", { contract_version: VERSION }, "POST", { signal: writeController.signal });
      if (!current(value)) return;
      acceptRuns([stopped]); notice = "停止请求已提交。";
      await finished(value);
    } catch (cause) {
      if (current(value)) error = cause.message || "停止失败，请重试。";
    } finally {
      if (current(value)) { writing = false; writeController = null; redraw(); poll(); }
    }
  }
  function runMarkup(run) {
    const counts = run.counts, done = counts.total - counts.queued - counts.running;
    const sourceRecords = accounts.find((item) => item.source_account_id === run.source_account_id)?.records || [];
    return `<article class="candidate-row"><div class="candidate-copy"><p class="status-note">${esc(LABELS[run.state] || run.state)} · ${done} / ${counts.total}</p><progress max="${counts.total}" value="${done}" aria-label="已结束 ${done} / ${counts.total} 条通知"></progress><details class="import-history"><summary>处理详情</summary>${run.records.map((record) => `<p class="status-note">${esc(sourceRecords.find((item) => item.input_id === record.input_id)?.label || record.input_id)} · ${esc(LABELS[record.state] || record.state)}${record.old_result_retained ? " · 保留旧结果" : ""}${record.decision_summary?.unresolved ? " · 新差异待核对" : ""}${record.error ? " · " + esc(record.error.message) : ""}</p>`).join("")}</details></div>${ACTIVE.has(run.state) ? `<div class="candidate-actions"><button type="button" class="btn quiet" id="reprocessing-stop-${esc(run.run_id)}" data-reprocess-action="stop" data-reprocess-run="${esc(run.run_id)}" ${writing || run.state === "stopping" ? "disabled" : ""}>${run.state === "stopping" ? "正在停止…" : "停止识别"}</button></div>` : ""}</article>`;
  }
  function render() {
    syncAccount();
    const blocked = loading || writing || active() || host.isImportBusy();
    return `<details class="import-history reprocessing-panel" data-reprocess-panel ${expanded ? "open" : ""}><summary id="reprocessing-toggle">重新识别已有通知${active() ? " · 进行中" : ""}</summary><p class="status-note">重新发送所选已保存内容，不重新抓取网页或附件。教学网明确任务直接加入任务清单，需要核对的结果保留供审核。</p>${error ? `<p class="error" role="alert">${esc(error)}</p>` : ""}${notice ? `<p class="status-note" role="status">${esc(notice)}</p>` : ""}<div class="section-heading"><span class="status-note">${loaded ? `${currentRecords().length} 条可选通知` : "选择服务端已保存的通知"}</span><button type="button" class="btn quiet" id="reprocessing-load" data-reprocess-action="load" ${loading || writing ? "disabled" : ""}>${loading ? "读取中…" : loaded ? "刷新记录" : "读取记录"}</button></div>${loaded ? `${accounts.length > 1 ? `<label class="field">来源<select id="reprocessing-source" data-reprocess-source ${blocked ? "disabled" : ""}>${accounts.map((item) => `<option value="${esc(item.source_account_id)}" ${item.source_account_id === source ? "selected" : ""}>${esc(item.label)}</option>`).join("")}</select></label>` : ""}${currentRecords().length ? `<div style="max-height:280px;overflow-y:auto">${currentRecords().map((item, index) => `<label class="field check-field"><input type="checkbox" id="reprocessing-record-${index}" data-reprocess-record="${esc(item.input_id)}" ${selected.has(item.input_id) ? "checked" : ""} ${blocked ? "disabled" : ""}><span class="reprocessing-record-copy"><strong>${esc(item.label)}</strong><small class="status-note">${esc([item.course_or_project, RESULT_LABELS[item.result_status] || item.result_status, item.policy_version].filter(Boolean).join(" · "))}</small></span></label>`).join("")}</div><button type="button" class="btn" id="reprocessing-start" data-reprocess-action="start" ${blocked || !selected.size ? "disabled" : ""}>${writing ? "正在处理…" : `重新识别所选 ${selected.size} 条`}</button>` : '<p class="status-note">暂无此接口支持的已保存通知。</p>'}` : ""}${runs.slice(0, 5).map(runMarkup).join("")}</details>`;
  }
  async function toggle(event) {
    const target = event.target;
    if (!target?.matches?.("[data-reprocess-panel]") || target.isConnected === false) return false;
    syncAccount();
    if (target.open === expanded) return true;
    expanded = target.open; clearPoll();
    if (expanded) await load({ directory: !loaded });
    return true;
  }
  function input(event) {
    const target = event.target, data = target?.dataset;
    if (!data || (data.reprocessSource === undefined && data.reprocessRecord === undefined)) return false;
    syncAccount();
    if (isBusy() || host.isImportBusy()) return true;
    if (data.reprocessSource !== undefined) { source = target.value; selected.clear(); }
    else if (currentRecords().some((item) => item.input_id === data.reprocessRecord)) {
      if (target.checked) selected.add(data.reprocessRecord); else selected.delete(data.reprocessRecord);
    }
    pendingOperation = null; redraw();
    return true;
  }
  async function click(button) {
    const action = button?.dataset?.reprocessAction;
    if (!action) return false;
    if (action === "load") await load();
    else if (action === "start") await start();
    else if (action === "stop") await stop(button.dataset.reprocessRun);
    return true;
  }
  function setVisible(value) {
    syncAccount();
    const entering = Boolean(value) && !visible;
    visible = Boolean(value);
    if (!visible) { cancelRead(); return; }
    if (entering && expanded) return load({ directory: !loaded });
  }
  return { render, click, input, toggle, reset, load, isBusy, setVisible };
}
