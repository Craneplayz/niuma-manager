import { describeUserError, userErrorMarkup } from "./user-errors.js";
import {
  api,
  session,
  accountVersion,
  flushStore,
  getTaskVersion,
} from "./team-store.js";
import { newId } from "./id.js";
import { renderPageHeading } from "./page-layout.js";
import { candidateEvidenceMarkup, failureSources } from "./import-evidence.js";
import { createMaterialsUI } from "./materials-ui.js";
import { createSourceSyncUI } from "./source-sync-ui.js";
import { createSyncScheduleUI } from "./source-schedule-ui.js";
import { importResultStatus, importStatusMarkup, importTypeMarkup, importCategorySummaries } from "./import-result-status.js";

const actions = {
  create: "新增任务",
  update: "更新任务",
  postpone: "调整截止",
  cancel: "取消任务",
  uncertain: "待确认",
};
const platforms = {
  teaching_web: "教学网",
  manual: "文本",
  email: "邮箱",
  wechat: "微信",
};
const zonedParts = (value) =>
  Object.fromEntries(
    new Intl.DateTimeFormat("sv-SE", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(value)
      .map((part) => [part.type, part.value]),
  );

export function shanghaiNow(value = new Date()) {
  const p = zonedParts(value);
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
}
export function buildTextImport(
  text,
  published = shanghaiNow(),
  id = newId(),
  collectedAt = new Date().toISOString(),
) {
  text = String(text || "").trim();
  if (!text) throw new Error("请先粘贴通知内容");
  if (text.length > 4000) throw new Error("每次最多识别 4000 字，请分段导入");
  let publication = { value: null, precision: "unknown", basis: "unknown" };
  if (published) {
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(published);
    if (
      !dateOnly &&
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(published)
    )
      throw new Error("请填写有效的通知时间");
    const value = dateOnly
      ? published
      : published + (published.length === 16 ? ":00" : "") + "+08:00";
    const stamp = new Date(dateOnly ? value + "T00:00:00+08:00" : value);
    if (
      !Number.isFinite(stamp.getTime()) ||
      (dateOnly
        ? shanghaiNow(stamp).slice(0, 10) !== published
        : shanghaiNow(stamp) !==
          published + (published.length === 16 ? ":00" : ""))
    )
      throw new Error("请填写有效的通知时间");
    publication = {
      value,
      precision: dateOnly ? "date" : "datetime",
      basis: "upstream_unverified",
    };
  }
  return {
    contract_version: "0.2",
    request_id: id,
    records: [
      {
        input_id: "manual:" + id,
        text,
        text_quality: "complete",
        platform: "manual",
        published_at: publication,
        collected_at: collectedAt,
        timezone: "Asia/Shanghai",
        timezone_basis: "caller_default",
        source_url: null,
        source_label: "文本导入",
        source_item_id: null,
        course_or_project: null,
        context: [],
        upstream_hints: null,
      },
    ],
  };
}
export function candidateDue(deadline = {}) {
  if (
    deadline.kind !== "deadline" ||
    !["explicit", "inferred"].includes(deadline.status) ||
    !deadline.value
  )
    return { date: "", time: "" };
  if (deadline.precision === "date") return { date: deadline.value, time: "" };
  if (
    deadline.precision !== "datetime" ||
    !Number.isFinite(Date.parse(deadline.value))
  )
    return { date: "", time: "" };
  const value = new Date(deadline.value),
    p = zonedParts(value);
  return {
    date: `${p.year}-${p.month}-${p.day}`,
    time:
      value.getUTCSeconds() || value.getUTCMilliseconds()
        ? ""
        : `${p.hour}:${p.minute}`,
  };
}
export function buildCandidateResolution(item, values, tasks, tasksVersion) {
  const action = values.action,
    target = tasks.find((task) => task.id === values.target_id);
  if (!["create", "update", "postpone", "cancel"].includes(action))
    throw new Error("请选择处理方式");
  if (
    item.candidate?.notice_type !== "uncertain" &&
    item.candidate?.notice_type !== action
  )
    throw new Error("处理方式已经改变，请重新核对通知");
  if (action === "create" && values.target_id)
    throw new Error("新增任务无需选择已有任务");
  if (action !== "create" && !target) throw new Error("请选择要修改的任务");
  const body = {
    action,
    version: item.version,
    tasks_version: tasksVersion,
    target_id: target?.id || null,
    reviewed: true,
  };
  if (action === "cancel") return body;
  if (values.time && !values.date)
    throw new Error("填写时刻前，请先选择截止日期");
  if (action === "postpone" && !values.date)
    throw new Error("请确认新的截止日期");
  const title =
    action === "postpone" ? target.title : String(values.title || "").trim();
  if (!title) throw new Error("请填写任务名称");
  body.draft = {
    content_type: action === "create"
      ? item.candidate?.content_type || "task"
      : target.content_type || "task",
    title,
    description:
      action === "postpone"
        ? target.notes || ""
        : String(values.description || ""),
    course:
      action === "postpone"
        ? target.course || null
        : String(values.course || "").trim() || null,
    due: values.date
      ? {
          date: values.date,
          precision: values.time ? "datetime" : "date",
          ...(values.time ? { time: values.time } : {}),
        }
      : null,
  };
  return body;
}

export function buildRecognitionRequest(request) {
  if (!request || typeof request !== "object")
    throw new Error("导入记录暂时无法读取，请刷新结果后重试");
  if (["teaching-workspace/0.1", "teaching-run/0.1", "0.4"].includes(request.contract_version)) {
    if (typeof request.request_id !== "string" || !request.request_id.trim() || request.request_id.length > 256)
      throw new Error("教学网导入记录缺少识别编号，请重新采集");
    return {contract_version:"teaching-run/0.1",request_id:request.request_id};
  }
  if (request.contract_version === "0.2") return request;
  throw new Error("采集记录格式暂不受支持，请更新服务后重新采集");
}

export const describeImportError = describeUserError;

const taskFingerprint = (task) =>
  JSON.stringify(
    task && [
      task.id,
      task.title,
      task.notes,
      task.course,
      task.due,
      task.dueTime,
      task.status,
    ],
  );
const incomplete = (batch) =>
  (batch.result?.errors || []).some(
    (error) => error.code === "incomplete_source",
  );

function batchErrorReport(batch) {
  const reports = (batch.result?.errors || []).map(describeImportError);
  return reports.find(report=>report.kind!=="unknown") || {
    kind:"recognition_failed",message:"部分通知识别失败，可重试识别。",details:"",
  };
}

export function createImportUI(host) {
  const esc = host.escapeHtml;
  const formIsOpen = (form) => form?.isConnected !== false && form?.closest?.("dialog")?.open !== false;
  let source = host.getImportSource?.() || "text",
    text = "",
    published = shanghaiNow(),
    batches = [],
    credentials = null,
    emailCredentials = null,
    emailSettingsState = "idle",
    emailSettingsError = "",
    emailSettingsRevision = 0,
    emailSettingsDialogOperation = 0,
    emailMessages = [],
    emailSelected = new Set(),
    emailPreferences = { consent_granted: false, process_all: false },
    emailStartBusy = false,
    emailStartOperationId = null,
    emailRetryOperationId = null,
    emailJobActionBusy = false,
    emailPollError = false,
    emailSnapshotId = null,
    emailCacheInfo = null,
    emailMessageById = new Map(),
    emailLoadedIds = [],
    emailReprocessConfirmIds = [],
    emailRecognitionUpdates = null,
    emailReprocessScope = null,
    emailUpdateUnknown = false,
    emailReprocessOperationId = null,
    emailReprocessError = "",
    emailPreviewOffset = 0,
    emailPageSize = 25,
    emailNextOffset = null,
    emailFilter = "all",
    emailSort = "attention",
    emailMatchedTotal = null,
    emailPreviewRequestId = 0,
    emailWholeRange = false,
    emailManualSelection = false,
    emailTotal = 0,
    emailRange = { limit: 50, start_date: null },
    emailLoadedRange = null,
    emailJob = null,
    emailJobTimer = null,
    emailJobRevision = null,
    emailJobDetailsRevision = null,
    emailJobPollDelay = 1000,
    emailJobVisibilityHandler = null,
    emailJobPolling = false,
    emailJobRequestId = 0,
    emailJobVisibilityDocument = null,
    emailPreviewed = false,
    emailExplicitRangeRead = false,
    emailPreviewBusy = false,
    emailPreviewApplyRange = false,
    emailError = "",
    loaded = false,
    loading = false,
    busy = false,
    stopping = false,
    error = "",
    notice = "",
    progress = "",
    request = null,
    modal = null,
    controller = null,
    revision = 0,
    batchesRevision = 0;
  const historyCategories = new Map(), historyProblems = new Map();
  const current = () => ({ epoch: accountVersion(), revision });
  const valid = (stamp) =>
    stamp.epoch === accountVersion() && stamp.revision === revision;
  function acceptBatches(next) {
    if (!Array.isArray(next)) throw new Error("导入结果暂时无法读取");
    batches = next;
    batchesRevision++;
  }
  const refresh = () => host.render();
  const demo = () => Boolean(host.isDemo?.());
  async function refreshResults(isCurrent = () => true) {
    const stamp = current(), previousBatchesRevision = batchesRevision, settingsRevision = emailSettingsRevision;
    const superseded = () => busy || previousBatchesRevision !== batchesRevision;
    if (superseded()) return null;
    await flushStore();
    if (!valid(stamp) || !isCurrent()) return false;
    await host.refresh();
    if (!valid(stamp) || !isCurrent()) return false;
    if (superseded()) return null;
    const next = await api("/api/recognition/batches");
    if (!valid(stamp) || !isCurrent()) return false;
    if (superseded()) return null;
    acceptBatches(next); loaded = true;
    refresh(); return true;
  }
  const sourceSync = createSourceSyncUI({
    ...host, api, flushStore, accountVersion, isDemo: demo,
    describeError: describeImportError,
    isAuthenticated: () => Boolean(session.user && !session.changing),
    isImportBusy: () => busy,
    render: refresh,
    onChanged: refreshResults,
    getCandidate: id => itemById(id),
    openReview(id) {
      const item = itemById(id);
      if (blocked()) { host.toast("正在处理，请稍候"); return; }
      const status = importResultStatus({ candidate_id: id }, "review", { ...host, getCandidate: itemById });
      if (status.taskId) { host.openTask?.(status.taskId); return; }
      if (item?.state === "pending") openReview(item);
      else host.toast(status.message);
    },
  });
  const syncIsActive = () => sourceSync.isBusy();
  const syncOwnsBatch = batch => batch?.sourceSyncManaged === true || sourceSync.ownsRequest(batch?.request?.request_id);
  const refreshSourceSync = () => sourceSync.load();
  const schedule = createSyncScheduleUI({
    ...host, isDemo: demo, isConnected: () => Boolean(credentials?.configured),
    isBusy: () => blocked() || stopping || sourceSync.isBusy(),
    isRunning: () => sourceSync.isRunning(),
    getProgressLabel: () => sourceSync.progressLabel(),
    canStop: () => sourceSync.canStop(),
    isStopping: () => stopping,
    start: () => sourceSync.start(),
  });
  const materials = createMaterialsUI({
    ...host, isDemo: demo, isVisible: () => source === "files" && (host.isImportVisible?.() ?? true),
    changed: refreshResults,
  });
  // Background teaching runs own their start/stop controls, not the whole import page.
  const blocked = () => loading || busy;
  const credentialsBlocked = () => blocked() || stopping || sourceSync.isBusy() || schedule.isBusy();
  const allowed = () => {
    if (demo()) {
      host.toast("示例预览暂不发送，请登录后导入");
      return false;
    }
    if (!session.user || session.changing) {
      host.toast("请先登录后导入");
      return false;
    }
    return true;
  };
  function emailPointerKey(credentials = emailCredentials) {
    const userId = session.user?.id, address = String(credentials?.address || "").trim().toLowerCase();
    if (!userId || !address) return null;
    let hash = 2166136261, value = `${userId}\0${address}`;
    for (let i = 0; i < value.length; i++) hash = Math.imul(hash ^ value.charCodeAt(i), 16777619);
    return `niuma:email-range:${(hash >>> 0).toString(36)}`;
  }
  function clearEmailPointer(credentials = emailCredentials) {
    const key = emailPointerKey(credentials);
    try { if (key) globalThis.localStorage?.removeItem(key); } catch { /* storage may be disabled */ }
  }
  function saveEmailPointer(range, offset) {
    const key = emailPointerKey();
    if (!key || !range || !Number.isInteger(offset) || offset < 0) return;
    try { globalThis.localStorage?.setItem(key, JSON.stringify({ range, offset, page_size: emailPageSize })); } catch { /* storage may be disabled */ }
  }
  function restoreEmailPointer() {
    const key = emailPointerKey();
    if (!key) return;
    try {
      const value = JSON.parse(globalThis.localStorage?.getItem(key) || "null");
      const range = value?.range, offset = value?.offset, pageSize = value?.page_size ?? 25;
      const validRange = range && ((range.start_date === null && Number.isInteger(range.limit) && range.limit > 0) || (typeof range.start_date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(range.start_date) && range.limit === null));
      if (!validRange || !Number.isInteger(offset) || offset < 0 || ![25,50,100].includes(pageSize) || offset % pageSize !== 0) return;
      emailRange = { limit: range.limit, start_date: range.start_date };
      emailLoadedRange = { ...emailRange };
      emailPreviewOffset = offset; emailPageSize = pageSize;
      emailSnapshotId = null;
    } catch { /* storage may be disabled or malformed */ }
  }
  const allItems = () =>
    batches.flatMap((batch) =>
      (batch.items || []).map((item) => ({ ...item, batch })),
    );
  const itemById = (id) => allItems().find((item) => item.id === id);
  function errorMarkup(value, action = source === "teaching" ? "teaching_sync" : "text_import", context) {
    return userErrorMarkup(value, action, context);
  }
  function reset() {
    revision++;
    emailJobRequestId++;
    controller?.abort();
    controller = null;
    source = host.getImportSource?.() || "text";
    text = "";
    published = shanghaiNow();
    batches = [];
    credentials = null;
    emailCredentials = null;
    emailSettingsState = "idle";
    emailSettingsError = "";
    emailSettingsRevision++;
    emailSettingsDialogOperation++;
    emailMessages = [];
    emailSelected = new Set();
    emailPreferences = { consent_granted: false, process_all: false };
    emailStartBusy = false; emailStartOperationId = null; emailRetryOperationId = null; emailJobActionBusy = false; emailPollError = false;
    emailPreviewRequestId++; emailPreviewOffset = 0; emailPageSize = 25; emailNextOffset = null; emailFilter = "all"; emailSort = "attention"; emailMatchedTotal = null; emailWholeRange = false; emailManualSelection = false; emailTotal = 0; emailRange = { limit: 50, start_date: null }; emailLoadedRange = null; emailJob = null;
    emailSnapshotId = null; emailCacheInfo = null; emailMessageById = new Map(); emailLoadedIds = []; emailReprocessConfirmIds = []; emailReprocessScope = null; emailRecognitionUpdates = null; emailUpdateUnknown = false; emailReprocessOperationId = null; emailReprocessError = "";
    clearTimeout(emailJobTimer); emailJobTimer = null;
    if (emailJobVisibilityHandler) emailJobVisibilityDocument?.removeEventListener?.("visibilitychange", emailJobVisibilityHandler);
    emailJobVisibilityHandler = null; emailJobVisibilityDocument = null; emailJobRevision = null; emailJobDetailsRevision = null; emailJobPollDelay = 1000; emailJobPolling = false;
    emailPreviewed = false;
    emailExplicitRangeRead = false;
    emailPreviewBusy = false; emailPreviewApplyRange = false;
    emailError = "";
    loaded = false;
    loading = false;
    busy = false;
    stopping = false;
    error = "";
    notice = "";
    progress = "";
    request = null;
    modal = null;
    sourceSync.reset(); schedule.reset();
    materials.reset();
    historyCategories.clear(); historyProblems.clear();
  }
  globalThis.window?.addEventListener("niuma:account-reset", reset);
  async function load() {
    if (loading || busy || !session.user || demo()) return;
    const stamp = current(), previousBatchesRevision = batchesRevision, settingsRevision = emailSettingsRevision;
    loading = true;
    error = "";
    refresh();
    const results = await Promise.allSettled([
      api("/api/recognition/batches"),
      api("/api/sources/credentials"),
      api("/api/sources/email/credentials"),
      api("/api/sources/email/preferences"),
    ]);
    if (!valid(stamp)) return;
    if (results[0].status === "fulfilled" && Array.isArray(results[0].value)) {
      if (previousBatchesRevision === batchesRevision) acceptBatches(results[0].value);
    } else error = describeImportError(results[0].reason || "导入结果暂时无法读取");
    if (results[1].status === "fulfilled") credentials = results[1].value;
    else if (!error) error = describeImportError(results[1].reason);
    if (settingsRevision === emailSettingsRevision) {
    if (results[2].status === "fulfilled") emailCredentials = results[2].value;
    else {
      emailCredentials = { configured: false, address: null, username: null };
      if (results[2].reason?.status !== 404) emailError = describeImportError(results[2].reason).message;
    }
    if (results[3]?.status === "fulfilled") { emailPreferences = results[3].value; }
    }
    loading = false;
    loaded = true;
    refresh();
    if (source === "teaching") { void refreshSourceSync(); void schedule.load(); }
    if (source === "email" && emailCredentials?.configured) {
      void refreshEmailJob();
      if (!emailPreviewed) { restoreEmailPointer(); void previewEmail(emailPreviewOffset, { autoSelectInitial: false, cacheOnly: true }); }
    }
    if (source === "files") void materials.load();
  }
  function deadlineLabel(deadline = {}) {
    if (deadline.kind === "reference") return "参考时间，待核对";
    if (deadline.status === "not_provided") return "无截止日期";
    if (deadline.status === "conflict") return "截止时间有冲突";
    const due = candidateDue(deadline);
    if (!due.date) return "截止日期待确认";
    return (
      due.date.replaceAll("-", " / ") +
      (due.time ? " " + due.time : "")
    );
  }
  function evidence(item) {
    return candidateEvidenceMarkup(item);
  }
  function candidateRow(item) {
    const c = item.candidate,
      record = item.batch.request?.records?.find(
        (record) => record.input_id === c.input_id,
      ),
      label = record?.source_label === "用户主动上传的文件" ? "图片与文件" : platforms[record?.platform] || c.sources?.[0]?.label || "通知";
    return `<article class="candidate-row"><div class="candidate-copy"><p class="status-note">${esc(c.content_type === "notice" ? ({create:"新增通知",update:"更新通知",cancel:"取消通知",postpone:"调整日期",uncertain:"待确认"}[c.notice_type] || "通知") : actions[c.notice_type] || "待确认")} · ${esc(deadlineLabel(c.deadline))}</p><h3>${esc(c.title)}</h3><p class="status-note">${esc([c.course_or_project, label].filter(Boolean).join(" · "))}</p>${item.state === "pending" ? evidence(item) : ""}</div><div class="candidate-actions">${item.state === "pending" ? `<button class="btn" type="button" data-import-review="${esc(item.id)}" ${blocked() ? "disabled" : ""}>核对${c.notice_type === "create" ? "并加入" : ""}</button><button class="btn quiet" type="button" data-import-ignore="${esc(item.id)}" ${blocked() ? "disabled" : ""}>忽略</button>` : item.state === "ignored" ? `<button class="btn quiet" type="button" data-import-reopen="${esc(item.id)}" ${blocked() ? "disabled" : ""}>撤回忽略</button>` : '<span class="status-note">已处理</span>'}</div></article>`;
  }
  function teachingPanel() {
    const connected = credentials?.configured;
    return `<section class="teaching-source"><div class="teaching-import"><div class="teaching-account"><h2>教学网</h2><div class="teaching-connection"><span class="status-note">${connected ? "已连接 " + esc(credentials.username) : loading ? "正在读取连接…" : "连接账号，获取课程作业与通知"}</span>${connected ? `<button class="btn quiet" type="button" data-import-action="credentials" ${credentialsBlocked() ? "disabled" : ""}>管理连接</button>` : ""}</div></div>${schedule.render()}</div>${sourceSync.renderProgress()}</section>`;
  }
  function independentHistory() {
    const groups = new Map(), tasks = host.getState().tasks || [];
    for (const batch of batches) {
      if (syncOwnsBatch(batch) || materials.owns(batch.request.request_id) || batch.request.contract_version === "0.3.1") continue;
      const platform = batch.request.records?.[0]?.platform;
      const batchSource = platform === "teaching_web" ? "teaching" : platform === "email" ? "email" : "text";
      if (source === "files" || batchSource !== source) continue;
      const id = batch.importId || batch.request.request_id;
      if (!groups.has(id)) groups.set(id, { id, batches: [], time: batch.importedAt || batch.request.records?.[0]?.collected_at });
      groups.get(id).batches.push(batch);
    }
    return [...groups.values()].map(group => {
      const ids = new Set(group.batches.map(batch => batch.request.request_id));
      const receipts = group.batches.flatMap(batch => batch.items || []);
      const knownCandidates = new Set(receipts.map(item => item.id));
      const added = receipts.filter(item => item.autoApplied === true).map(item => ({
        label: item.candidate.title, candidate_id: item.id,
        task_id: tasks.find(task => task.recognitionHistory?.some(event => event.storageId === item.id))?.id,
      })), updated = [];
      for (const task of tasks) {
        const events = (task.recognitionHistory || []).filter(event => ids.has(event.requestId) && !knownCandidates.has(event.storageId));
        if (events.some(event => event.action === "create")) added.push({ label: task.title, task_id: task.id });
        else if (events.length) updated.push({ label: task.title, task_id: task.id });
      }
      const review = group.batches.flatMap(batch => (batch.items || []).filter(item => item.autoApplied !== true)
        .map(item => ({ label: item.candidate.title, candidate_id: item.id })));
      const problems = group.batches.filter(batch => ["failed", "partial_success"].includes(batch.result?.status)).flatMap(batch => {
        const sources = failureSources(batch);
        return (sources.length ? sources : [{ label: "本次识别", input_id: "batch" }]).map(item => ({ ...item, batch }));
      });
      return { ...group, added, review, updated, problems };
    }).filter(group => group.added.length || group.review.length || group.updated.length || group.problems.length);
  }
  function independentBatchMarkup(group) {
    const collections = { added: group.added, review: group.review, updated: group.updated, error: group.problems };
    const labels = { added: "新增", review: "待核对", updated: "修改", error: "错误" };
    const selected = historyCategories.get(group.id), category = collections[selected]?.length ? selected : null;
    const token = encodeURIComponent(group.id), region = `import-batch-${token}`;
    const summary = importCategorySummaries(collections, { ...host, getCandidate: itemById }).map(({ key, label, count, unit, tone, description }) =>
      `<button id="${esc(region)}-${key}" class="sync-category sync-category-${tone}" type="button" data-import-history-category="${key}" data-import-history-batch="${esc(group.id)}" aria-label="${esc(`${label} ${count} ${unit}`)}" aria-expanded="${category === key}" aria-controls="${esc(region)}" title="${esc(description)}" aria-description="${esc(description)}"><span>${label}</span><span class="sync-category-count" aria-hidden="true">${count}</span></button>`).join("");
    const items = collections[category] || [];
    const names = items.map((item, index) => {
      const status = importResultStatus(item, category, { ...host, getCandidate: itemById });
      const isError = category === "error", expanded = isError && historyProblems.get(group.id) === index;
      const itemId = `${region}-${category}-${index}`, detailId = `${itemId}-detail`;
      const detail = expanded ? `<div id="${esc(detailId)}" class="sync-problem" role="region" aria-labelledby="${esc(itemId)}">${errorMarkup(batchErrorReport(item.batch), source === "teaching" ? "teaching_sync" : "text_import", JSON.stringify(["recognition",item.batch.request.request_id,item.input_id]))}${item.links ? `<p class="status-note">${item.links}</p>` : ""}</div>` : "";
      return `<li${isError ? ` class="sync-error-entry${expanded ? " is-expanded" : ""}"` : ""}><button id="${esc(itemId)}" class="sync-result-name" type="button" data-import-history-item="${index}" data-import-history-batch="${esc(group.id)}" title="${esc([item.label,item.course,status.label].filter(Boolean).join(" · "))}" ${isError ? `aria-expanded="${expanded}"${expanded ? ` aria-controls="${esc(detailId)}"` : ""}` : ""}>${importTypeMarkup(status)}<span class="sync-result-title">${esc(item.label)}</span>${importStatusMarkup(status)}${isError ? `<span class="sync-result-chevron" aria-hidden="true">${host.icon("chevron")}</span>` : ""}</button>${detail}</li>`;
    }).join("");
    const retry = group.batches.filter(batch => ["failed", "partial_success"].includes(batch.result?.status) && !incomplete(batch));
    const results = group.added.length || group.review.length || group.updated.length;
    return `<article class="sync-run sync-run-independent" data-import-history-batch-entry="${esc(group.id)}"><div class="sync-run-heading"><div class="sync-run-status"><strong role="status">${group.problems.length ? results ? "部分完成" : "导入未完成" : "导入完成"}</strong><div class="sync-categories" role="group" aria-label="查看本轮导入结果">${summary}</div></div>${retry.length ? `<div class="candidate-actions"><button class="btn quiet sync-history-retry" type="button" data-import-history-retry="${esc(group.id)}" ${blocked() ? "disabled" : ""}>${host.icon("refresh")}重试识别</button></div>` : ""}</div><div id="${esc(region)}" class="sync-results" role="region" aria-label="${labels[category] || "导入"}内容" ${category ? "" : "hidden"}>${names ? `<ul class="sync-result-list"${category === "error" ? " data-import-error-list" : ""}>${names}</ul>` : ""}</div></article>`;
  }

  function emailWholeRangeSelected() {
    return emailWholeRange || (emailPreferences.process_all && !emailManualSelection);
  }
  function emailNeedsAttention(message) {
    return !message.excluded && (["unprocessed","failed","interrupted"].includes(message.state) || message.pending_review_count > 0 || (!message.state && message.needs_attention));
  }
  function orderedEmailMessages() {
    const receiptOrder = message => {
      const uid = /:(\d+)$/.exec(message.input_id || "");
      return uid ? Number(uid[1]) : Date.parse(message.received_at || "") || 0;
    };
    return emailLoadedIds.map(id => emailMessageById.get(id)).filter(Boolean).filter(message => {
      if (emailFilter === "all") return true;
      if (emailFilter === "excluded") return Boolean(message.excluded);
      if (message.excluded) return false;
      if (emailFilter === "attention") return emailNeedsAttention(message);
      if (emailFilter === "pending_review") return message.pending_review_count > 0;
      if (emailFilter === "failed") return ["failed","interrupted"].includes(message.state);
      return message.state === emailFilter;
    }).sort((a,b) => {
      if (emailSort === "attention") {
        const tier = message => message.excluded ? 2 : emailNeedsAttention(message) ? 0 : 1;
        const priority = tier(a) - tier(b);
        if (priority) return priority;
      }
      return emailSort === "oldest" ? receiptOrder(a) - receiptOrder(b) : receiptOrder(b) - receiptOrder(a);
    });
  }
  function visibleEmailMessages() { return emailMessages; }
  function emailPanel() {
    pageEmailLocally(emailPreviewOffset,emailPageSize,false);
    const connected = emailCredentials?.configured, selected = emailSelected.size;
    const wholeRange = emailWholeRangeSelected();
    const startCount = wholeRange ? emailTotal : selected;
    const entireRangeLoaded = emailLoadedIds.length >= emailTotal;
    const rangeFullySelected = wholeRange || (emailTotal > 0 && selected >= emailTotal) || (entireRangeLoaded && emailLoadedIds.every(id => emailMessageById.get(id)?.excluded || emailSelected.has(id)));
    const activeEmailJob = ["queued", "running"].includes(emailJob?.state);
    const rangeInputs = `<div class="email-range-controls" role="group" aria-label="邮件获取范围"><label class="field email-range-mode"><select aria-label="范围" data-email-range-limit><option value="recent" ${emailRange.limit !== null ? "selected" : ""}>最近 N 封邮件</option><option value="all" ${emailRange.limit === null ? "selected" : ""}>按日期范围</option></select></label>${emailRange.limit !== null ? `<label class="field email-count-field"><input aria-label="邮件数量" type="number" min="1" step="1" data-email-limit value="${esc(emailRange.limit)}"><span aria-hidden="true">封</span></label>` : `<label class="field email-date-field"><input aria-label="起始日期" type="date" data-email-start-date value="${esc(emailRange.start_date || "")}"></label>`}<button class="btn primary email-fetch-button" type="button" data-import-action="email-preview" aria-busy="${emailPreviewBusy && emailPreviewApplyRange}" aria-disabled="${blocked()||(emailPreviewBusy && emailPreviewApplyRange)}" ${blocked()?"disabled":""}><span class="email-fetch-symbol" aria-hidden="true">${host.icon("mail")}</span>获取邮件</button></div>`;
    const sortChoices = [["attention","待处理优先","sortAttention"],["newest","最新收件优先","sortNewest"],["oldest","最早收件优先","sortOldest"]];
    const filterIcon = { all: "inbox", attention: "clock", unprocessed: "mail", pending_review: "check", has_tasks: "tasks", no_tasks: "minus", failed: "warning", excluded: "excluded" }[emailFilter] || "inbox";
    const filterControls = `<div class="email-filter-controls"><label class="email-filter-picker"><span class="email-filter-symbol" aria-hidden="true">${host.icon(filterIcon)}</span><select aria-label="筛选邮件" data-email-filter>${[["all","全部邮件"],["attention","待处理"],["unprocessed","未识别"],["pending_review","待核对"],["has_tasks","已识别有任务"],["no_tasks","已识别无任务"],["failed","失败或中断"],["excluded","已排除"]].map(([value,label])=>`<option value="${value}" ${emailFilter===value?"selected":""}>${label}</option>`).join("")}</select></label><span class="email-sort-control"><button class="email-sort-toggle" type="button" data-import-action="email-cycle-sort" data-email-sort-trigger aria-describedby="email-sort-tooltip" aria-label="排序：${esc(sortChoices.find(([value])=>value===emailSort)[1])}；点击切换为${esc(sortChoices[(sortChoices.findIndex(([value])=>value===emailSort)+1)%sortChoices.length][1])}" ${blocked()?"disabled":""}>${host.icon(sortChoices.find(([value])=>value===emailSort)[2])}</button><span id="email-sort-tooltip" class="email-sort-tooltip" role="tooltip">${esc(sortChoices.find(([value])=>value===emailSort)[1])}</span></span><span class="email-sort-control email-page-size-control"><button class="email-sort-toggle email-page-size-toggle" type="button" data-import-action="email-cycle-page-size" data-email-page-size-trigger aria-label="每页 ${emailPageSize} 封；点击切换为${[25,50,100][([25,50,100].indexOf(emailPageSize)+1)%3]}封" aria-describedby="email-page-size-tooltip" aria-disabled="${blocked()||emailPreviewBusy}" ${blocked()?"disabled":""}>${emailPageSize}<span aria-hidden="true"> / 页</span></button><span id="email-page-size-tooltip" class="email-sort-tooltip" role="tooltip">每页 ${emailPageSize} 封</span></span></div>`;
    const status = message => ({unprocessed:"未处理",excluded:"已排除",queued:"排队中",processing:"识别中",has_tasks:`${message.task_count || 0}项任务${message.pending_review_count ? ` · 待核对 ${message.pending_review_count}` : ""}`,no_tasks:"未识别到任务",failed:"识别失败",interrupted:"处理中断"}[message.state] || "待处理");
    const sortedMessages = visibleEmailMessages();
    const policyLabel = message => ({unknown:"识别规则版本未知",unavailable:"识别规则状态不可用"}[message.policy_status] || "");
    const rows = sortedMessages.map((message, index) => { const successful = ["has_tasks", "no_tasks"].includes(message.state); return `<div class="email-message ${message.excluded ? "is-excluded" : ""}"><label class="email-message-selection"><input type="checkbox" data-email-message="${esc(message.input_id)}" ${!message.excluded && (wholeRange || emailSelected.has(message.input_id)) ? "checked" : ""} ${blocked() || message.excluded || wholeRange ? "disabled" : ""} aria-label="选择 ${esc(message.subject || '无主题邮件')}"><span><strong>${esc(message.subject || "(无主题)")}${successful && message.policy_status === "outdated" ? ` <span class="email-policy-warning" data-policy-status="outdated" role="img" tabindex="0" aria-label="使用旧版规则识别" aria-describedby="email-policy-tooltip-${index}">${host.icon("warningFilled")}<span id="email-policy-tooltip-${index}" class="email-sort-tooltip" role="tooltip">使用旧版规则识别</span></span>` : ""}</strong><small>${esc(message.sender || "发件人未知")}${message.received_at ? ` · ${esc(new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",dateStyle:"medium",timeStyle:"short"}).format(new Date(message.received_at)))}` : ""} · ${esc(status(message))}${successful && policyLabel(message) ? ` <span class="email-policy-status" data-policy-status="${esc(message.policy_status)}">${esc(policyLabel(message))}</span>` : ""}</small>${message.error ? `<small class="error">${esc(message.error)}</small>` : ""}</span></label><button class="btn quiet" type="button" data-email-exclude="${esc(message.input_id)}" data-excluded="${message.excluded ? "false" : "true"}" ${blocked() ? "disabled" : ""}>${message.excluded ? "恢复" : "排除"}</button></div>`; }).join("");
    const jobTotal = Math.max(0, Number(emailJob?.counts?.selected) || 0);
    const jobDone = Math.min(jobTotal, Math.max(0, jobTotal - (Number(emailJob?.counts?.pending) || 0)));
    const jobPercent = jobTotal ? Math.round(jobDone / jobTotal * 100) : 0;
    const waitingLabels = { global_capacity: "等待系统处理名额", account_capacity: "等待账户处理名额", provider_backoff: "等待邮件服务恢复", provider_budget: "等待邮件服务处理额度", model_capacity: "等待识别服务处理名额" };
    const waitingNote = emailJob?.state === "queued" && waitingLabels[emailJob.waiting_reason] ? `<p class="status-note">${waitingLabels[emailJob.waiting_reason]}</p>` : "";
    const jobProgress = `<div class="email-job-progress" role="progressbar" aria-label="邮件识别进度" aria-valuemin="0" aria-valuemax="${jobTotal || 1}" aria-valuenow="${jobDone}" aria-valuetext="已完成 ${jobDone} / ${jobTotal} 封"><span class="email-job-progress-fill" style="width:${jobPercent}%"></span><span class="email-job-progress-animation" aria-hidden="true"></span></div>${waitingNote}`;
    const retryableJob = ["failed","partial_success","stopped","interrupted"].includes(emailJob?.state) && (
      emailJob.counts?.failed > 0 || emailJob.counts?.pending > 0 ||
      (emailJob.items || []).some(item => ["failed","interrupted"].includes(item.state)) ||
      ["failed","stopped","interrupted"].includes(emailJob.state)
    );
    const jobAction = activeEmailJob
      ? { action:"email-stop-job", tone:"stop", icon:"stop", label:emailJobActionBusy ? "正在停止…" : "停止", disabled:emailJobActionBusy }
      : retryableJob
        ? { action:emailJob.kind === "reprocess" ? "email-reprocess-retry" : "email-retry-job", tone:"retry", icon:"retry", label:"重试识别", disabled:blocked()||emailJobActionBusy||emailStartBusy||emailPreviewBusy }
        : emailJob && emailRecognitionUpdates?.available && emailRecognitionUpdates.outdated_count > 0
          ? { action:"email-update-recognition", tone:"update", icon:"refresh", label:"更新识别", disabled:blocked()||emailStartBusy||emailPreviewBusy }
          : null;
    const retryHint = emailJob?.kind === "reprocess"
      ? "仅重试失败或中断邮件，复用已保存正文发送至当前识别服务，不重新读取邮箱。保留已审核任务和处理决定；失败时保留原识别结果。"
      : "重试失败或未完成的邮件；成功缓存会跳过。尚未保存正文的邮件可能需要重新读取邮箱。";
    const jobHint = jobAction?.tone === "retry" ? retryHint : jobAction?.tone === "update"
      ? "复用已保存正文更新整个范围的旧版结果；保留已审核任务和决定，失败保留原结果。"
      : "";
    const jobTooltipId = jobAction?.tone === "retry" ? "email-retry-tooltip" : "email-update-tooltip";
    const jobActionButton = jobAction ? `<span class="email-job-action-slot"><button class="btn quiet email-job-action email-job-${jobAction.tone}" type="button" data-import-action="${jobAction.action}" ${jobHint ? `aria-describedby="${jobTooltipId}"` : ""} ${jobAction.disabled ? "disabled" : ""}>${host.icon(jobAction.icon)}${jobAction.label}</button>${jobHint ? `<span id="${jobTooltipId}" class="email-sort-tooltip" role="tooltip">${jobHint}</span>` : ""}</span>` : "";
    return `<section class="email-source"><div class="email-source-heading"><div class="email-source-title"><h2>北大邮箱</h2>${connected ? `<span class="email-connection-identity"><span class="status-note">${esc(emailCredentials.address)}</span><button class="icon-button email-connection-edit" type="button" data-import-action="email-credentials" aria-label="管理北大邮箱连接" title="管理北大邮箱连接" ${blocked()?"disabled":""}>${host.icon("edit")}</button></span>` : `<span class="status-note">${loading ? "正在读取连接…" : "连接学校邮箱，预览近期通知"}</span>`}</div><div class="email-source-actions">${connected ? rangeInputs : `<button class="btn primary" type="button" data-import-action="email-credentials" ${blocked()?"disabled":""}>连接北大邮箱</button>`}</div></div>${!connected ? `<p class="status-note">首发支持 @stu.pku.edu.cn 和 @pku.edu.cn。请使用邮箱客户端专用密码。</p>` : ""}${emailError ? `<p class="error" role="alert">${esc(emailError)}</p>` : ""}${emailPreviewed ? `<div class="email-list-toolbar">${filterControls}<div class="email-selection-count">${wholeRange ? `整个范围已选择（含筛选隐藏邮件，已排除项除外）` : `已选 ${selected} 封${selected && emailFilter !== "all" ? ` · 当前页 ${visibleEmailMessages().filter(m => emailSelected.has(m.input_id)).length} 封` : ""}`} · ${emailFilter === "all" ? `本范围共 ${emailTotal} 封` : `匹配 ${emailMatchedTotal} 封 / 本范围共 ${emailTotal} 封`}</div></div><div class="email-bulk-actions"><button class="btn quiet" type="button" data-import-action="email-select-page">选择本页</button><button class="btn quiet" type="button" data-import-action="email-clear-selection" ${!selected && !(wholeRange && emailTotal > 0) ? "disabled" : ""}>清除所有</button><button class="btn quiet" type="button" data-import-action="email-select-range" ${rangeFullySelected ? "disabled" : ""}>选择所有</button></div><div class="email-message-list">${rows || `<p class="empty-state">${emailCacheInfo?.miss ? "本地缓存暂无邮件；点击“获取邮件”后同步" : emailFilter !== "all" && emailLoadedIds.length ? "已获取邮件中没有匹配邮件" : "此页没有邮件"}</p>`}</div><div class="email-import-footer">${emailPreviewOffset > 0 || emailNextOffset !== null ? `<div class="email-pagination"><button class="btn quiet" type="button" data-import-action="email-prev" aria-label="上一页" title="上一页" ${emailPreviewOffset<=0?"disabled":""}>${host.icon("arrowLeft")}</button><span>第 ${Math.floor(emailPreviewOffset/emailPageSize)+1} 页</span><button class="btn quiet" type="button" data-import-action="email-next" aria-label="下一页" title="下一页" ${emailNextOffset===null?"disabled":""}>${host.icon("arrowRight")}</button></div>` : ""}<button class="btn primary" type="button" data-import-action="email-start-job" aria-label="${emailStartOperationId ? "重试启动邮件识别" : "识别邮件"}${startCount ? `，已选 ${startCount} 封邮件` : ""}" ${!emailPreferences.consent_granted ? 'title="开始识别前，请在邮箱设置中同意正文处理"' : ""} ${blocked()||emailStartBusy||activeEmailJob||(!selected && !wholeRange)?"disabled":""}>${host.icon("scan")}${emailStartOperationId ? "重试启动邮件识别" : "识别邮件"}${startCount ? `<span class="email-selection-badge" aria-hidden="true">${startCount}</span>` : ""}</button></div>` : ""}${emailJob ? `<div class="email-job" role="status"><div class="email-job-summary"><div class="email-job-copy"><div class="email-job-heading"><strong>${esc(({queued:'排队中',running:'正在处理',succeeded:'处理完成',partial_success:'部分完成',failed:'处理失败',stopped:'已停止',interrupted:'处理中断'})[emailJob.state] || emailJob.state)}</strong>${activeEmailJob ? `<span class="email-job-progress-count">${jobDone} / ${jobTotal}</span>` : ""}</div><div class="email-job-caption">${activeEmailJob ? jobProgress : `<p>已处理 ${emailJob.counts?.processed||0} / ${emailJob.counts?.selected||0} · 缓存 ${emailJob.counts?.cached||0} · 旧版缓存 ${emailRecognitionUpdates?.available ? emailRecognitionUpdates.outdated_count : "—"} · 排除 ${emailJob.counts?.excluded||0} · 失败 ${emailJob.counts?.failed||0} · 未完成 ${emailJob.counts?.pending||0}</p>`}</div></div>${jobActionButton}</div>${emailJob.error ? `<p class="error">${esc(emailJob.error)}</p>` : ""}${emailPollError ? `<button class="btn quiet" data-import-action="email-poll">继续读取状态</button>`:""}</div>` : ""}</section>`;
  }
  function render() {
    if (session.user && !demo() && !loaded && !loading && !busy) queueMicrotask(load);
    const items = allItems(), pending = items.filter(item => item.state === "pending"),
      ignored = items.filter(item => item.state === "ignored");
    const history = independentHistory().map(group => ({ time: group.time, html: independentBatchMarkup(group) }));
    const tabs = [["text", "文本"], ["teaching", "教学网"], ["email", "邮箱"], ["files", "图片与文件"]]
      .map(([key, label]) => `<button type="button" class="btn ${source === key ? "selected" : "quiet"}" data-import-source="${key}" aria-pressed="${source === key}">${label}</button>`).join("");
    const textPanel = `<form data-import-form="text"><label class="field" for="import-text">通知内容<textarea id="import-text" name="text" rows="6" maxlength="4000" placeholder="粘贴通知、邮件正文或任务内容" ${blocked() ? "disabled" : ""}>${esc(text)}</textarea></label><details class="import-time"><summary>通知时间</summary><label class="field" for="import-published">发布时间<input id="import-published" name="published" type="datetime-local" step="1" value="${esc(published)}" ${blocked() ? "disabled" : ""}></label><p class="status-note">按此时间理解“今天”“明天”。</p></details><div class="import-form-actions"><button class="btn primary" type="submit" ${blocked() ? "disabled" : ""}>${busy && source === "text" ? "正在识别…" : "识别任务"}</button></div></form>`;
    const panel = source === "text" ? textPanel : source === "teaching" ? teachingPanel()
      : source === "email" ? emailPanel() : materials.render();
    return `${(host.renderPageHeading || renderPageHeading)({ title: "导入", escapeHtml: esc })}<div class="page-reading">
      <div class="import-source-tabs" role="group" aria-label="导入来源">${tabs}</div>
      <section class="import-input">${panel}
        ${busy && progress ? `<div class="import-progress" role="status"><span class="status-note">${esc(progress)}</span><button class="btn quiet" type="button" data-import-action="stop">停止导入</button></div>` : ""}
        ${source !== "files" && !demo() ? sourceSync.renderHistory({ entries: history, includeSync: source === "teaching" }) : ""}
        ${errorMarkup(error)}${notice && !busy ? `<p class="status-note" role="status">${esc(notice)}</p>` : ""}
      </section>
      <section class="import-results"><div class="section-heading"><h2>待核对${pending.length ? ` <span class="status-note">${pending.length}</span>` : ""}</h2><button class="btn quiet" type="button" data-import-action="refresh" ${blocked() || loading ? "disabled" : ""}>${loading ? "读取中…" : "刷新"}</button></div>${pending.length ? pending.map(candidateRow).join("") : `<p class="empty-state">${loading ? "正在读取导入结果…" : "暂无待核对任务"}</p>`}${ignored.length ? `<details class="import-history"><summary>已忽略 · ${ignored.length}</summary>${ignored.map(candidateRow).join("")}</details>` : ""}</section>
    </div>`;
  }
  function dialogHeader(title) {
    return `<header class="dialog-header"><h2>${esc(title)}</h2><button class="icon-button" type="button" data-import-action="close" aria-label="关闭">${host.icon("close")}</button></header>`;
  }
  function openCredentials() {
    modal = null;
    host.openDialog(
      `<form data-import-form="credentials" data-connection-settings="teaching">${dialogHeader("连接教学网")}<label class="field">教学网账号<input name="username" required maxlength="200" autocomplete="username" value="${esc(credentials?.username || "")}"></label><label class="field">教学网密码<input name="password" type="password" required maxlength="200" autocomplete="current-password"></label><div class="error" data-import-error hidden></div><div class="dialog-actions"><button class="btn primary" type="submit">保存连接</button>${credentials?.configured ? '<button class="btn quiet" type="button" data-import-action="remove-credentials">移除连接</button>' : ""}</div></form>`,
    );
  }
  function renderEmailSettings(details = false) {
    if (!details) return `<section class="account-connection" data-account-section="pku-email" tabindex="-1" aria-label="北大邮箱账号管理"><div><h3>北大邮箱</h3><p class="status-note">管理用于获取邮件通知的账号</p></div><button type="button" class="btn quiet" data-import-action="email-settings">管理连接</button></section>`;
    const content = emailSettingsState === "loading"
      ? '<p class="status-note" role="status">正在读取邮箱设置…</p>'
      : emailSettingsState === "error"
        ? `<p class="error" role="alert">${esc(emailSettingsError)}</p><button type="button" class="btn quiet" data-import-action="email-settings">重试读取设置</button>`
        : `<form data-import-form="email-credentials" data-account-email-settings><label class="field">邮箱地址<input name="address" type="email" required maxlength="254" autocomplete="email" placeholder="name@stu.pku.edu.cn" value="${esc(emailCredentials?.address || "")}"></label><label class="field">邮箱登录账号<input name="username" required maxlength="200" autocomplete="username" value="${esc(emailCredentials?.username || "")}"></label><div class="field"><div class="email-password-label"><label for="email-client-password">客户端专用密码</label><span class="email-password-help"><button type="button" aria-label="客户端专用密码说明" aria-describedby="email-password-tooltip">${host.icon("info")}</button><span id="email-password-tooltip" class="email-sort-tooltip" role="tooltip">${emailCredentials?.configured ? "只修改授权或识别设置时，无需填写密码；更换邮箱地址、登录账号或更新密码时再填写。" : "请填写学校邮箱提供的客户端专用密码。"}密码会加密保存，不会显示在页面或识别请求中。</span></span></div><input id="email-client-password" name="password" type="password" ${emailCredentials?.configured ? 'placeholder="留空以保留已保存的密码"' : "required"} maxlength="200" autocomplete="new-password"></div><label class="field check-field"><input type="checkbox" name="consent" value="true" ${emailPreferences.consent_granted ? "checked" : ""}>同意邮件正文发送至识别服务处理</label><label class="field check-field"><input type="checkbox" name="process_all" value="true" ${emailPreferences.process_all ? "checked" : ""}>默认处理整个范围</label><div class="error" data-import-error hidden></div><div class="dialog-actions"><button class="btn primary" type="submit">${emailCredentials?.configured ? "保存设置" : "保存连接"}</button>${emailCredentials?.configured ? '<button class="btn quiet" type="button" data-import-action="remove-email-credentials">移除连接</button>' : ""}</div></form>`;
    return `<div data-email-settings-dialog data-connection-settings="pku-email">${dialogHeader("北大邮箱设置")}${content}</div>`;
  }
  async function loadEmailSettings() {
    if (!session.user || session.changing || demo()) return false;
    const stamp = current(), attempt = ++emailSettingsRevision;
    emailSettingsState = "loading";
    emailSettingsError = "";
    const results = await Promise.allSettled([
      api("/api/sources/email/credentials"), api("/api/sources/email/preferences"),
    ]);
    if (!valid(stamp) || attempt !== emailSettingsRevision) return false;
    const failure = results.find(result => result.status === "rejected");
    if (failure) {
      emailSettingsState = "error";
      emailSettingsError = describeImportError(failure.reason).message;
      return false;
    }
    emailCredentials = results[0].value;
    emailPreferences = results[1].value;
    emailSettingsState = "ready";
    refresh();
    return true;
  }
  async function openEmailCredentials() {
    await openEmailSettings();
  }
  async function openEmailSettings() {
    modal = null;
    const stamp = current(), attempt = ++emailSettingsDialogOperation;
    const pending = loadEmailSettings();
    host.openDialog(renderEmailSettings(true));
    host.focusEmailSettings?.();
    await pending;
    if (valid(stamp) && attempt === emailSettingsDialogOperation && host.isDialogOpen?.("[data-email-settings-dialog]")) {
      host.openDialog(renderEmailSettings(true));
      host.focusEmailSettings?.();
    }
  }
  function openReview(item) {
    if (!item || item.state !== "pending") return;
    const due = candidateDue(item.candidate.deadline);
    modal = {
      kind: "review",
      id: item.id,
      version: item.version,
      tasksVersion: getTaskVersion(),
      values: {
        action:
          item.candidate.notice_type === "uncertain"
            ? ""
            : item.candidate.notice_type,
        title: item.candidate.title,
        description: item.candidate.description || "",
        course: item.candidate.course_or_project || "",
        date: due.date,
        time: due.time,
        target_id: "",
      },
    };
    showReview();
  }
  function showReview() {
    const item = itemById(modal?.id);
    if (!item) return;
    const v = modal.values,
      action = v.action,
      tasks = host.getState().tasks.filter((task) => !task.recycleBin),
      target = tasks.find((task) => task.id === v.target_id);
    modal.targetFingerprint = taskFingerprint(target);
    const isNotice = item.candidate.content_type === "notice";
    const exact =
      item.candidate.deadline?.precision === "datetime" &&
      item.candidate.deadline.value &&
      Number.isFinite(Date.parse(item.candidate.deadline.value)) &&
      new Date(item.candidate.deadline.value).getUTCSeconds();
    host.openDialog(
      `<form data-import-form="review" data-candidate="${esc(item.id)}">${dialogHeader(isNotice ? "核对通知" : "核对任务")}<p class="status-note">${esc(item.candidate.title)}</p>${
        item.candidate.notice_type === "uncertain"
          ? `<label class="field">处理方式<select name="action" required data-import-field="action"><option value="">请选择</option>${Object.entries(
              actions,
            )
              .filter(([key]) => key !== "uncertain")
              .map(
                ([key, label]) =>
                  `<option value="${key}" ${action === key ? "selected" : ""}>${label}</option>`,
              )
              .join("")}</select></label>`
          : `<input type="hidden" name="action" value="${esc(action)}">`
      }${action && action !== "create" ? `<label class="field">目标任务<select name="target_id" required data-import-field="target"><option value="">选择已有任务</option>${tasks.map((task) => `<option value="${esc(task.id)}" ${task.id === v.target_id ? "selected" : ""}>${esc(task.title)}</option>`).join("")}</select></label>${target ? `<p class="status-note">当前任务：${esc(target.title)}${target.due ? " · " + esc(target.due) + (target.dueTime ? " " + esc(target.dueTime) : "") : ""}</p>` : ""}` : ""}${["create", "update"].includes(action) ? `<label class="field">${isNotice ? "通知名称" : "任务名称"}<input name="title" required maxlength="180" value="${esc(v.title)}"></label><label class="field">课程<input name="course" maxlength="200" value="${esc(v.course)}" placeholder="可不填写"></label><label class="field">说明<textarea name="description" rows="3">${esc(v.description)}</textarea></label>` : ""}${action && action !== "cancel" ? `<div class="form-grid"><label class="field">截止日期<input name="date" type="date" value="${esc(v.date)}" ${action === "postpone" ? "required" : ""}></label><label class="field">截止时刻<input name="time" type="time" value="${esc(v.time)}"></label></div><p class="status-note">${exact ? "原文时间包含秒，请确认要保存的时刻。" : "原文只有日期时，时刻可以留空。"}</p>` : ""}${action === "cancel" ? '<p class="status-note">确认后将取消所选任务，原记录保留。</p>' : ""}${evidence(item)}<div class="error" data-import-error hidden></div><div class="dialog-actions"><button class="btn primary" type="submit" ${!action ? "disabled" : ""}>${action === "create" ? (isNotice ? "确认加入通知" : "确认加入任务") : action === "cancel" ? "确认取消任务" : "确认保存"}</button><button class="btn quiet" type="button" data-import-action="refresh-review" hidden>刷新后重新核对</button></div></form>`,
    );
  }
  function formError(form, message, conflict = false) {
    const report = message ? describeUserError(message) : null;
    const node = form.querySelector("[data-import-error]");
    if (node) {
      node.textContent = report?.message || "";
      node.innerHTML = userErrorMarkup(report, form.dataset.importForm === "credentials" ? "teaching_sync" : form.dataset.importForm === "email-credentials" ? "email_sync" : "import_review");
      node.hidden = !report;
    }
    const retry = form.querySelector('[data-import-action="refresh-review"]');
    if (retry) retry.hidden = !conflict;
  }
  function lockForm(form, lock) {
    form.querySelectorAll("input,textarea,select,button").forEach((field) => {
      if (field.dataset.errorFeedback) return;
      if (lock) {
        field.dataset.wasDisabled = String(field.disabled);
        field.disabled = true;
      } else {
        field.disabled = field.dataset.wasDisabled === "true";
        delete field.dataset.wasDisabled;
      }
    });
  }
  async function recognize(body, retry = false) {
    const next = await api(
      retry ? "/api/recognition/retry" : "/api/recognition/batches",
      buildRecognitionRequest(body),
      "POST",
      { signal: controller.signal, timeoutMs: 280000 },
    );
    if (!Array.isArray(next)) throw new Error("导入结果暂时无法读取");
    return next;
  }
  async function autoApply(requestId, stamp) {
    const signal = controller?.signal;
    const batch = batches.find((batch) => batch.request.request_id === requestId);
    const candidates = !batch || batch.demo ? [] : (batch.items || []).filter((item) => {
      const candidate = item.candidate;
      const record = batch.request.records?.find((record) => record.input_id === candidate?.input_id);
      return item.state === "pending" && item.version === 1 &&
        record?.platform === "teaching_web" && candidate.notice_type === "create" &&
        candidate.needs_review === false && !candidate.review_reasons?.length;
    });
    let applied = 0, failed = 0;
    if (!candidates.length || !valid(stamp) || signal?.aborted) return { applied, failed };
    await flushStore();
    for (const [index, item] of candidates.entries()) {
      if (!valid(stamp) || signal?.aborted) break;
      progress = `正在加入任务 ${index + 1} / ${candidates.length}`;
      refresh();
      try {
        // The server owns eligibility, source validation and the task contents.
        const result = await api(
          "/api/recognition/candidates/" + encodeURIComponent(item.id) + "/resolve",
          { action: "create", auto_apply: true, version: item.version },
          "POST", { signal },
        );
        if (!valid(stamp)) return { applied, failed };
        if (!Array.isArray(result.batches)) throw new Error("导入结果暂时无法读取");
        acceptBatches(result.batches);
        applied++;
      } catch (e) {
        if (!valid(stamp)) return { applied, failed };
        if (signal?.aborted) break;
        // A refusal keeps the candidate available for manual review.
        if (e.status === 400) continue;
        failed++;
        error = "部分任务自动加入未完成，可再次采集重试，或核对后加入。";
      }
    }
    if (applied && valid(stamp)) {
      try { await host.refresh(); }
      catch { if (valid(stamp)) error = "任务已加入，但列表刷新失败，请重新读取任务。"; }
    }
    return { applied, failed };
  }
  async function importText(values) {
    if (blocked() || !allowed()) return;
    text = String(values.text || "");
    published = String(values.published ?? published);
    const stamp = current();
    try {
      const signature = JSON.stringify([text.trim(), published]);
      if (request?.signature !== signature)
        request = { signature, body: buildTextImport(text, published) };
      busy = true;
      error = "";
      notice = "";
      progress = "正在识别通知…";
      controller = new AbortController();
      refresh();
      const next = await recognize(request.body);
      if (!valid(stamp)) return;
      acceptBatches(next);
      loaded = true;
      const result = next.find(
        (batch) => batch.request.request_id === request.body.request_id,
      )?.result;
      if (["failed", "partial_success"].includes(result?.status))
        error = "部分通知识别失败，可展开历史导入重试";
      else
        notice =
          result?.status === "no_tasks"
            ? "未找到需要处理的任务"
            : "识别完成，请核对下方任务";
    } catch (e) {
      if (valid(stamp)) {
        if (controller?.signal.aborted)
          notice = "已停止导入，后台已开始的识别可能继续，可刷新查看结果。";
        else error = describeImportError(e);
      }
    } finally {
      if (valid(stamp)) {
        busy = false;
        controller = null;
        progress = "";
        refresh();
      }
    }
  }
  async function sync() {
    if (stopping || !allowed()) return;
    if (sourceSync.canStop()) {
      const stamp = current();
      stopping = true;
      refresh();
      try {
        await schedule.stop();
        if (valid(stamp)) await sourceSync.stop();
      } finally {
        if (valid(stamp)) { stopping = false; refresh(); }
      }
      return;
    }
    if (blocked() || syncIsActive()) return;
    if (!credentials?.configured) { openCredentials(); return; }
    await schedule.start();
  }
  function pageEmailLocally(offset, pageSize = emailPageSize, render = true) {
    emailPageSize = pageSize;
    const ordered = orderedEmailMessages();
    emailMatchedTotal = ordered.length;
    const lastOffset = Math.max(0, Math.ceil(ordered.length/pageSize)-1)*pageSize;
    emailPreviewOffset = Math.min(Math.max(0,offset),lastOffset);
    emailMessages = ordered.slice(emailPreviewOffset,emailPreviewOffset+pageSize);
    emailNextOffset = emailPreviewOffset+pageSize<ordered.length ? emailPreviewOffset+pageSize : null;
    if (render) { if (emailLoadedRange) saveEmailPointer(emailLoadedRange,emailPreviewOffset); refresh(); }
  }
  async function previewEmail(offset = 0, { autoSelectInitial = true, applyRange = false, cacheOnly = false, pageSize = emailPageSize } = {}) {
    if (blocked() || !allowed()) return;
    if (!emailCredentials?.configured) { await openEmailCredentials(); return; }
    const requestedRange = { ...(applyRange || !emailLoadedRange ? emailRange : emailLoadedRange) };
    const rangeChanged = emailLoadedRange && JSON.stringify(requestedRange) !== JSON.stringify(emailLoadedRange);
    const stamp = current(), requestId = ++emailPreviewRequestId, firstPreview = applyRange && !emailExplicitRangeRead; emailPreviewBusy = true; emailPreviewApplyRange = applyRange; emailError = "";
    // Keep the mounted toolbar and focused button while the request is pending.
    if (host.setEmailPreviewBusy?.(true, { fetching:applyRange }) !== true) refresh();
    try {
      const sameSnapshotRange = !rangeChanged && emailLoadedRange && JSON.stringify(requestedRange) === JSON.stringify(emailLoadedRange);
      const result = await api("/api/sources/email/preview", { ...requestedRange, filter: "all", sort: "attention", offset:0, page_size: pageSize, load_all:true, refresh: !cacheOnly && applyRange, cache_only: cacheOnly || !applyRange, ...(sameSnapshotRange && emailSnapshotId && !applyRange ? { snapshot_id: emailSnapshotId } : {}) }, "POST");
      if (!valid(stamp) || requestId !== emailPreviewRequestId) return;
      if (rangeChanged) { emailSelected.clear(); emailWholeRange = false; emailManualSelection = false; }
      emailLoadedRange = requestedRange;
      const allMessages = Array.isArray(result.messages) ? result.messages : [];
      emailMessageById = new Map(allMessages.map(message=>[message.input_id,message]));
      emailLoadedIds = [...emailMessageById.keys()];
      emailSnapshotId = result.cache?.snapshot_id || null;
      emailCacheInfo = result.cache || null;
      emailRecognitionUpdates = result.recognition_updates || null;
      emailTotal = Number(result.summary?.total || 0); emailMatchedTotal = Number(result.summary?.matched_total ?? emailTotal);
      pageEmailLocally(offset,pageSize,false);
      if (!cacheOnly) saveEmailPointer(requestedRange,emailPreviewOffset);
      emailSelected = new Set([...emailSelected].filter(id => emailMessageById.has(id) && !emailMessageById.get(id).excluded));
      if (firstPreview && autoSelectInitial) emailSelected = new Set(emailMessages.filter(item => !item.excluded).map(item => item.input_id));
      emailPreviewed = true;
      if (applyRange) emailExplicitRangeRead = true;
      if (result.cache?.miss) notice = `已加载 ${emailLoadedIds.length} / ${emailTotal} 封邮件的缓存标题；点击“获取邮件”补全列表`;
      else if (!emailMatchedTotal) notice = emailTotal ? "筛选条件下没有邮件" : "此范围内没有邮件";
      else notice = "";
    } catch (e) { if (valid(stamp) && requestId === emailPreviewRequestId) emailError = e?.status === 409 ? "邮件列表已更新，请点击“获取邮件”重新同步后重试" : describeImportError(e).message; }
    finally { if (valid(stamp) && requestId === emailPreviewRequestId) { emailPreviewBusy = false; emailPreviewApplyRange = false; refresh(); } }
  }
  async function refreshEmailJob() {
    const stamp = current(), requestId = ++emailJobRequestId;
    try {
      const jobs = await api("/api/sources/email/jobs?summary=1");
      if (!valid(stamp) || requestId !== emailJobRequestId) return;
      const visibleJobs = jobs.jobs || [];
      const active = visibleJobs.find(job => ["queued","running"].includes(job.state));
      const latest = active || visibleJobs[0] || null;
      if (emailJob?.job_id !== latest?.job_id) emailJobDetailsRevision = null;
      clearTimeout(emailJobTimer); emailJobTimer = null;
      emailJob = latest;
      if (active) {
        emailPollError = false;
        emailJobRevision = active.revision ?? null;
        emailJobPollDelay = 1000;
        scheduleEmailJobPoll(active.job_id, emailJobPollDelay);
      }
      refresh();
    } catch (e) { if (valid(stamp) && requestId === emailJobRequestId) { emailError = describeImportError(e).message; refresh(); } }
  }
  function scheduleEmailJobPoll(id, delay) {
    clearTimeout(emailJobTimer); emailJobTimer = null;
    if (!emailJobVisibilityHandler && globalThis.document?.addEventListener) {
      emailJobVisibilityDocument = globalThis.document;
      emailJobVisibilityHandler = () => {
        if (globalThis.document.hidden) { clearTimeout(emailJobTimer); emailJobTimer = null; }
        else if (emailJob?.job_id && ["queued", "running"].includes(emailJob.state) && !emailJobTimer) void pollEmailJob(emailJob.job_id);
      };
      emailJobVisibilityDocument.addEventListener("visibilitychange", emailJobVisibilityHandler);
    }
    if (globalThis.document?.hidden) return;
    emailJobTimer = setTimeout(() => { emailJobTimer = null; void pollEmailJob(id); }, delay);
  }
  function mergeEmailJobItems(job) {
    const itemStates = new Map((job.items || []).map(item => [item.input_id, item]));
    for (const [id,message] of emailMessageById) { const item = itemStates.get(id); if (item) emailMessageById.set(id,{...message,state:item.state,task_count:item.task_count,request_id:item.request_id,error:item.error}); }
    pageEmailLocally(emailPreviewOffset,emailPageSize,false);
  }
  async function pollEmailJob(id) {
    if (!emailJob?.job_id || emailJob.job_id !== id) return;
    clearTimeout(emailJobTimer);
    emailJobTimer = null;
    if (globalThis.document?.hidden || emailJobPolling) return;
    emailJobPolling = true;
    const stamp = current(), requestId = ++emailJobRequestId;
    const currentRequest = () => valid(stamp) && requestId === emailJobRequestId && emailJob?.job_id === id;
    try {
      const summary = await api(`/api/sources/email/jobs/${encodeURIComponent(id)}?summary=1`);
      if (!currentRequest()) return;
      emailPollError = false;
      const changed = emailJobRevision === null || summary.revision !== emailJobRevision || emailJobDetailsRevision !== summary.revision;
      if (changed || !["queued", "running"].includes(summary.state)) {
        const job = await api(`/api/sources/email/jobs/${encodeURIComponent(id)}`);
        if (!currentRequest()) return;
        emailJob = job; emailJobRevision = job.revision ?? summary.revision ?? null;
        emailJobDetailsRevision = emailJobRevision;
        mergeEmailJobItems(job);
        if (!["queued","running"].includes(job.state)) { await refreshResults(currentRequest); if (currentRequest()) await previewEmail(emailPreviewOffset); }
        emailJobPollDelay = 1000;
        if (currentRequest()) refresh();
      } else {
        const before = emailJob;
        emailJob = { ...emailJob, ...summary };
        emailJobRevision = summary.revision ?? emailJobRevision;
        const visibleSummaryChanged = before.state !== emailJob.state ||
          before.waiting_reason !== emailJob.waiting_reason ||
          JSON.stringify(before.counts || {}) !== JSON.stringify(emailJob.counts || {});
        emailJobPollDelay = summary.state === "queued" ? Math.min(15000, Math.max(3000, emailJobPollDelay * 1.7)) : Math.min(8000, Math.max(1200, emailJobPollDelay * 1.35));
        if (visibleSummaryChanged && currentRequest()) refresh();
      }
      if (currentRequest() && ["queued","running"].includes(emailJob.state)) scheduleEmailJobPoll(id, emailJobPollDelay);
    } catch (e) { if (currentRequest()) { emailPollError = true; emailError = describeImportError(e).message; refresh(); } }
    finally { emailJobPolling = false; }
  }
  async function syncEmail() {
    if (blocked() || emailStartBusy || ["queued","running"].includes(emailJob?.state) || !allowed()) return;
    if (!emailWholeRangeSelected() && !emailSelected.size) return;
    if (!emailPreferences.consent_granted) { await openEmailSettings(); return; }
    const stamp = current(), requestId = ++emailJobRequestId;
    clearTimeout(emailJobTimer); emailJobTimer = null;
    if (!emailStartOperationId) emailStartOperationId = newId();
    const operation_id = emailStartOperationId;
    emailStartBusy = true; emailError = ""; refresh();
    try {
      const requestBody = emailSelected.size && !emailWholeRangeSelected()
        ? { operation_id, input_ids: [...emailSelected] }
        : { operation_id, range: emailLoadedRange || emailRange };
      const job = await api("/api/sources/email/jobs", requestBody, "POST");
      if (!valid(stamp) || requestId !== emailJobRequestId) return;
      emailJob = job; emailPollError = false; emailStartOperationId = null; emailJobRevision = job.revision ?? null; emailJobDetailsRevision = job.revision ?? null;
      clearTimeout(emailJobTimer);
      if (["queued","running"].includes(job.state)) { emailJobRevision = job.revision ?? null; emailJobPollDelay = 1000; scheduleEmailJobPoll(job.job_id, emailJobPollDelay); }
      refresh();
    } catch (e) {
      if (valid(stamp) && requestId === emailJobRequestId) { emailError = describeImportError(e).message; refresh(); }
    } finally {
      if (valid(stamp)) { emailStartBusy = false; refresh(); }
    }
  }
  function openEmailReprocessConfirm(inputIds, showDialog = true) {
    emailReprocessScope = null; emailUpdateUnknown = false;
    const ids = [...new Set(inputIds)].filter(Boolean);
    if (!ids.length || ids.length > 1000) { emailError = ids.length > 1000 ? "每次最多重新识别 1000 封邮件" : "没有可重新识别的邮件"; refresh(); return false; }
    if (JSON.stringify(ids) !== JSON.stringify(emailReprocessConfirmIds)) emailReprocessOperationId = null;
    emailReprocessConfirmIds = ids;
    emailReprocessError = "";
    if (showDialog) renderEmailReprocessConfirm();
    return true;
  }
  async function updateEmailRecognition() {
    if (blocked() || emailStartBusy || emailPreviewBusy || ["queued","running"].includes(emailJob?.state) || !allowed()) return;
    if (!emailLoadedRange || !emailSnapshotId || !emailRecognitionUpdates?.available) return;
    if (!emailPreferences.consent_granted) { void openEmailSettings(); return; }
    const stamp = current();
    await previewEmail(emailPreviewOffset, { autoSelectInitial: false, cacheOnly: true });
    if (!valid(stamp) || emailError || !emailRecognitionUpdates?.available) return;
    if (!emailRecognitionUpdates.outdated_count) { host.toast("当前范围无需更新识别结果"); return; }
    const scope = { range: { ...emailLoadedRange }, snapshot_id: emailSnapshotId, ...emailRecognitionUpdates };
    const identity = value => value && JSON.stringify([value.range,value.snapshot_id,value.policy_version,value.selection_token]);
    if (identity(scope) !== identity(emailReprocessScope)) emailReprocessOperationId = null;
    emailReprocessScope = scope;
    emailReprocessConfirmIds = []; emailReprocessError = ""; emailUpdateUnknown = false;
    await confirmEmailReprocess({ showDialog: false });
  }
  function renderEmailReprocessConfirm() {
    const scope = emailReprocessScope;
    const count = scope ? scope.outdated_count + (emailUpdateUnknown ? scope.unknown_count : 0) : emailReprocessConfirmIds.length;
    host.openDialog(`<section class="email-reprocess-confirm">${dialogHeader(scope ? "更新识别结果" : "重新识别邮件")}<p>${scope ? "将使用当前规则更新整个已读取范围内的旧版识别结果，包括其他页；已排除邮件和当前版本结果会跳过。" : "将复用系统保存的邮件正文，再次发送至当前配置的识别服务。"}已审核的任务和处理决定会保留。</p>${scope?.unknown_count ? `<label class="email-update-unknown"><input type="checkbox" data-email-update-unknown ${emailUpdateUnknown?"checked":""} ${emailStartBusy?"disabled":""}>同时更新版本未知的 ${scope.unknown_count} 封邮件</label>` : ""}<p class="status-note">本次将重新识别 ${count} 封邮件，复用已保存正文；不会重新读取邮箱。失败时保留原识别结果。</p>${emailReprocessError ? `<p class="error" role="alert">${esc(emailReprocessError)}</p>` : ""}<div class="dialog-actions"><button class="btn quiet" type="button" data-import-action="email-reprocess-cancel" ${emailStartBusy?"disabled":""}>取消</button><button class="btn primary" type="button" data-import-action="email-reprocess-confirm" ${emailStartBusy||!count?"disabled":""}>${emailStartBusy?"正在提交…":scope?"确认更新":"确认重新识别"}</button></div></section>`);
  }
  async function startEmailReprocess(inputIds = [...emailSelected].filter(id => ["has_tasks", "no_tasks"].includes(emailMessageById.get(id)?.state)), { confirm = true } = {}) {
    if (blocked() || emailStartBusy || ["queued", "running"].includes(emailJob?.state) || !allowed()) return;
    if (!emailPreferences.consent_granted) { await openEmailSettings(); return; }
    if (openEmailReprocessConfirm(inputIds, confirm) && !confirm) await confirmEmailReprocess({ showDialog: false });
  }
  async function confirmEmailReprocess({ showDialog = true } = {}) {
    if (blocked() || emailStartBusy || ["queued", "running"].includes(emailJob?.state) || !allowed()) return;
    const ids = [...emailReprocessConfirmIds], scope = emailReprocessScope;
    if (!emailPreferences.consent_granted) { emailError = "请先在邮箱设置中同意正文处理"; refresh(); return; }
    if (scope ? !(scope.outdated_count + (emailUpdateUnknown ? scope.unknown_count : 0)) : (!ids.length || ids.length > 1000)) { emailError = "重新识别范围无效"; refresh(); return; }
    if (!emailReprocessOperationId) emailReprocessOperationId = newId();
    const stamp = current(), requestId = ++emailJobRequestId; emailStartBusy = true; emailError = ""; emailReprocessError = ""; if (showDialog) renderEmailReprocessConfirm(); refresh();
    try {
      const selection = scope ? { range: scope.range, snapshot_id: scope.snapshot_id, policy_version: scope.policy_version, selection_token: scope.selection_token, include_unknown: emailUpdateUnknown } : { input_ids: ids };
      const job = await api("/api/sources/email/reprocess", { operation_id: emailReprocessOperationId, ...selection, consent: true }, "POST");
      if (!valid(stamp) || requestId !== emailJobRequestId) return;
      emailJob = job; emailPollError = false; emailJobRevision = job.revision ?? null; emailJobDetailsRevision = job.revision ?? null; emailReprocessOperationId = null; emailReprocessConfirmIds = []; emailReprocessScope = null;
      emailReprocessError = "";
      if (showDialog && host.isDialogOpen?.(".email-reprocess-confirm")) host.closeDialog();
      clearTimeout(emailJobTimer);
      if (["queued", "running"].includes(job.state) && job.job_id) { emailJobRevision = job.revision ?? null; emailJobPollDelay = 1000; scheduleEmailJobPoll(job.job_id, emailJobPollDelay); }
      refresh();
    } catch (e) { if (valid(stamp) && requestId === emailJobRequestId) { emailReprocessError = describeImportError(e).message; if (!showDialog) emailError = emailReprocessError; refresh(); } }
    finally { if (valid(stamp)) { emailStartBusy = false; if (showDialog && (emailReprocessConfirmIds.length || emailReprocessScope) && emailReprocessError && host.isDialogOpen?.(".email-reprocess-confirm")) renderEmailReprocessConfirm(); refresh(); } }
  }
  async function emailJobAction(action) {
    if (!emailJob?.job_id || blocked() || emailJobActionBusy) return;
    const stamp = current(), jobId = emailJob.job_id;
    const requestId = action === "poll" ? null : ++emailJobRequestId;
    if (action !== "poll") { clearTimeout(emailJobTimer); emailJobTimer = null; }
    emailJobActionBusy = true; emailError = ""; refresh();
    try {
      let next;
      if (action === "stop") next = await api(`/api/sources/email/jobs/${encodeURIComponent(jobId)}/stop`, {}, "POST");
      else if (action === "retry") {
        if (!emailRetryOperationId) emailRetryOperationId = newId();
        next = await api(`/api/sources/email/jobs/${encodeURIComponent(jobId)}/retry`, { operation_id: emailRetryOperationId }, "POST");
      } else if (action === "poll") { await pollEmailJob(jobId); return; }
      if (!valid(stamp) || (requestId !== null && requestId !== emailJobRequestId)) return;
      emailJob = next;
      if (action === "retry") emailRetryOperationId = null;
      emailPollError = false;
      await refreshEmailJob();
    } catch (e) { if (valid(stamp) && (requestId === null || requestId === emailJobRequestId)) { emailError = describeImportError(e).message; refresh(); } }
    finally { if (valid(stamp)) { emailJobActionBusy = false; refresh(); } }
  }
  async function resolveSmall(item, action) {
    if (
      blocked() ||
      !allowed() ||
      !item ||
      item.state !== (action === "reopen" ? "ignored" : "pending")
    )
      return;
    const stamp = current();
    busy = true;
    error = "";
    refresh();
    try {
      const result = await api(
        "/api/recognition/candidates/" +
          encodeURIComponent(item.id) +
          "/resolve",
        { action, version: item.version },
      );
      if (valid(stamp)) {
        acceptBatches(result.batches);
        host.toast(action === "ignore" ? "已忽略" : "已恢复待核对");
      }
    } catch (e) {
      if (valid(stamp)) error = describeImportError(e);
    } finally {
      if (valid(stamp)) {
        busy = false;
        refresh();
        if (source === "email" && emailPreviewed) void previewEmail(emailPreviewOffset, { autoSelectInitial: false });
      }
    }
  }
  async function retryBatch(batch) {
    if (blocked() || !allowed() || !batch || syncOwnsBatch(batch) || !["failed", "partial_success"].includes(batch.result?.status) || incomplete(batch)) return;
    const stamp = current();
    busy = true;
    error = "";
    notice = "";
    progress = "正在重试识别…";
    controller = new AbortController();
    refresh();
    try {
      const next = await recognize(batch.request, true);
      if (!valid(stamp)) return;
      acceptBatches(next);
      const automatic = await autoApply(batch.request.request_id, stamp);
      if (!valid(stamp)) return;
      await host.refresh();
      if (!valid(stamp)) return;
      notice = controller.signal.aborted ? "已停止导入，后台已开始的识别可能继续，可刷新查看结果。" :
        "识别结果已更新" + (automatic.applied ? `，已自动加入 ${automatic.applied} 项任务` : "");
    } catch (e) {
      if (valid(stamp))
        error = controller?.signal.aborted
          ? "已停止导入，后台已开始的识别可能继续，可刷新查看结果。"
          : describeImportError(e);
    } finally {
      if (valid(stamp)) {
        busy = false;
        controller = null;
        progress = "";
        refresh();
      }
    }
  }
  async function click(button) {
    const d = button.dataset;
    if (d.importAction === "email-cycle-sort") {
      if (blocked()) return true;
      const modes = ["attention", "newest", "oldest"];
      emailSort = modes[(modes.indexOf(emailSort) + 1) % modes.length];
      pageEmailLocally(0);
      host.focusEmailSort?.();
      return true;
    }
    if (d.emailExclude) {
      const id = d.emailExclude, excluded = d.excluded === "true", stamp = current();
      try { await api("/api/sources/email/exclusions", {input_ids:[id], excluded}, "POST"); if (valid(stamp)) { emailMessages = emailMessages.map(m => m.input_id === id ? {...m, excluded, state:excluded?"excluded":"unprocessed"} : m); if (emailMessageById.has(id)) emailMessageById.set(id, {...emailMessageById.get(id), excluded, state:excluded?"excluded":"unprocessed"}); emailSelected.delete(id); refresh(); } }
      catch (e) { if (valid(stamp)) { emailError = describeImportError(e).message; refresh(); } }
      return true;
    }
    if (d.sourceSyncAction) {
      if (allowed()) await sourceSync.click(button);
      return true;
    }
    if (d.syncScheduleAction) return schedule.click(button);
    if (d.materialAction) { if (allowed()) await materials.click(button); return true; }
    if (d.importSource) {
        if (!setSource(d.importSource)) return true;
        sourceSync.dismissRecoveryHighlight();
        host.onImportSourceChange?.(source);
        emailError = "";
        if (source === "teaching" && allowed()) { void refreshSourceSync(); void schedule.load(); }
        if (source === "email" && allowed()) { if (emailCredentials === null) void load(); else if (emailCredentials.configured) { void refreshEmailJob(); if (!emailPreviewed) { restoreEmailPointer(); void previewEmail(emailPreviewOffset, { autoSelectInitial: false, cacheOnly: true }); } } }
        if (source === "files" && allowed()) void materials.load();
        refresh();
      return true;
    }
    if (d.importHistoryCategory || d.importHistoryItem !== undefined || d.importHistoryRetry) {
      if (!allowed()) return true;
      const group = independentHistory().find(item => item.id === (d.importHistoryBatch || d.importHistoryRetry));
      if (!group) return true;
      if (d.importHistoryRetry) {
        const stamp = current();
        for (const batch of group.batches.filter(batch => ["failed", "partial_success"].includes(batch.result?.status) && !incomplete(batch))) {
          if (!valid(stamp)) break;
          await retryBatch(batch);
        }
      } else if (d.importHistoryCategory) {
        if (!["added", "review", "updated", "error"].includes(d.importHistoryCategory)) return true;
        sourceSync.dismissRecoveryHighlight();
        historyCategories.set(group.id, historyCategories.get(group.id) === d.importHistoryCategory ? null : d.importHistoryCategory);
        historyProblems.delete(group.id); refresh();
      } else {
        const category = historyCategories.get(group.id), index = Number(d.importHistoryItem);
        if (!Number.isInteger(index) || index < 0) return true;
        const item = (category === "error" ? group.problems : group[category])?.[index];
        if (!item) return true;
        if (category === "error") {
          historyProblems.set(group.id, historyProblems.get(group.id) === index ? null : index); refresh();
        } else {
          const candidate = itemById(item.candidate_id);
          const status = importResultStatus(item, category, { ...host, getCandidate: itemById });
          if (status.taskId) host.openTask?.(status.taskId);
          else if (!blocked() && candidate?.state === "pending") openReview(candidate);
          else if (status.message) host.toast(status.message);
        }
      }
      return true;
    }
    if (d.importReview) {
      if (!blocked() && allowed()) openReview(itemById(d.importReview));
      return true;
    }
    if (d.importIgnore) {
      await resolveSmall(itemById(d.importIgnore), "ignore");
      return true;
    }
    if (d.importReopen) {
      await resolveSmall(itemById(d.importReopen), "reopen");
      return true;
    }
    if (d.importRetry) {
      await retryBatch(
        batches.find((batch) => batch.request.request_id === d.importRetry),
      );
      return true;
    }
    const action = d.importAction;
    if (!action) return false;
    if (action === "close") {
      if (!busy) {
        modal = null;
        host.closeDialog();
      }
      return true;
    }
    if (action === "stop") {
      controller?.abort();
      return true;
    }
    if (action === "refresh") {
      if (allowed()) { await host.refresh(); await load(); await refreshSourceSync(); }
      return true;
    }
    if (action === "credentials") {
      if (credentialsBlocked() || !allowed()) return true;
      if (credentials) {
        openCredentials();
        return true;
      }
      // The account dialog can reach this form before the import page is loaded.
      const stamp = current(), label = button.textContent;
      busy = true;
      button.disabled = true;
      button.textContent = "正在读取…";
      try {
        const result = await api("/api/sources/credentials");
        if (valid(stamp)) {
          credentials = result;
          if (formIsOpen(button)) openCredentials();
        }
      } catch (e) {
        if (valid(stamp) && formIsOpen(button)) throw e;
      } finally {
        if (valid(stamp)) {
          busy = false;
          button.disabled = false;
          button.textContent = label;
          refresh();
        }
      }
      return true;
    }
    if (action === "email-settings") {
      if (!blocked() && allowed()) await openEmailSettings();
      return true;
    }
    if (action === "email-credentials") {
      if (!blocked() && allowed()) await openEmailCredentials();
      return true;
    }
    if (action === "email-preview") {
      if (emailPreviewBusy && emailPreviewApplyRange) return true;
      await previewEmail(0, { applyRange: true });
      return true;
    }
    if (action === "email-sync") {
      await syncEmail();
      return true;
    }
    if (action === "email-cycle-page-size") {
      if (blocked() || emailPreviewBusy) return true;
      const nextSize = [25,50,100][([25,50,100].indexOf(emailPageSize)+1)%3];
      pageEmailLocally(Math.floor(emailPreviewOffset/nextSize)*nextSize,nextSize);
      host.focusEmailPageSize?.();
      return true;
    }
    if (action === "email-next") { if (!emailPreviewBusy && emailNextOffset !== null) pageEmailLocally(emailNextOffset); return true; }
    if (action === "email-prev") { if (!emailPreviewBusy) pageEmailLocally(Math.max(0,emailPreviewOffset-emailPageSize)); return true; }
    if (action === "email-select-page") { emailSelected = new Set(visibleEmailMessages().filter(m => !m.excluded).map(m => m.input_id)); emailWholeRange = false; emailManualSelection = true; refresh(); return true; }
    if (action === "email-clear-selection") { emailSelected.clear(); emailWholeRange = false; emailManualSelection = true; emailStartOperationId = null; refresh(); return true; }
    if (action === "email-select-range") { emailSelected.clear(); emailWholeRange = true; emailManualSelection = true; refresh(); return true; }
    if (action === "email-start-job") { await syncEmail(); return true; }
    if (action === "email-update-recognition") { await updateEmailRecognition(); return true; }
    if (action === "email-reprocess") { await startEmailReprocess(); return true; }
    if (action === "email-reprocess-retry") { await startEmailReprocess((emailJob?.items || []).filter(item => ["failed", "interrupted"].includes(item.state)).map(item => item.input_id), { confirm: false }); return true; }
    if (action === "email-reprocess-cancel") { emailReprocessConfirmIds = []; emailReprocessScope = null; emailUpdateUnknown = false; emailReprocessOperationId = null; if (host.isDialogOpen?.(".email-reprocess-confirm")) host.closeDialog(); return true; }
    if (action === "email-reprocess-confirm") { await confirmEmailReprocess(); return true; }
    if (action === "email-stop-job") { await emailJobAction("stop"); return true; }
    if (action === "email-retry-job") { await emailJobAction("retry"); return true; }
    if (action === "email-poll") { await emailJobAction("poll"); return true; }
    if (action === "sync") {
      await sync();
      return true;
    }
    if (action === "remove-credentials") {
      if (credentialsBlocked() || !allowed()) return true;
      const stamp = current(),
        form = button.closest?.("form");
      busy = true;
      error = "";
      if (form) lockForm(form, true);
      refresh();
      try {
        const result = await api("/api/sources/credentials/remove", {});
        if (valid(stamp)) {
          credentials = result;
          modal = null;
          if (formIsOpen(form)) host.closeDialog();
          host.toast("教学网连接已移除");
          void schedule.load();
        }
      } catch (e) {
        if (valid(stamp)) {
          if (form) formError(form, describeImportError(e));
          else error = describeImportError(e);
        }
      } finally {
        if (valid(stamp)) {
          busy = false;
          if (form) lockForm(form, false);
          refresh();
        }
      }
      return true;
    }
    if (action === "remove-email-credentials") {
      if (credentialsBlocked() || !allowed()) return true;
      emailSettingsRevision++;
      const stamp = current(), form = button.closest?.("form");
      if (form) lockForm(form, true);
      try {
        const result = await api("/api/sources/email/credentials/remove", {}, "POST");
        if (valid(stamp)) {
          emailSettingsRevision++;
          clearEmailPointer(emailCredentials); emailPageSize = 25;
          emailCredentials = result;
          emailPreferences = { ...emailPreferences, consent_granted: false };
          emailPreviewRequestId++; emailPreviewBusy = false; emailPreviewApplyRange = false; emailMessages = [];
          emailMessageById = new Map(); emailLoadedIds = []; emailSnapshotId = null; emailCacheInfo = null; emailRecognitionUpdates = null; emailReprocessScope = null; emailExplicitRangeRead = false;
          emailSelected = new Set();
          emailPreviewed = false;
          emailManualSelection = false;
          emailLoadedRange = null;
          emailPreviewOffset = 0; emailSnapshotId = null; emailCacheInfo = null; emailMessageById = new Map(); emailLoadedIds = []; emailExplicitRangeRead = false;
          emailError = "";
          modal = null;
          emailSettingsState = "ready";
          if (formIsOpen(form)) host.closeDialog();
          host.toast("邮箱连接已移除");
          refresh();
        }
      } catch (e) {
        if (valid(stamp) && form) formError(form, describeImportError(e));
      } finally {
        if (valid(stamp) && form) lockForm(form, false);
      }
      return true;
    }
    if (action === "refresh-review") {
      if (blocked() || !allowed()) return true;
      const review = modal,
        stamp = current(),
        form = button.closest?.("form");
      if (review?.kind !== "review") return true;
      const currentReview = () => valid(stamp) && modal === review && formIsOpen(form);
      if (form) lockForm(form, true);
      try {
        await host.refresh();
        if (!currentReview()) return true;
        await load();
        if (currentReview()) {
          const item = itemById(review.id);
          if (item?.state === "pending") openReview(item);
          else {
            modal = null;
            host.closeDialog();
            host.toast("这项通知已处理");
          }
        }
      } catch (e) {
        if (currentReview()) {
          if (form) formError(form, describeImportError(e), true);
          else error = describeImportError(e);
          refresh();
        }
      } finally {
        if (form) lockForm(form, false);
      }
      return true;
    }
    return false;
  }
  async function submit(form) {
    const kind = form.dataset.importForm;
    if (!kind) return false;
    if (blocked() || !allowed()) return true;
    const values = Object.fromEntries(new FormData(form));
    if (kind === "text") {
      await importText(values);
      return true;
    }
    const stamp = current();
    if (kind === "credentials") {
      if (credentialsBlocked()) return true;
      busy = true;
      formError(form, "");
      lockForm(form, true);
      refresh();
      try {
        const result = await api("/api/sources/credentials", {
          username: String(values.username || "").trim(),
          password: values.password,
        });
        if (valid(stamp)) {
          credentials = result;
          modal = null;
          if (formIsOpen(form)) host.closeDialog();
          host.toast("教学网已连接");
          void schedule.load();
        }
      } catch (e) {
        if (valid(stamp)) formError(form, describeImportError(e));
      } finally {
        if (valid(stamp)) {
          busy = false;
          lockForm(form, false);
          refresh();
        }
      }
      return true;
    }
    if (kind === "email-credentials") {
      const address = String(values.address || "").trim().toLowerCase();
      const username = String(values.username || "").trim();
      const password = String(values.password || "");
      const sameConnection = emailCredentials?.configured && address === emailCredentials.address && username === emailCredentials.username;
      const settingsOnly = sameConnection && !password.trim();
      if (!settingsOnly && !password.trim()) {
        formError(form, emailCredentials?.configured ? "更换邮箱地址或登录账号时，请填写客户端专用密码" : "请填写邮箱客户端专用密码");
        return true;
      }
      if (!settingsOnly && credentialsBlocked()) return true;
      emailSettingsRevision++;
      busy = true;
      formError(form, "");
      lockForm(form, true);
      refresh();
      try {
        if (!valid(stamp)) return true;
        const preferencesBody = {consent: values.consent === "true", process_all: values.process_all === "true"};
        if (settingsOnly) {
          const preferences = await api("/api/sources/email/preferences", preferencesBody, "POST");
          if (!valid(stamp)) return true;
          emailSettingsRevision++;
          emailPreferences = preferences;
          emailManualSelection = false;
        } else {
          const result = await api("/api/sources/email/credentials", {
            address, username, password, ...preferencesBody,
          }, "POST");
          if (!valid(stamp)) return true;
          const preferences = await api("/api/sources/email/preferences");
          if (!valid(stamp)) return true;
          if (emailCredentials?.address?.toLowerCase() !== result.address?.toLowerCase() || emailCredentials?.username !== result.username) {
            clearEmailPointer(emailCredentials); clearEmailPointer(result); emailPageSize = 25;
          }
          emailSettingsRevision++;
          emailCredentials = result;
          emailPreferences = preferences;
          emailManualSelection = false;
          emailPreviewRequestId++; emailPreviewBusy = false; emailPreviewApplyRange = false; emailMessages = [];
          emailMessageById = new Map(); emailLoadedIds = []; emailSnapshotId = null; emailCacheInfo = null; emailRecognitionUpdates = null; emailReprocessScope = null; emailExplicitRangeRead = false;
          emailSelected = new Set();
          emailPreviewed = false;
          emailManualSelection = false;
          emailLoadedRange = null;
          emailPreviewOffset = 0; emailSnapshotId = null; emailCacheInfo = null; emailMessageById = new Map(); emailLoadedIds = []; emailExplicitRangeRead = false;
        }
        emailError = "";
        modal = null;
        emailSettingsState = "ready";
        if (formIsOpen(form)) host.closeDialog();
        host.toast(settingsOnly ? "邮箱设置已保存" : "北大邮箱已连接");
      } catch (e) {
        if (valid(stamp)) formError(form, describeImportError(e));
      } finally {
        if (valid(stamp)) {
          busy = false;
          lockForm(form, false);
          refresh();
        }
      }
      return true;
    }
    if (kind === "review") {
      const item = itemById(form.dataset.candidate);
      if (
        !item ||
        item.state !== "pending" ||
        modal?.kind !== "review" ||
        item.version !== modal.version
      ) {
        formError(form, "这项通知已被处理，请刷新后重新核对", true);
        return true;
      }
      busy = true;
      formError(form, "");
      lockForm(form, true);
      refresh();
      try {
        await flushStore();
        if (!valid(stamp)) return true;
        const target = host
          .getState()
          .tasks.find((task) => task.id === values.target_id);
        if (
          getTaskVersion() !== modal.tasksVersion ||
          taskFingerprint(target) !== modal.targetFingerprint
        ) {
          const conflict = new Error("内容刚刚发生变化，请刷新后重新核对");
          conflict.status = 409;
          throw conflict;
        }
        const body = buildCandidateResolution(
          item,
          values,
          host.getState().tasks,
          modal.tasksVersion,
        );
        const result = await api(
          "/api/recognition/candidates/" +
            encodeURIComponent(item.id) +
            "/resolve",
          body,
        );
        if (!valid(stamp)) return true;
        acceptBatches(result.batches);
        modal = null;
        if (formIsOpen(form)) host.closeDialog();
        try {
          await host.refresh();
          if (valid(stamp)) {
            host.toast(
              body.action === "create"
                ? "已加入任务"
                : body.action === "cancel"
                  ? "任务已取消"
                  : "任务已更新",
            );
          }
        } catch (e) {
          if (valid(stamp))
            error = "任务已保存，刷新页面时出现问题，请刷新后查看";
        }
      } catch (e) {
        if (valid(stamp)) formError(form, describeImportError(e), e.status === 409);
      } finally {
        if (valid(stamp)) {
          busy = false;
          lockForm(form, false);
          refresh();
          if (source === "email" && emailPreviewed) void previewEmail(emailPreviewOffset, { autoSelectInitial: false });
        }
      }
      return true;
    }
    return false;
  }
  function input(event) {
    if (materials.input(event)) return true;
    if (schedule.input(event)) return true;
    const target = event.target;
    if (target.dataset?.emailFilter !== undefined) {
      if (!["all","attention","unprocessed","pending_review","has_tasks","no_tasks","failed","excluded"].includes(target.value)) return true;
      emailFilter = target.value;
      pageEmailLocally(0);
      return true;
    }
    if (target.dataset?.emailMessage) {
      emailManualSelection = true;
      if (target.checked) emailSelected.add(target.dataset.emailMessage);
      else emailSelected.delete(target.dataset.emailMessage);
      emailError = "";
      refresh();
      return true;
    }
    if (target.dataset?.emailRangeLimit !== undefined) {
      emailRange = target.value === "all" ? { limit: null, start_date: emailRange.start_date || "" } : { limit: 50, start_date: null }; refresh(); return true;
    }
    if (target.dataset?.emailUpdateUnknown !== undefined) { if (!emailStartBusy && emailReprocessScope) { emailUpdateUnknown = target.checked; emailReprocessOperationId = null; renderEmailReprocessConfirm(); } return true; }
    if (target.dataset?.emailStartDate !== undefined) { emailRange = { ...emailRange, start_date: target.value || null }; return true; }
    if (target.dataset?.emailLimit !== undefined) { const value = Number(target.value); if (Number.isInteger(value) && value > 0) emailRange = {limit:value,start_date:null}; return true; }
    if (target.id === "import-text") {
      text = target.value;
      return true;
    }
    if (target.id === "import-published") {
      published = target.value;
      return true;
    }
    if (target.dataset?.importField && modal?.kind === "review") {
      const form = target.closest("form");
      modal.values = {
        ...modal.values,
        ...Object.fromEntries(new FormData(form)),
      };
      if (target.dataset.importField === "action") modal.values.target_id = "";
      showReview();
      return true;
    }
    return false;
  }
  function setVisible(visible) {
    sourceSync.setVisible(visible);
  }
  function setSource(next) {
    if (!["text", "teaching", "email", "files"].includes(next)) return false;
    source = next;
    error = "";
    notice = "";
    return true;
  }
  return { render, renderEmailSettings, loadEmailSettings, click, submit, input, reset, load, setVisible, setSource, cancelRecoveryNavigation: () => sourceSync.cancelRecoveryNavigation(), dismissRecoveryHighlight: () => sourceSync.dismissRecoveryHighlight(), loadSourceSync: () => sourceSync.load(), toggle: (event) => sourceSync.toggle(event) || materials.toggle(event) };
}
