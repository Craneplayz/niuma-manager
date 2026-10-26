import { renderCourseBadge } from "./course-identity.js";
import { candidateDue, zonedParts } from "./candidate-data.js";
export { candidateDue } from "./candidate-data.js";
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
import { createTextImportJobs, TEXT_JOB_LABELS } from './text-import-jobs.js';
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

function batchErrorReport(batch, inputId) {
  const reports = (batch.result?.errors || []).filter(error => !inputId || error.input_id == null || error.input_id === inputId).map(describeImportError);
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
  let taskReviewsLoading = false, taskReviewsError = '';
  const historyCategories = new Map(), historyProblems = new Map();
  const current = () => ({ epoch: accountVersion(), revision });
  const valid = (stamp) =>
    stamp.epoch === accountVersion() && stamp.revision === revision;
  function acceptBatches(next) {
    if (!Array.isArray(next)) throw new Error("导入结果暂时无法读取");
    batches = next;
    batchesRevision++;
    taskReviewsError = '';
  }
  const refresh = () => host.render();
  const demo = () => Boolean(host.isDemo?.());
  async function refreshResults(isCurrent = () => true) {
    const stamp = current(), previousBatchesRevision = batchesRevision;
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
  const textJobs = createTextImportJobs({
    api, accountVersion, escapeHtml: esc, isDemo: demo,
    isAuthenticated: () => Boolean(session.user && !session.changing),
    render: refresh, onChanged: refreshResults,
  });
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
    controller?.abort();
    controller = null;
    source = host.getImportSource?.() || "text";
    text = "";
    published = shanghaiNow();
    batches = [];
    taskReviewsLoading = false;
    taskReviewsError = '';
    credentials = null;
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
    materials.reset(); textJobs.reset();
    historyCategories.clear(); historyProblems.clear();
  }
  globalThis.window?.addEventListener("niuma:account-reset", reset);
  async function loadTaskReviews() {
    if (taskReviewsLoading || busy || loading || !session.user || session.changing || demo()) return;
    const stamp = current(), previousBatchesRevision = batchesRevision;
    taskReviewsLoading = true;
    taskReviewsError = '';
    try {
      const next = await api('/api/recognition/batches');
      if (valid(stamp) && previousBatchesRevision === batchesRevision && !busy) acceptBatches(next);
    } catch (error) {
      if (valid(stamp)) taskReviewsError = error;
    } finally {
      if (valid(stamp)) { taskReviewsLoading = false; refresh(); }
    }
  }
  const taskReviewsStatus = () => taskReviewsError
    ? `<div class="task-review-load-error">${userErrorMarkup(taskReviewsError, 'import_review')}<button type="button" class="btn quiet" data-import-action="refresh-task-reviews">重试读取待核对</button></div>` : '';
  async function confirmTask(id, task, complete = false, expectedVersion = task.reviewVersion) {
    if (blocked() || !allowed()) throw new Error('正在处理，请稍后重试');
    const stamp = current(), item = itemById(id);
    if (!item || item.state !== 'pending' || item.version !== expectedVersion) {
      const error = new Error('候选已变化，请刷新后重新操作'); error.status = 409; throw error;
    }
    if (!['create', 'uncertain'].includes(item.candidate.notice_type)) throw new Error('请选择明确的变更操作并核对目标任务');
    busy = true;
    refresh();
    try {
      await flushStore();
      if (!valid(stamp)) throw new Error('账号已切换，请重新操作');
      const body = buildCandidateResolution(item, { action: 'create', title: task.title,
        description: task.notes || '', course: task.course || '', date: task.due || '', time: task.dueTime || '' },
        host.getState().tasks, getTaskVersion());
      body.draft.content_type = task.content_type || 'task';
      if (complete) body.complete = true;
      const result = await api('/api/recognition/candidates/' + encodeURIComponent(id) + '/resolve', body);
      if (!valid(stamp)) throw new Error('账号已切换，请重新操作');
      acceptBatches(result.batches);
      try { await host.refresh(); }
      catch { throw new Error('任务已保存，但页面更新失败。请刷新结果。'); }
      if (!valid(stamp)) throw new Error('账号已切换，请重新操作');
      return host.getState().tasks.find(saved => saved.recognitionHistory?.some(event => event.storageId === id));
    } finally {
      if (valid(stamp)) { busy = false; refresh(); }
    }
  }
  async function load() {
    if (loading || busy || !session.user || demo()) return;
    const stamp = current(), previousBatchesRevision = batchesRevision;
    loading = true;
    error = "";
    refresh();
    const results = await Promise.allSettled([
      api("/api/recognition/batches"),
      api("/api/sources/credentials"),
    ]);
    if (!valid(stamp)) return;
    if (results[0].status === "fulfilled" && Array.isArray(results[0].value)) {
      if (previousBatchesRevision === batchesRevision) acceptBatches(results[0].value);
    } else error = describeImportError(results[0].reason || "导入结果暂时无法读取");
    if (results[1].status === "fulfilled") credentials = results[1].value;
    else if (!error) error = describeImportError(results[1].reason);
    loading = false;
    loaded = true;
    refresh();
    if (source === "teaching") { void refreshSourceSync(); void schedule.load(); }
    if (source === "files") void materials.load();
    if (source === "text") void textJobs.load();
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
    return `<article class="candidate-row"><div class="candidate-copy"><p class="status-note">${esc(actions[c.notice_type] || "待确认")} · ${esc(deadlineLabel(c.deadline))}</p><h3>${esc(c.title)}</h3><p class="status-note candidate-course">${renderCourseBadge(c.course_or_project)}<span>${esc(label)}</span></p>${item.state === "pending" ? evidence(item) : ""}</div><div class="candidate-actions">${item.state === "pending" ? `<button class="btn" type="button" data-import-review="${esc(item.id)}" ${blocked() ? "disabled" : ""}>核对${c.notice_type === "create" ? "并加入" : ""}</button><button class="btn quiet" type="button" data-import-ignore="${esc(item.id)}" ${blocked() ? "disabled" : ""}>忽略</button>` : item.state === "ignored" ? `<button class="btn quiet" type="button" data-import-reopen="${esc(item.id)}" ${blocked() ? "disabled" : ""}>撤回忽略</button>` : '<span class="status-note">已处理</span>'}</div></article>`;
  }
  function teachingPanel() {
    const connected = credentials?.configured;
    return `<section class="teaching-source"><div class="teaching-import"><div class="teaching-account"><h2>教学网</h2><div class="teaching-connection"><span class="status-note">${connected ? "已连接 " + esc(credentials.username) : loading ? "正在读取连接…" : "连接账号，获取课程作业与通知"}</span>${connected ? `<button class="btn quiet" type="button" data-import-action="credentials" ${credentialsBlocked() ? "disabled" : ""}>管理连接</button>` : ""}</div></div>${schedule.render()}</div>${sourceSync.renderProgress()}</section>`;
  }
  function independentHistory() {
    const groups = new Map(), tasks = host.getState().tasks || [];
    for (const batch of batches) {
      if (syncOwnsBatch(batch) || materials.owns(batch.request.request_id) || batch.request.contract_version === "0.3.1") continue;
      const teaching = batch.request.records?.some(record => record.platform === "teaching_web");
      if (source === "files" || teaching !== (source === "teaching")) continue;
      const id = batch.importId || batch.request.request_id;
      if (!groups.has(id)) groups.set(id, { id, batches: [], time: batch.importedAt || batch.request.records?.[0]?.collected_at });
      groups.get(id).batches.push(batch);
    }
    if (source === "text") for (const job of textJobs.list()) {
      if (!groups.has(job.request_id)) groups.set(job.request_id, { id: job.request_id, batches: [], time: job.created_at });
      groups.get(job.request_id).job = job;
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
      if (['failed', 'partial_success'].includes(group.job?.state) && group.job.error) {
        // A retry can fail before writing a batch. The job owns the latest
        // attempt's error; the saved batch may still describe an older attempt.
        problems.splice(0, problems.length, {
          label: '文本导入', input_id: group.job.error.input_id || 'job',
          batch: { request: { request_id: group.id }, result: { errors: [group.job.error] } },
        });
      }
      return { ...group, added, review, updated, problems };
    }).filter(group => source === 'text' || group.added.length || group.review.length || group.updated.length || group.problems.length);
  }
  function independentBatchMarkup(group) {
    const collections = { added: group.added, review: group.review, updated: group.updated, error: group.problems };
    const labels = { added: "新增", review: "待核对", updated: "修改", error: "错误" };
    const selected = historyCategories.get(group.id), category = collections[selected]?.length ? selected : null;
    const token = encodeURIComponent(group.id), region = `import-batch-${token}`;
    const summary = importCategorySummaries(collections, { ...host, getCandidate: itemById }).map(({ key, label, count, unit, tone, description }) =>
      `<button id="${esc(region)}-${key}" class="sync-category sync-category-${tone}" type="button" data-import-history-category="${key}" data-import-history-batch="${esc(group.id)}" aria-expanded="${category === key}" aria-controls="${esc(region)}" title="${esc(description)}" aria-description="${esc(description)}">${label} ${count} ${unit}</button>`).join("");
    const items = collections[category] || [];
    const names = items.map((item, index) => {
      const status = importResultStatus(item, category, { ...host, getCandidate: itemById });
      return `<li><button id="${esc(region)}-${category}-${index}" class="sync-result-name" type="button" data-import-history-item="${index}" data-import-history-batch="${esc(group.id)}" title="${esc([item.label,item.course,status.label].filter(Boolean).join(" · "))}" ${category === "error" ? `aria-expanded="${historyProblems.get(group.id) === index}"` : ""}>${importTypeMarkup(status)}<span class="sync-result-title">${esc(item.label)}</span>${importStatusMarkup(status)}</button></li>`;
    }).join("");
    const problem = category === "error" ? items[historyProblems.get(group.id)] : null;
    const detail = problem ? `<div class="sync-problem" role="region" aria-label="${esc(problem.label)}的处理情况"><strong>${esc(problem.label)}</strong>${errorMarkup(batchErrorReport(problem.batch, problem.input_id), source === "teaching" ? "teaching_sync" : "text_import", JSON.stringify(["recognition",problem.batch.request.request_id,problem.input_id]))}${problem.links ? `<p class="status-note">${problem.links}</p>` : ""}</div>` : "";
    const job = group.job;
    const retry = group.batches.filter(batch => ["failed", "partial_success"].includes(batch.result?.status) && !incomplete(batch));
    const results = group.added.length || group.review.length || group.updated.length;
    const statusLabel = job ? TEXT_JOB_LABELS[job.state] : group.batches.every(batch => batch.result?.status === 'no_tasks') ? '未识别出任务' : null;
    const jobControls = job ? textJobs.controls(job) : '';
    const jobNote = job?.state === 'stopped' ? '<p class="status-note">已停止本次导入，后续返回的识别结果不会加入任务。</p>' : job?.state === 'interrupted' ? '<p class="status-note">服务重启导致识别中断，已保存的结果保留，可重试未完成的识别。</p>' : '';
    return `<article class="sync-run" data-import-history-batch-entry="${esc(group.id)}"><div class="sync-run-heading"><div class="sync-run-status"><strong role="status">${esc(statusLabel || (group.problems.length ? results ? "部分完成" : "导入未完成" : "导入完成"))}</strong><div class="sync-categories" role="group" aria-label="查看本轮导入结果">${summary}</div></div>${job ? jobControls : retry.length ? `<div class="candidate-actions"><button class="btn quiet" type="button" data-import-history-retry="${esc(group.id)}" ${blocked() ? "disabled" : ""}>处理未完成</button></div>` : ""}</div>${jobNote}<div id="${esc(region)}" class="sync-results" role="region" aria-label="${labels[category] || "导入"}内容" ${category ? "" : "hidden"}>${names ? `<ul class="sync-result-list">${names}</ul>` : ""}${detail}</div></article>`;
  }
  function render() {
    if (session.user && !demo() && !loaded && !loading && !busy) queueMicrotask(load);
    const items = allItems(), pending = items.filter(item => item.state === "pending"),
      ignored = items.filter(item => item.state === "ignored");
    const history = independentHistory().map(group => ({ time: group.time, html: independentBatchMarkup(group) }));
    const tabs = [["text", "文本"], ["teaching", "教学网"], ["files", "图片与文件"]]
      .map(([key, label]) => `<button type="button" class="btn ${source === key ? "selected" : "quiet"}" data-import-source="${key}" aria-pressed="${source === key}">${label}</button>`).join("");
    const textBlocked = blocked() || textJobs.isActive() || textJobs.isWriting();
    const textPanel = `<form data-import-form="text"><label class="field" for="import-text">通知内容<textarea id="import-text" name="text" rows="6" maxlength="4000" placeholder="粘贴通知、邮件正文或任务内容" ${textBlocked ? "disabled" : ""}>${esc(text)}</textarea></label><details class="import-time"><summary>通知时间</summary><label class="field" for="import-published">发布时间<input id="import-published" name="published" type="datetime-local" step="1" value="${esc(published)}" ${textBlocked ? "disabled" : ""}></label><p class="status-note">按此时间理解“今天”“明天”。</p></details><div class="import-form-actions"><button class="btn primary" type="submit" ${textBlocked ? "disabled" : ""}>${(busy && source === "text") || textJobs.isActive() ? "正在识别…" : "识别任务"}</button></div></form>`;
    const panel = source === "text" ? textPanel : source === "teaching" ? teachingPanel()
      : materials.render();
    return `${(host.renderPageHeading || renderPageHeading)({ title: "导入", escapeHtml: esc })}<div class="page-reading">
      <div class="import-source-tabs" role="group" aria-label="导入来源">${tabs}<button type="button" class="btn quiet" disabled title="邮箱尚未接入">邮箱 <span class="status-note">尚未接入</span></button></div>
      <section class="import-input">${panel}${source === "text" ? textJobs.renderProgress() : ""}
        ${busy && progress && source !== "text" ? `<div class="import-progress" role="status"><span class="status-note">${esc(progress)}</span><button class="btn quiet" type="button" data-import-action="stop">停止导入</button></div>` : ""}
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
      `<form data-import-form="credentials">${dialogHeader("连接教学网")}<label class="field">教学网账号<input name="username" required maxlength="200" autocomplete="username" value="${esc(credentials?.username || "")}"></label><label class="field">教学网密码<input name="password" type="password" required maxlength="200" autocomplete="current-password"></label><div class="error" data-import-error hidden></div><div class="dialog-actions"><button class="btn primary" type="submit">保存连接</button>${credentials?.configured ? '<button class="btn quiet" type="button" data-import-action="remove-credentials">移除连接</button>' : ""}</div></form>`,
    );
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
        target_id: host.getState().tasks.some(task => task.id === item.candidate.target_reference) ? item.candidate.target_reference : "",
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
    const exact =
      item.candidate.deadline?.precision === "datetime" &&
      item.candidate.deadline.value &&
      Number.isFinite(Date.parse(item.candidate.deadline.value)) &&
      new Date(item.candidate.deadline.value).getUTCSeconds();
    host.openDialog(
      `<form data-import-form="review" data-candidate="${esc(item.id)}">${dialogHeader("核对任务")}<p class="status-note">${esc(item.candidate.title)}</p>${
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
      }${action && action !== "create" ? `<label class="field">目标任务<select name="target_id" required data-import-field="target"><option value="">选择已有任务</option>${tasks.map((task) => `<option value="${esc(task.id)}" ${task.id === v.target_id ? "selected" : ""}>${esc(task.title)}</option>`).join("")}</select></label>${target ? `<p class="status-note">当前任务：${esc(target.title)}${target.due ? " · " + esc(target.due) + (target.dueTime ? " " + esc(target.dueTime) : "") : ""}</p>` : ""}` : ""}${["create", "update"].includes(action) ? `<label class="field">任务名称<input name="title" required maxlength="180" value="${esc(v.title)}"></label><label class="field">课程<input name="course" maxlength="200" value="${esc(v.course)}" placeholder="可不填写"></label><label class="field">说明<textarea name="description" rows="3">${esc(v.description)}</textarea></label>` : ""}${action && action !== "cancel" ? `<div class="form-grid"><label class="field">截止日期<input name="date" type="date" value="${esc(v.date)}" ${action === "postpone" ? "required" : ""}></label><label class="field">截止时刻<input name="time" type="time" value="${esc(v.time)}"></label></div><p class="status-note">${exact ? "原文时间包含秒，请确认要保存的时刻。" : "原文只有日期时，时刻可以留空。"}</p>` : ""}${action === "cancel" ? '<p class="status-note">确认后将取消所选任务，原记录保留。</p>' : ""}${evidence(item)}<div class="error" data-import-error hidden></div><div class="dialog-actions"><button class="btn primary" type="submit" ${!action ? "disabled" : ""}>${action === "create" ? "确认加入任务" : action === "cancel" ? "确认取消任务" : "确认保存"}</button><button class="btn quiet" type="button" data-import-action="refresh-review" hidden>刷新后重新核对</button></div></form>`,
    );
  }
  function formError(form, message, conflict = false) {
    const report = message ? describeUserError(message) : null;
    const node = form.querySelector("[data-import-error]");
    if (node) {
      node.textContent = report?.message || "";
      node.innerHTML = userErrorMarkup(report, form.dataset.importForm === "credentials" ? "teaching_sync" : "import_review");
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
    if (blocked() || textJobs.isActive() || !allowed()) return;
    text = String(values.text || "");
    published = String(values.published ?? published);
    const stamp = current();
    try {
      const signature = JSON.stringify([text.trim(), published]);
      if (request?.signature !== signature)
        request = { signature, body: buildTextImport(text, published) };
      busy = true; error = ""; notice = ""; refresh();
      if (!await textJobs.start(buildRecognitionRequest(request.body)) || !valid(stamp)) return;
      const next = await api('/api/recognition/batches');
      if (!valid(stamp)) return;
      acceptBatches(next); loaded = true;
      const job = textJobs.get(request.body.request_id);
      notice = job?.state === 'no_tasks' ? '未识别出任务，本次结果已保留在历史导入中。'
        : job?.state === 'success' ? '识别完成，请核对下方任务' : '';
    } catch (e) {
      if (valid(stamp)) error = describeImportError(e);
    } finally {
      if (valid(stamp)) { busy = false; refresh(); }
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
        host.toast(action === "ignore" ? "已忽略" : "已恢复待核对", action === 'ignore' ? async () => {
          const restored = await resolveSmall(itemById(item.id), 'reopen');
          if (!restored) throw new Error('无法恢复待核对，请稍后重试');
        } : undefined);
        return true;
      }
    } catch (e) {
      if (valid(stamp)) {
        error = describeImportError(e);
        if (host.isImportVisible?.() === false) host.reportReviewError?.(e);
      }
      return false;
    } finally {
      if (valid(stamp)) {
        busy = false;
        refresh();
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
      const retried = next.find(item => item.request?.request_id === batch.request.request_id);
      const failed = ["failed", "partial_success"].includes(retried?.result?.status);
      notice = controller.signal.aborted ? "已停止导入，后台已开始的识别可能继续，可刷新查看结果。" :
        failed ? "仍有内容识别失败，请查看具体原因；成功结果已保留。" :
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
    if (d.textJobAction) { if (allowed()) await textJobs.click(button); return true; }
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
        if (source === "teaching" && allowed()) { void refreshSourceSync(); void schedule.load(); }
        if (source === "files" && allowed()) void materials.load();
        refresh();
      return true;
    }
    if (d.importHistoryCategory || d.importHistoryItem !== undefined || d.importHistoryRetry) {
      if (!allowed()) return true;
      const group = independentHistory().find(item => item.id === (d.importHistoryBatch || d.importHistoryRetry));
      if (!group) return true;
      if (d.importHistoryRetry && source === 'text') {
        try {
          // Legacy synchronous failures acquire a durable record before retrying.
          for (const batch of group.batches) {
            const job = textJobs.get(batch.request.request_id);
            if (!job) await textJobs.start(batch.request);
            else if (['failed','partial_success','stopped','interrupted'].includes(job.state)) await textJobs.retry(job);
          }
          await refreshResults();
        } catch (e) { error = describeImportError(e); refresh(); }
      } else if (d.importHistoryRetry) {
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
    if (action === 'refresh-task-reviews') { await loadTaskReviews(); return true; }
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
    textJobs.setVisible(visible && source === "text");
  }
  function setSource(next) {
    if (!["text", "teaching", "files"].includes(next)) return false;
    source = next;
    error = "";
    notice = "";
    return true;
  }
  return { render, click, submit, input, reset, load, loadTaskReviews, taskReviewsStatus, confirmTask,
    reviewEvidence: id => evidence(itemById(id)),
    getReviewItems: () => allItems().filter(item => item.state === 'pending'),
    getCourseNames: () => allItems().map(item => item.candidate?.course_or_project).filter(Boolean),
    isReviewBusy: blocked,
    setVisible, setSource, cancelRecoveryNavigation: () => sourceSync.cancelRecoveryNavigation(), dismissRecoveryHighlight: () => sourceSync.dismissRecoveryHighlight(), loadSourceSync: () => sourceSync.load(), toggle: (event) => sourceSync.toggle(event) || materials.toggle(event) };
}
