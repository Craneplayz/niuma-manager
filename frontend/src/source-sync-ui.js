import { newId } from "./id.js";

const API = "/api/sources/sync-runs";
const VERSION = "source-sync/0.1";
const ACTIVE = new Set(["queued", "crawling", "recognizing"]);
const STATES = ["queued", "running", "succeeded", "failed", "incomplete", "cancelled", "interrupted"];
const LABELS = {
  queued: "等待导入", crawling: "正在采集", recognizing: "正在识别",
  succeeded: "导入完成", partial_success: "部分完成", failed: "导入未完成",
  stopped: "已停止导入", interrupted: "导入已中断",
};
const RECORD_LABELS = {
  queued: "等待识别", running: "正在识别", succeeded: "已识别", failed: "识别失败",
  incomplete: "来源待补全", cancelled: "未处理", interrupted: "已中断",
};
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const needsCollection = (record) => record.state === "incomplete" || record.error?.code === "source_incomplete" || record.error?.code === "source_changed";
const retryable = (record) => !record.recovered && !needsCollection(record) && (["cancelled", "interrupted"].includes(record.state) || record.state === "failed" && record.error?.retryable === true);
const usefulRecord = record => record.added > 0 || record.review > 0 || ["failed", "incomplete", "cancelled", "interrupted"].includes(record.state);
const usefulRun = run => Boolean(run.error || ["failed", "interrupted"].includes(run.state) || run.updated_tasks?.length || run.records.some(usefulRecord));

function checkRun(run) {
  if (run?.contract_version !== VERSION || !/^[A-Za-z0-9_.:-]{1,160}$/.test(run.run_id || "") ||
      !Object.hasOwn(LABELS, run.state) || !Number.isInteger(run.revision) || run.revision < 0 ||
      !Array.isArray(run.records) || !run.counts || run.records.some((record) =>
        typeof record.request_id !== "string" || !record.request_id || !STATES.includes(record.state) ||
        record.review !== undefined && (!Number.isInteger(record.review) || record.review < 0) ||
        record.recovered !== undefined && typeof record.recovered !== "boolean") ||
      run.updated_tasks !== undefined && (!Array.isArray(run.updated_tasks) || run.updated_tasks.some(task =>
        typeof task.task_id !== "string" || typeof task.label !== "string" || task.course != null && typeof task.course !== "string"))) {
    throw new Error("导入服务版本不匹配，请刷新或更新服务。");
  }
  const expected = Object.fromEntries(STATES.map((state) => [state, run.records.filter((record) => record.state === state).length]));
  expected.total = run.records.length;
  expected.skipped = run.skipped;
  expected.added = run.records.reduce((total, record) => total + record.added, 0);
  if (run.counts.review !== undefined) expected.review = run.records.reduce((total, record) => total + (record.review || 0), 0);
  if (run.counts.updated !== undefined) expected.updated = run.updated_tasks?.length || 0;
  if (new Set(run.records.map((record) => record.request_id)).size !== run.records.length ||
      Object.entries(expected).some(([key, count]) => !Number.isInteger(count) || count < 0 || run.counts[key] !== count)) {
    throw new Error("导入进度暂时无法读取，请刷新重试。");
  }
  return run;
}

function checkLast(value) {
  if (value == null) return null;
  if (!/^[A-Za-z0-9_.:-]{1,160}$/.test(value.run_id || "") || !Object.hasOwn(LABELS, value.state) || ACTIVE.has(value.state) ||
      !Number.isFinite(Date.parse(value.updated_at)) || typeof value.changed !== "boolean")
    throw new Error("导入进度暂时无法读取，请刷新重试。");
  return value;
}

