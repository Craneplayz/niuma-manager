import { newId } from "./id.js";
import { describeUserError, userErrorMarkup } from "./user-errors.js";

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
const needsCollection = (record) => record.state === "incomplete" || ["source_incomplete", "source_changed", "source_missing"].includes(record.error?.code);
const retryable = (record) => !record.recovered && !needsCollection(record) && (["cancelled", "interrupted"].includes(record.state) || record.state === "failed" && record.error?.retryable === true);
const recoverable = record => !record.recovered && (retryable(record) || needsCollection(record));
const unresolved = record => !record.recovered && ["failed", "incomplete", "cancelled", "interrupted"].includes(record.state);
const usefulRecord = record => record.added > 0 || record.review > 0 || unresolved(record);
const runError = run => run.error && !run.records.some(record => record.recovered && record.error?.code === run.error.code && record.error?.message === run.error.message) ? run.error : null;
const usefulRun = run => Boolean(runError(run) || !run.records.length && ["failed", "interrupted"].includes(run.state) || run.updated_tasks?.length || run.records.some(usefulRecord));
const validItems = (items, count, added = false) => items === undefined || Array.isArray(items) && items.length === count &&
  new Set(items.map(item => item?.candidate_id)).size === count && items.every(item => item &&
    typeof item.candidate_id === "string" && item.candidate_id && typeof item.label === "string" &&
    (item.course == null || typeof item.course === "string") && (!added || item.task_id === null || typeof item.task_id === "string"));
const UPDATE_FIELDS = { title: "名称", description: "说明", course: "课程", due: "日期", status: "状态", completedAt: "完成时间", recycleBin: "回收状态", content_type: "内容类型" };

function checkRun(run) {
  if (run?.contract_version !== VERSION || !/^[A-Za-z0-9_.:-]{1,160}$/.test(run.run_id || "") ||
      !Object.hasOwn(LABELS, run.state) || !Number.isInteger(run.revision) || run.revision < 0 ||
      !Array.isArray(run.records) || !run.counts || run.records.some((record) =>
        typeof record.request_id !== "string" || !record.request_id || !STATES.includes(record.state) ||
        record.review !== undefined && (!Number.isInteger(record.review) || record.review < 0) ||
        record.recovered !== undefined && typeof record.recovered !== "boolean" ||
        !validItems(record.added_items, record.added, true) || !validItems(record.review_items, record.review || 0)) ||
      run.updated_tasks !== undefined && (!Array.isArray(run.updated_tasks) || run.updated_tasks.some(task =>
        typeof task.task_id !== "string" || typeof task.label !== "string" || task.course != null && typeof task.course !== "string" ||
        task.changed_fields !== undefined && (!Array.isArray(task.changed_fields) || task.changed_fields.some(field => !Object.hasOwn(UPDATE_FIELDS, field)))))) {
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
  const categories = new Map(), selectedProblems = new Map();
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
    showHistory = false; categories.clear(); selectedProblems.clear();
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
    const formatted = host.describeError?.(cause) || describeUserError(cause, "teaching_sync");
    if (formatted?.message && formatted.kind !== "unknown") return formatted;
    if (/^(导入服务版本不匹配|导入进度暂时无法读取)/.test(cause?.message || "")) return { message: cause.message, details: "" };
    return { message: fallback, details: "" };
  }
  function errorMarkup(value, context) {
    return userErrorMarkup(value, "teaching_sync", context);
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
      if (["retry", "recover"].includes(action)) {
        const latest = latestAttempts();
        const eligible = run.records.filter((record) => latest.get(record.request_id)?.run === run && (action === "recover" ? recoverable(record) : retryable(record)));
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
  function recoveryPlan(run, latest = latestAttempts()) {
    const records = run.records.filter(record => latest.get(record.request_id)?.run === run && recoverable(record));
    return { records, whole: !run.records.length && Boolean(run.error || ["stopped", "interrupted"].includes(run.state)) };
  }
  function recover(id, request = null) {
    syncAccount();
    const run = runs.find(item => item.run_id === id);
    if (!run || ACTIVE.has(run.state)) return Promise.resolve(false);
    const plan = recoveryPlan(run), selected = request ? plan.records.filter(record => record.request_id === request) : plan.records;
    if (selected.length) return act(selected.some(needsCollection) ? "recover" : "retry", id, selected.map(record => record.request_id));
    return !request && plan.whole ? start() : Promise.resolve(false);
  }
  function guidance(record) {
    if (needsCollection(record)) return "来源内容尚未完整取得，处理时会先重新获取这条来源。";
    if (record.error?.code === "model_configuration" || record.error?.code === "agent_unavailable" && !record.error.retryable) return "请修复模型配置后重新获取来源。";
    if (["cancelled", "interrupted"].includes(record.state)) return "已保存内容保留，可继续处理。";
    if (record.error?.code === "agent_timeout") return "识别超时，可稍后重试。";
    return retryable(record) ? "识别暂时失败，可重试。" : "请检查服务后重新获取来源。";
  }
  function problemsFor(run) {
    const problems = run.records.filter(unresolved), failure = runError(run);
    if (failure && !problems.some(record => record.error?.code === failure.code && record.error?.message === failure.message))
      problems.push({ label: "本轮导入", error: failure, state: "failed" });
    return problems;
  }
  function resultItems(run, category) {
    if (category === "updated") return { items: run.updated_tasks || [], missing: 0 };
    if (category === "error") return { items: problemsFor(run), missing: 0 };
    const field = category === "added" ? "added_items" : "review_items";
    let missing = 0;
    const items = run.records.flatMap(record => {
      if (!record[field]) missing += record[category] || 0;
      return record[field] || [];
    });
    return { items, missing };
  }
  function problemMarkup(record, run, latest) {
    const newest = record.request_id && latest.get(record.request_id);
    const superseded = newest && newest.run !== run;
    const recovered = record.recovered || superseded && newest.record.state === "succeeded";
    const blocked = isBusy() || host.isImportBusy();
    const formatted = report(needsCollection(record) ? { ...record.error, code: "source_incomplete" } : record.error, guidance(record));
    const status = recovered ? "后续已恢复。" : superseded ? `后续${RECORD_LABELS[newest.record.state]}。` : null;
    return `<div class="sync-problem" role="region" aria-label="${esc(record.label)}的处理情况"><strong>${esc(record.label)}</strong>${status ? `<p class="status-note">${esc(status)}</p>` : errorMarkup(formatted, JSON.stringify(["source-sync", run.run_id, record.request_id]))}${!status && !ACTIVE.has(run.state) && recoverable(record) && record.request_id ? `<button class="btn quiet" type="button" data-source-sync-action="recover" data-source-sync-run="${esc(run.run_id)}" data-source-sync-request="${esc(record.request_id)}" ${blocked ? "disabled" : ""}>处理本条</button>` : ""}</div>`;
  }
  function resultMarkup(run, category, latest) {
    const { items, missing } = resultItems(run, category);
    const list = items.map((item, index) => {
      const action = category === "error" ? "problem" : category === "review" ? "open-review" : "open-task";
      const identity = category === "error" ? String(index) : category === "review" ? item.candidate_id : item.task_id;
      const changes = category === "updated" ? [...new Set((item.changed_fields || []).map(field => UPDATE_FIELDS[field]))].join("、") : "";
      const note = changes ? `${changes}已更新` : category === "error" && item.recovered ? "已恢复" : "";
      const title = [item.label, item.course, note].filter(Boolean).join(" · ");
      return `<li><button id="sync-item-${esc(run.run_id)}-${category}-${index}" class="sync-result-name" type="button" data-source-sync-action="${action}" data-source-sync-run="${esc(run.run_id)}" data-source-sync-item="${esc(identity || "")}" title="${esc(title)}" ${category === "error" ? `aria-expanded="${selectedProblems.get(run.run_id) === (item.request_id || "run")}" aria-controls="sync-problem-${esc(run.run_id)}"` : ""}><span>${esc(item.label)}</span>${note ? `<small>${esc(note)}</small>` : ""}</button></li>`;
    }).join("");
    const selected = items.find(item => (item.request_id || "run") === selectedProblems.get(run.run_id));
    return `<div id="sync-results-${esc(run.run_id)}-${category}" class="sync-results" role="region" aria-label="${{added:"新增",review:"待核对",updated:"更新",error:"错误"}[category]}内容">${list ? `<ul class="sync-result-list">${list}</ul>` : ""}${missing ? `<p class="status-note">${missing} 项旧记录未保存具体名称。</p>` : ""}${category === "error" ? `<div id="sync-problem-${esc(run.run_id)}">${selected ? problemMarkup(selected, run, latest) : ""}</div>` : ""}</div>`;
  }
  function runMarkup(run, latest, compact = false) {
    const counts = run.counts, done = counts.total - counts.queued - counts.running;
    const plan = recoveryPlan(run, latest);
    const blocked = isBusy() || host.isImportBusy();
    const groups = [["added", "新增", counts.added, "项"], ["review", "待核对", counts.review, "项"], ["updated", "更新", counts.updated, "项"], ["error", "错误", problemsFor(run).length, "条"]].filter(([, , count]) => count > 0);
    const selected = categories.get(run.run_id);
    const category = groups.some(([key]) => key === selected) ? selected : null;
    const summary = groups.map(([key, label, count, unit]) => `<button id="sync-category-${esc(run.run_id)}-${key}" class="sync-category" type="button" data-source-sync-action="category" data-source-sync-run="${esc(run.run_id)}" data-source-sync-category="${key}" aria-expanded="${category === key}" aria-controls="sync-results-${esc(run.run_id)}-${key}">${label} ${count} ${unit}</button>`).join("");
    const progress = ACTIVE.has(run.state) ? `<progress ${counts.total && run.state !== "crawling" ? `max="${counts.total}" value="${done}"` : ""} aria-label="${esc(counts.total ? `已处理 ${done} / ${counts.total} 条通知` : LABELS[run.state])}"></progress>` : "";
    const actions = !ACTIVE.has(run.state) && (plan.records.length || plan.whole) ? `<button class="btn quiet" type="button" data-source-sync-action="recover" data-source-sync-run="${esc(run.run_id)}" ${blocked ? "disabled" : ""}>处理未完成${plan.records.length ? ` · ${plan.records.length}` : ""}</button>` : "";
    return `<article class="sync-run ${compact ? "sync-run-current" : ""}"><div class="sync-run-heading"><div class="sync-run-status"><strong role="status">${esc(LABELS[run.state])}</strong>${ACTIVE.has(run.state) && counts.total ? `<span class="status-note">已处理 ${done} / ${counts.total}</span>` : ""}${summary ? `<div class="sync-categories" role="group" aria-label="查看本轮导入结果">${summary}</div>` : ""}${progress}</div>${actions ? `<div class="candidate-actions">${actions}</div>` : ""}</div>${groups.map(([key]) => category === key ? resultMarkup(run, key, latest) : `<div id="sync-results-${esc(run.run_id)}-${key}" hidden></div>`).join("")}</article>`;
  }
  function currentRun() {
    return runs.find(run => ACTIVE.has(run.state)) || runs.find(run => run.run_id === focusedRun && usefulRun(run));
  }
  function renderProgress() {
    syncAccount();
    if (host.isDemo() || !host.isAuthenticated()) return "";
    const run = currentRun(), starting = ["start", "retry", "recover"].includes(actionInFlight);
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
    if (["category", "problem", "open-task", "open-review"].includes(action)) {
      syncAccount();
      const run = runs.find(item => item.run_id === data.sourceSyncRun);
      if (!run) return true;
      if (action === "category") {
        const category = data.sourceSyncCategory;
        if (!["added", "review", "updated", "error"].includes(category) || !resultItems(run, category).items.length && !resultItems(run, category).missing) return true;
        categories.set(run.run_id, categories.get(run.run_id) === category ? null : category);
        selectedProblems.delete(run.run_id);
        redraw();
      } else if (action === "problem") {
        const index = Number(data.sourceSyncItem);
        const record = problemsFor(run)[index];
        if (!record) return true;
        const identity = record.request_id || "run";
        selectedProblems.set(run.run_id, selectedProblems.get(run.run_id) === identity ? null : identity);
        redraw();
      } else if (action === "open-task") {
        const item = [...resultItems(run, "added").items, ...(run.updated_tasks || [])].find(item => item.task_id && item.task_id === data.sourceSyncItem);
        if (item) host.openTask?.(item.task_id);
        else host.toast?.("这条任务或通知已不在当前列表中");
      } else {
        const item = resultItems(run, "review").items.find(item => item.candidate_id === data.sourceSyncItem);
        if (item) await host.openReview?.(item.candidate_id);
      }
      return true;
    }
    if (action === "refresh") await load();
    else if (action === "recover") await recover(data.sourceSyncRun, data.sourceSyncRequest);
    else if (action === "retry") await act(action, data.sourceSyncRun, data.sourceSyncRequest ? [data.sourceSyncRequest] : null);
    return true;
  }
  function toggle(event) {
    const target = event.target;
    if (target?.isConnected === false) return false;
    if (target?.dataset?.sourceSyncHistory !== undefined) { showHistory = target.open; return true; }
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