/** Durable teaching import; the server performs recognition and task creation. */
export function createSourceSyncUI(host) {
  const esc = host.escapeHtml || escape;
  const setTimer = host.setTimer || globalThis.setTimeout;
  const clearTimer = host.clearTimer || globalThis.clearTimeout;
  let runs = [], loaded = false, loading = false, writing = false, refreshing = false, visible = false;
  let error = null, timer = null, generation = 0, readSequence = 0, refreshSequence = 0;
  let account = host.accountVersion(), readController = null, writeController = null;
  let renderedWhileLoading = false;
  let pendingOperation = null, consumed = null, showHistory = false, focusedRun = null, actionInFlight = null;
  const details = new Map();
  let lastCheck = null;
  const manualRuns = new Set(), announced = new Set();
  const active = () => runs.some((run) => ACTIVE.has(run.state));
  const stamp = () => ({ generation, account: host.accountVersion() });
  const current = (value) => value.generation === generation && value.account === host.accountVersion() && host.isAuthenticated() && !host.isDemo();
  const redraw = () => host.render();

  function clearPoll() {
    if (timer !== null) clearTimer(timer);
    timer = null;
  }
  function cancelRead() {
    readSequence++;
    readController?.abort();
    readController = null; loading = false;
    clearPoll();
  }
  function reset() {
    generation++; refreshSequence++;
    cancelRead(); writeController?.abort(); writeController = null;
    account = host.accountVersion();
    runs = []; loaded = writing = refreshing = false;
    error = null; pendingOperation = consumed = focusedRun = actionInFlight = null;
    showHistory = false; details.clear();
    lastCheck = null; manualRuns.clear(); announced.clear();
  }
  function syncAccount() { if (account !== host.accountVersion()) reset(); }
  function isBusy() { syncAccount(); if (loading) renderedWhileLoading = true; return loading || writing || refreshing || active(); }
  function isRunning() { syncAccount(); return writing || active(); }
  function canStop() { syncAccount(); return active() && !writing; }
  function ownsRequest(id) { syncAccount(); return runs.some((run) => run.records.some((record) => record.request_id === id)); }
  function allowed() {
    syncAccount();
    if (host.isDemo()) error = { message: "示例预览不发送导入请求，请登录后操作。" };
    else if (!host.isAuthenticated()) error = { message: "请先登录后导入。" };
    else return true;
    redraw(); return false;
  }
  function report(cause, fallback) {
    const formatted = host.describeError?.(cause);
    if (formatted?.message && formatted.kind !== "unknown") return formatted;
    if (/^(导入服务版本不匹配|导入进度暂时无法读取)/.test(cause?.message || "")) return { message: cause.message, details: "" };
    return { message: fallback, details: "" };
  }
  function errorMarkup(value) {
    if (!value?.message) return "";
    return `<p class="error" role="alert">${esc(value.message)}</p>${value.details ? `<details class="import-error-details"><summary>查看原因</summary><p class="status-note">${esc(value.details)}</p></details>` : ""}`;
  }
  function poll() {
    clearPoll();
    if (!visible || loading || writing || refreshing || !current(stamp())) return;
    timer = setTimer(() => {
      timer = null;
      if (host.isImportBusy()) { poll(); return; }
      return load();
    }, active() || !loaded ? 2000 : 30000);
    timer?.unref?.();
  }
  function latestAttempts() {
    const latest = new Map();
    // The server orders attempts newest first; timestamps may tie.
    for (const run of runs) for (const record of run.records) {
      if (!latest.has(record.request_id)) latest.set(record.request_id, { run, record });
    }
    return latest;
  }
  function acceptList(incoming) {
    const previous = new Map(runs.map((run) => [run.run_id, run]));
    runs = incoming.map((run) => {
      const saved = previous.get(run.run_id);
      return saved && saved.revision > run.revision ? saved : run;
    });
    loaded = true;
  }
  function acceptRun(run) {
    const index = runs.findIndex((item) => item.run_id === run.run_id);
    if (index < 0) runs = [run, ...runs].slice(0, 20);
    else if (runs[index].revision <= run.revision) runs = runs.map((item, at) => at === index ? run : item);
    loaded = true;
  }
  function notifyQuiet() {
    for (const id of manualRuns) {
      const run = runs.find(item => item.run_id === id);
      const quiet = run ? run.state === "succeeded" && !usefulRun(run)
        : lastCheck?.run_id === id && lastCheck.state === "succeeded" && !lastCheck.changed;
      if (quiet && !announced.has(id)) {
        announced.add(id); manualRuns.delete(id);
        if (focusedRun === id) focusedRun = null;
        host.toast?.("已检查，没有新内容");
      }
    }
  }
  async function changed(valid) {
    const fingerprint = JSON.stringify(runs);
    if (!valid() || fingerprint === consumed) return true;
    if (!runs.length && consumed === null) { consumed = fingerprint; return true; }
    const sequence = ++refreshSequence;
    refreshing = true;
    try {
      const refreshed = await host.onChanged(valid);
      if (!valid()) return false;
      if (refreshed === null) return false;
      if (refreshed === false) throw new Error("refresh incomplete");
      consumed = fingerprint;
      return true;
    } catch {
      if (valid()) error = { message: "导入结果已保存，刷新失败，请刷新进度重试。" };
      return false;
    } finally { if (sequence === refreshSequence) refreshing = false; }
  }
  async function load() {
    if (!allowed() || writing || loading) return false;
    if (host.isImportBusy()) { poll(); return false; }
    clearPoll();
    const value = stamp(), sequence = ++readSequence, controller = new AbortController();
    const valid = () => current(value) && sequence === readSequence;
    const previous = JSON.stringify([runs, lastCheck]), wasLoaded = loaded, hadError = Boolean(error);
    readController = controller; loading = true; renderedWhileLoading = false; error = null;
    try {
      const response = await host.api(API, undefined, "GET", { signal: controller.signal });
      if (!valid()) return false;
      if (response?.contract_version !== VERSION || !Array.isArray(response.runs) || response.runs.length > 20) throw new Error("导入服务版本不匹配，请刷新或更新服务。");
      lastCheck = checkLast(response.last_check);
      acceptList(response.runs.map(checkRun));
      focusedRun = runs.find(run => ACTIVE.has(run.state))?.run_id || focusedRun;
      const refreshed = await changed(valid);
      if (valid() && refreshed) notifyQuiet();
      return refreshed;
    } catch (cause) {
      if (valid()) error = report(cause, "暂时无法读取导入进度，后台可能仍在运行，请刷新重试。");
      return false;
    } finally {
      if (valid()) {
        loading = false; readController = null;
        if (renderedWhileLoading || error || hadError || !wasLoaded || previous !== JSON.stringify([runs, lastCheck])) redraw();
        poll();
      }
    }
  }
  async function act(action, id = null, selection = null) {
    if (!allowed() || writing) return false;
    if (action !== "stop" && (active() || loading || refreshing || host.isImportBusy())) {
      error = { message: "请先等待当前导入完成，再继续操作。" }; redraw(); return false;
    }
    let selected = selection;
    if (action !== "start") {
      const run = runs.find((item) => item.run_id === id);
      if (!run) return false;
      if (action === "stop" && !ACTIVE.has(run.state)) return false;
      if (action === "retry") {
        const latest = latestAttempts();
        const eligible = run.records.filter((record) => latest.get(record.request_id)?.run === run && retryable(record));
        selected = selection ? eligible.filter((record) => selection.includes(record.request_id)).map((record) => record.request_id) : eligible.map((record) => record.request_id);
        if (!selected.length || selection && selected.length !== selection.length) return false;
      }
    }
    cancelRead();
    const value = stamp(), controller = new AbortController();
    const signature = JSON.stringify([action, id, selected ? [...selected].sort() : null]);
    if (action !== "stop" && pendingOperation?.signature !== signature) pendingOperation = { signature, id: newId() };
    const body = action === "stop" ? { contract_version: VERSION } : {
      contract_version: VERSION, operation_id: pendingOperation.id, consent: true,
      ...(action === "start" ? { sources: ["teaching_web"] } : { request_ids: selected }),
    };
    writeController = controller; writing = true; error = null; actionInFlight = action;
    if (action === "start") focusedRun = null;
    redraw();
    let requested = false;
    try {
      if (action !== "stop") {
        await host.flushStore();
        if (!current(value)) return false;
      }
      requested = true;
      const run = await host.api(action === "start" ? API : API + "/" + encodeURIComponent(id) + "/" + action, body, "POST", { signal: controller.signal });
      if (!current(value)) return false;
      acceptRun(checkRun(run));
      focusedRun = run.run_id;
      if (action !== "stop") manualRuns.add(run.run_id);
      if (action !== "stop") pendingOperation = null;
      await changed(() => current(value));
      if (current(value)) notifyQuiet();
      return current(value);
    } catch (cause) {
      if (current(value)) error = !requested && action !== "stop"
        ? { message: "任务修改尚未保存，请先处理保存问题再导入。" }
        : report(cause, action === "stop" ? "停止导入未完成，请刷新进度后重试。" : "导入请求暂时未能确认，请刷新进度或重试。");
      return false;
    } finally {
      if (current(value)) { writing = false; actionInFlight = null; writeController = null; redraw(); poll(); }
    }
  }
  const start = () => act("start");
  function stop() {
    syncAccount();
    const run = runs.find(run => ACTIVE.has(run.state));
    return run ? act("stop", run.run_id) : Promise.resolve(true);
  }
  function guidance(record) {
    if (needsCollection(record)) return "请重新获取来源；重试识别不会补齐原文或附件。";
    if (record.error?.code === "model_configuration" || record.error?.code === "agent_unavailable" && !record.error.retryable) return "请修复模型配置后重新获取来源。";
    if (["cancelled", "interrupted"].includes(record.state)) return "已保存内容保留，可继续处理。";
    if (record.error?.code === "agent_timeout") return "识别超时，可稍后重试。";
    return retryable(record) ? "识别暂时失败，可重试。" : "请检查服务后重新获取来源。";
  }
  function recordMarkup(record, run, latest) {
    const newest = latest.get(record.request_id), superseded = newest?.run !== run;
    const problem = !superseded && !record.recovered && ["failed", "incomplete", "cancelled", "interrupted"].includes(record.state);
    const outcome = [record.added ? `新增 ${record.added} 项` : "", record.review ? `待核对 ${record.review} 项` : ""].filter(Boolean).join(" · ") || RECORD_LABELS[record.state];
    return `<div class="candidate-row"><div class="candidate-copy"><h3>${esc(record.label)}</h3><p class="status-note">${esc([record.course, record.recovered ? "后续已恢复" : outcome].filter(Boolean).join(" · "))}${superseded ? " · 后续" + esc(RECORD_LABELS[newest.record.state]) : ""}</p>${problem ? errorMarkup(report(record.error, guidance(record))) : ""}</div>${problem && !ACTIVE.has(run.state) && retryable(record) ? `<div class="candidate-actions"><button class="btn quiet" type="button" data-source-sync-action="retry" data-source-sync-run="${esc(run.run_id)}" data-source-sync-request="${esc(record.request_id)}" ${isBusy() || host.isImportBusy() ? "disabled" : ""}>${["cancelled", "interrupted"].includes(record.state) ? "继续本条" : "重试本条"}</button></div>` : ""}</div>`;
  }
  function runMarkup(run, latest, compact = false) {
    const counts = run.counts, done = counts.total - counts.queued - counts.running;
    const records = run.records.filter((record) => latest.get(record.request_id)?.run === run);
    const pending = records.filter(retryable), problems = records.filter((record) => !record.recovered && ["failed", "incomplete", "cancelled", "interrupted"].includes(record.state));
    const recovered = run.records.filter((record) => record.recovered || record.state !== "succeeded" && latest.get(record.request_id)?.run !== run && latest.get(record.request_id)?.record.state === "succeeded").length;
    const recollect = records.some(needsCollection) || Boolean(run.error) || !records.length && ["stopped", "interrupted"].includes(run.state);
    const blocked = isBusy() || host.isImportBusy();
    const summary = [ACTIVE.has(run.state) && counts.total ? `已处理 ${done} / ${counts.total}` : "", counts.added ? `新增 ${counts.added} 项` : "", counts.review ? `待核对 ${counts.review} 项` : "", counts.updated ? `更新 ${counts.updated} 项` : "", problems.some(r => r.state === "failed") ? `失败 ${problems.filter(r => r.state === "failed").length} 条` : "", counts.incomplete ? `待补全 ${counts.incomplete} 条` : ""].filter(Boolean).join(" · ");
    const shown = run.records.filter(usefulRecord);
    const updates = (run.updated_tasks || []).map(task => `<div class="candidate-row"><div class="candidate-copy"><h3>${esc(task.label)}</h3><p class="status-note">${esc([task.course, "任务已更新"].filter(Boolean).join(" · "))}</p></div></div>`).join("");
    const progress = ACTIVE.has(run.state) ? `<progress ${counts.total && run.state !== "crawling" ? `max="${counts.total}" value="${done}"` : ""} aria-label="${esc(counts.total ? `已处理 ${done} / ${counts.total} 条通知` : LABELS[run.state])}"></progress>` : "";
    const actions = ACTIVE.has(run.state) ? "" : `${pending.length ? `<button class="btn quiet" type="button" data-source-sync-action="retry" data-source-sync-run="${esc(run.run_id)}" ${blocked ? "disabled" : ""}>继续未完成 · ${pending.length}</button>` : ""}${recollect ? `<button class="btn quiet" type="button" data-source-sync-action="collect" ${blocked ? "disabled" : ""}>重新获取来源</button>` : ""}`;
    return `<article class="sync-run ${compact ? "sync-run-current" : ""}"><div class="sync-run-heading"><div class="sync-run-status" role="status"><strong>${esc(LABELS[run.state])}</strong>${summary ? `<span class="status-note">${esc(summary)}</span>` : ""}${progress}</div>${actions ? `<div class="candidate-actions">${actions}</div>` : ""}</div>${run.error ? errorMarkup(report(run.error, "本轮导入未完成，请重新获取来源。")) : ""}${recovered ? `<p class="status-note">后续已恢复 ${recovered} 条；本轮记录保留。</p>` : ""}${shown.length || updates ? `<details class="import-history" data-source-sync-details="${esc(run.run_id)}" ${(details.get(run.run_id) ?? (!compact && Boolean(problems.length))) ? "open" : ""}><summary>处理详情 · ${shown.length + (run.updated_tasks?.length || 0)}</summary>${shown.map((record) => recordMarkup(record, run, latest)).join("")}${updates}</details>` : ""}</article>`;
  }
  function currentRun() {
    return runs.find(run => ACTIVE.has(run.state)) || runs.find(run => run.run_id === focusedRun && usefulRun(run));
  }
  function renderProgress() {
    syncAccount();
    if (host.isDemo() || !host.isAuthenticated()) return "";
    const run = currentRun(), starting = ["start", "retry"].includes(actionInFlight);
    if (!run && !error && !starting && !lastCheck) return "";
    const checked = lastCheck && !run && !starting ? `<p class="status-note" data-source-sync-last-check>上次检查 <time datetime="${esc(lastCheck.updated_at)}">${esc(new Date(lastCheck.updated_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }))}</time> · ${lastCheck.state === "succeeded" ? lastCheck.changed ? "有新结果" : "无新内容" : esc(LABELS[lastCheck.state])}</p>` : "";
    return `<div class="teaching-progress" data-source-sync-progress aria-label="教学网导入进度">${errorMarkup(error)}${error ? `<button class="btn quiet" type="button" data-source-sync-action="refresh" ${loading || writing || refreshing ? "disabled" : ""}>刷新进度</button>` : ""}${starting && !run ? '<p class="status-note" role="status">正在启动识别…</p>' : ""}${run ? runMarkup(run, latestAttempts(), true) : ""}${checked}</div>`;
  }
  function renderHistory() {
    syncAccount();
    if (host.isDemo() || !host.isAuthenticated()) return "";
    const current = currentRun(), history = runs.filter(run => run !== current && usefulRun(run));
    if (!history.length) return "";
    const latest = latestAttempts();
    return `<details class="import-history" data-source-sync-history ${showHistory ? "open" : ""}><summary>历史导入 · ${history.length}</summary>${history.map(run => runMarkup(run, latest)).join("")}</details>`;
  }
  async function click(button) {
    const data = button?.dataset, action = data?.sourceSyncAction;
    if (!action) return false;
    if (action === "refresh") await load();
    else if (action === "collect") await start();
    else if (action === "retry") await act(action, data.sourceSyncRun, data.sourceSyncRequest ? [data.sourceSyncRequest] : null);
    return true;
  }
  function toggle(event) {
    const target = event.target;
    if (target?.isConnected === false) return false;
    if (target?.dataset?.sourceSyncHistory !== undefined) { showHistory = target.open; return true; }
    if (target?.dataset?.sourceSyncDetails) { details.set(target.dataset.sourceSyncDetails, target.open); return true; }
    return false;
  }
  function setVisible(value) {
    syncAccount();
    const next = Boolean(value), entering = next && !visible;
    visible = next;
    if (!visible) { cancelRead(); return; }
    if (entering) { focusedRun = null; return load(); }
    if (!loaded && !loading && !writing && !error) return load();
    poll();
  }
  return { renderProgress, renderHistory, click, toggle, reset, load, start, stop, canStop, isBusy, isRunning, ownsRequest, setVisible };
}
