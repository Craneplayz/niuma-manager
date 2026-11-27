import { describeUserError, userErrorMarkup } from "./user-errors.js";
import {
  api,
  session,
  accountVersion,
  flushStore,

  acceptStoredCandidates,
  readStoredCandidates,
} from "./team-store.js";
import { renderPageHeading } from "./page-layout.js";
import { failureSources } from "./import-evidence.js";
import { createSourceSyncUI } from "./source-sync-ui.js";
import { createSyncScheduleUI } from "./source-schedule-ui.js";
import { importResultStatus, importStatusMarkup, importTypeMarkup, importCategorySummaries } from "./import-result-status.js";

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
  let batches = [],
    credentials = null,
    loaded = false,
    loading = false,
    busy = false,
    stopping = false,
    error = "",
    notice = "",
    progress = "",
    controller = null,
    revision = 0,
    batchesRevision = 0;
  let batchesRead = null, historyError = '';
  let credentialsLoading = false, credentialsLoaded = false, credentialsError = null, credentialsRead = null, credentialsRevision = 0;
  const historyCategories = new Map(), historyProblems = new Map();
  const current = () => ({ epoch: accountVersion(), revision });
  const valid = (stamp) =>
    stamp.epoch === accountVersion() && stamp.revision === revision;
  function acceptBatches(next) {
    if (!Array.isArray(next)) throw new Error("导入结果暂时无法读取");
    acceptStoredCandidates(next.flatMap(batch => batch.items || []));
    batches = next;
    batchesRevision++;
    historyError = '';
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
  const sourceSync = createSourceSyncUI({
    ...host, api, flushStore, accountVersion, isDemo: demo,
    describeError: describeImportError,
    isAuthenticated: () => Boolean(session.user && !session.changing),
    isImportBusy: () => busy,
    render: refresh,
    onChanged: refreshResults,
    getCandidate: id => itemById(id),
    openHistoryItem(id) {
      const status = importResultStatus({ candidate_id: id }, "review", { ...host, getCandidate: itemById });
      if (status.taskId) host.openTask?.(status.taskId);
      else host.toast(status.message);
    },
  });
  const syncBlocked = () => busy || credentialsLoading || stopping || sourceSync.blocksStart();
  const syncOwnsBatch = batch => batch?.sourceSyncManaged === true || sourceSync.ownsRequest(batch?.request?.request_id);
  const refreshSourceSync = () => sourceSync.load();
  const schedule = createSyncScheduleUI({
    ...host, isDemo: demo, isConnected: () => Boolean(credentials?.configured),
    isBusy: syncBlocked,
    isRunning: () => sourceSync.isRunning(),
    getProgressLabel: () => sourceSync.progressLabel(),
    canStop: () => sourceSync.canStop(),
    isStopping: () => stopping,
    onStarting: sourceSync.clearCompletionNotice,
    start: () => sourceSync.start(),
  });
  // Background teaching runs own their start/stop controls, not the whole import page.
  const blocked = () => loading || busy;
  const credentialsBlocked = () => blocked() || credentialsLoading || stopping || sourceSync.isBusy() || schedule.isBusy();
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
    readStoredCandidates(batches.flatMap((batch) =>
      (batch.items || []).map((item) => ({ ...item, batch })),
    ));
  const itemById = (id) => allItems().find((item) => item.id === id);
  function errorMarkup(value, action = "teaching_sync", context) {
    return userErrorMarkup(value, action, context);
  }
  function reset() {
    revision++;
    controller?.abort();
    controller = null;
    batches = [];
    historyError = '';
    batchesRead = null;
    credentials = null;
    credentialsLoading = credentialsLoaded = false;
    credentialsError = credentialsRead = null;
    credentialsRevision++;
    loaded = false;
    loading = false;
    busy = false;
    stopping = false;
    error = "";
    notice = "";
    progress = "";
    sourceSync.reset(); schedule.reset();
    historyCategories.clear(); historyProblems.clear();
  }
  globalThis.window?.addEventListener("niuma:account-reset", reset);
  globalThis.window?.addEventListener("niuma:review-store", () => {
    if (!session.user || session.changing) return;
    if (host.onWorkspaceChange) host.onWorkspaceChange();
    else refresh();
  });
  globalThis.window?.addEventListener('niuma:bulk-saved', event => {
    if (Array.isArray(event.detail?.batches)) {
      acceptBatches(event.detail.batches);
      refresh();
    } else if (event.detail && event.detail.historyChanged !== false) {
      void refreshHistory().catch(() => {});
    }
  });
  async function refreshHistory() {
    if (!session.user || session.changing || demo()) return;
    const stamp = current(), expectedRevision = ++batchesRevision;
    historyError = '';
    try {
      const next = await api('/api/recognition/batches');
      if (!valid(stamp)) throw new Error('账号已切换，请重新操作');
      if (expectedRevision === batchesRevision) acceptBatches(next);
    } catch (error) {
      if (valid(stamp)) historyError = error;
      throw error;
    } finally {
      if (valid(stamp)) refresh();
    }
  }
  function loadHistory() {
    if (batchesRead) return batchesRead;
    if (busy || !session.user || session.changing || demo()) return Promise.resolve();
    const stamp = current(), previousBatchesRevision = batchesRevision;
    loading = true;
    historyError = '';
    refresh();
    const pending = (async () => {
      try {
        const next = await api('/api/recognition/batches');
        if (valid(stamp) && previousBatchesRevision === batchesRevision && !busy) acceptBatches(next);
      } catch (cause) {
        if (valid(stamp) && previousBatchesRevision === batchesRevision) historyError = cause;
      } finally {
        if (valid(stamp)) {
          loading = false; loaded = true;
          refresh();
        }
      }
    })();
    batchesRead = pending;
    pending.finally(() => { if (batchesRead === pending) batchesRead = null; });
    return pending;
  }
  function loadCredentials(force = false) {
    if (credentialsRead) return credentialsRead;
    if (!session.user || session.changing || demo()) return Promise.resolve(false);
    if (credentialsLoaded && !force) return Promise.resolve(true);
    const stamp = current(), version = credentialsRevision;
    credentialsLoading = true;
    credentialsError = null;
    refresh();
    const pending = (async () => {
      try {
        const next = await api('/api/sources/credentials');
        if (!valid(stamp) || version !== credentialsRevision) return false;
        credentials = next;
        credentialsLoaded = true;
        return true;
      } catch (cause) {
        if (valid(stamp) && version === credentialsRevision) credentialsError = cause;
        return false;
      } finally {
        if (valid(stamp) && version === credentialsRevision) { credentialsLoading = false; refresh(); }
      }
    })();
    credentialsRead = pending;
    pending.finally(() => { if (credentialsRead === pending) credentialsRead = null; });
    return pending;
  }
  async function load() {
    if (busy || !session.user || session.changing || demo()) return;
    const stamp = current(), reading = loadHistory();
    void loadCredentials().then(() => {
      if (valid(stamp)) { void refreshSourceSync(); void schedule.load(); }
    });
    await reading;
  }
  function teachingPanel() {
    const connected = credentials?.configured;
    return `<section class="teaching-source"><div class="teaching-import"><div class="teaching-account"><h2>教学网</h2><div class="teaching-connection"><span class="status-note">${credentialsLoading ? "正在读取连接…" : connected ? "已连接 " + esc(credentials.username) : "连接账号，获取课程作业与通知"}</span>${connected ? `<button class="btn quiet" type="button" data-import-action="credentials" ${credentialsBlocked() ? "disabled" : ""}>管理连接</button>` : ""}</div></div>${schedule.render()}</div>${credentialsError ? `<div class="teaching-connection-error">${errorMarkup(credentialsError, 'teaching_sync')}<button class="btn quiet" type="button" data-import-action="refresh-credentials">重试读取连接</button></div>` : ''}${sourceSync.renderProgress(schedule.isBusy())}</section>`;
  }
  function independentHistory() {
    const groups = new Map(), tasks = host.getState().tasks || [];
    for (const batch of batches) {
      if (syncOwnsBatch(batch) || batch.request.contract_version === "0.3.1") continue;
      const teaching = batch.request.records?.some(record => record.platform === "teaching_web");
      if (!teaching) continue;
      const id = batch.importId || batch.request.request_id;
      if (!groups.has(id)) groups.set(id, { id, batches: [], time: batch.importedAt || batch.request.records?.[0]?.collected_at });
      groups.get(id).batches.push(batch);
    }
    return [...groups.values()].map(group => {
      const ids = new Set(group.batches.map(batch => batch.request.request_id));
      const receipts = group.batches.flatMap(batch => batch.items || []);
      const knownCandidates = new Set(receipts.map(item => item.id));
      const added = [], updated = [];
      for (const item of receipts.filter(item => item.state === "applied")) {
        const task = tasks.find(task => task.recognitionHistory?.some(event => event.storageId === item.id));
        const event = task?.recognitionHistory.find(entry => entry.storageId === item.id);
        const category = event && event.action !== "create" ? updated : added;
        category.push({ label: item.candidate.title, candidate_id: item.id, task_id: task?.id });
      }
      for (const task of tasks) {
        const events = (task.recognitionHistory || []).filter(event => ids.has(event.requestId) && !knownCandidates.has(event.storageId));
        if (events.some(event => event.action === "create")) added.push({ label: task.title, task_id: task.id });
        else if (events.length) updated.push({ label: task.title, task_id: task.id });
      }
      const review = group.batches.flatMap(batch => (batch.items || []).filter(item => item.state === "ignored")
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
    const labels = { added: "新增", review: "历史记录", updated: "修改", error: "错误" };
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
    const detail = problem ? `<div class="sync-problem" role="region" aria-label="${esc(problem.label)}的处理情况"><strong>${esc(problem.label)}</strong>${errorMarkup(batchErrorReport(problem.batch, problem.input_id), "teaching_sync", JSON.stringify(["recognition",problem.batch.request.request_id,problem.input_id]))}${problem.links ? `<p class="status-note">${problem.links}</p>` : ""}</div>` : "";
    const retry = group.batches.filter(batch => ["failed", "partial_success"].includes(batch.result?.status) && !incomplete(batch));
    const results = group.added.length || group.review.length || group.updated.length;
    const statusLabel = group.batches.every(batch => batch.result?.status === 'no_tasks') ? '未识别出任务' : null;
    return `<article class="sync-run" data-import-history-batch-entry="${esc(group.id)}"><div class="sync-run-heading"><div class="sync-run-status"><strong role="status">${esc(statusLabel || (group.problems.length ? results ? "部分完成" : "导入未完成" : "导入完成"))}</strong><div class="sync-categories" role="group" aria-label="查看本轮导入结果">${summary}</div></div>${retry.length ? `<div class="candidate-actions"><button class="btn quiet" type="button" data-import-history-retry="${esc(group.id)}" ${blocked() ? "disabled" : ""}>处理未完成</button></div>` : ""}</div><div id="${esc(region)}" class="sync-results" role="region" aria-label="${labels[category] || "导入"}内容" ${category ? "" : "hidden"}>${names ? `<ul class="sync-result-list">${names}</ul>` : ""}${detail}</div></article>`;
  }
  function render() {
    if (session.user && !demo() && !loaded && !loading && !busy) queueMicrotask(load);
    const history = independentHistory().map(group => ({ time: group.time, html: independentBatchMarkup(group) }));
    return `${(host.renderPageHeading || renderPageHeading)({ title: "导入", escapeHtml: esc })}<div class="page-reading">
      <div class="import-source-tabs" role="group" aria-label="导入来源"><span class="btn selected">教学网</span><button type="button" class="btn quiet" disabled title="邮箱尚未接入">邮箱 <span class="status-note">尚未接入</span></button></div>
      <section class="import-input">${teachingPanel()}
        ${busy && progress ? `<div class="import-progress" role="status"><span class="status-note">${esc(progress)}</span><button class="btn quiet" type="button" data-import-action="stop">停止导入</button></div>` : ""}
        ${!demo() ? sourceSync.renderHistory({ entries: history, includeSync: true }) : ""}
        ${errorMarkup(error)}${notice && !busy ? `<p class="status-note" role="status">${esc(notice)}</p>` : ""}
      </section>
      ${historyError ? errorMarkup(historyError) + '<button type="button" class="btn quiet" data-import-action="retry-history">重试读取导入历史</button>' : ""}
    </div>`;
  }
  function dialogHeader(title) {
    return `<header class="dialog-header"><h2>${esc(title)}</h2><button class="icon-button" type="button" data-import-action="close" aria-label="关闭">${host.icon("close")}</button></header>`;
  }
  function openCredentials() {
    host.openDialog(
      `<form data-import-form="credentials">${dialogHeader("连接教学网")}<label class="field">教学网账号<input name="username" required maxlength="200" autocomplete="username" value="${esc(credentials?.username || "")}"></label><label class="field">教学网密码<input name="password" type="password" required maxlength="200" autocomplete="current-password"></label><div class="error" data-import-error hidden></div><div class="dialog-actions"><button class="btn primary" type="submit">保存连接</button>${credentials?.configured ? '<button class="btn quiet" type="button" data-import-action="remove-credentials">移除连接</button>' : ""}</div></form>`,
    );
  }
  function formError(form, message) {
    const report = message ? describeUserError(message) : null;
    const node = form.querySelector("[data-import-error]");
    if (node) {
      node.textContent = report?.message || "";
      node.innerHTML = userErrorMarkup(report, "teaching_sync");
      node.hidden = !report;
    }
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
    if (syncBlocked()) return;
    if (!credentials?.configured) { openCredentials(); return; }
    await schedule.start();
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
      if (!valid(stamp)) return;
      await host.refresh();
      if (!valid(stamp)) return;
      const retried = next.find(item => item.request?.request_id === batch.request.request_id);
      const failed = ["failed", "partial_success"].includes(retried?.result?.status);
      notice = controller.signal.aborted ? "已停止导入，后台已开始的识别可能继续，可刷新查看结果。" :
        failed ? "仍有内容识别失败，请查看具体原因；成功结果已保留。" :
        "识别结果已更新";
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
    if (d.sourceSyncAction) {
      if (allowed()) await sourceSync.click(button);
      return true;
    }
    if (d.syncScheduleAction) return schedule.click(button);
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
          const status = importResultStatus(item, category, { ...host, getCandidate: itemById });
          if (status.taskId) host.openTask?.(status.taskId);
          else if (status.message) host.toast(status.message);
        }
      }
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
    if (action === 'retry-history') { await loadHistory(); return true; }
    if (action === 'refresh-credentials') {
      if (allowed() && !busy) { await loadCredentials(true); void schedule.load(); }
      return true;
    }
    if (action === "close") {
      if (!busy) {
        host.closeDialog();
      }
      return true;
    }
    if (action === "stop") {
      controller?.abort();
      return true;
    }
    if (action === "credentials") {
      if (credentialsBlocked() && !credentialsLoading || !allowed()) return true;
      if (credentials) {
        openCredentials();
        return true;
      }
      // The account dialog can reach this form before the import page is loaded.
      const stamp = current(), label = button.textContent;
      button.disabled = true;
      button.textContent = "正在读取…";
      try {
        const ready = await loadCredentials();
        if (valid(stamp) && formIsOpen(button)) {
          if (!ready) throw credentialsError || new Error('教学网连接暂时无法读取');
          openCredentials();
        }
      } catch (e) {
        if (valid(stamp) && formIsOpen(button)) throw e;
      } finally {
        if (valid(stamp)) {
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
          credentialsLoaded = true;
          credentialsError = null;
          credentialsRevision++;
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
    return false;
  }
  async function submit(form) {
    const kind = form.dataset.importForm;
    if (!kind) return false;
    if (blocked() || !allowed()) return true;
    const values = Object.fromEntries(new FormData(form));
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
          credentialsLoaded = true;
          credentialsError = null;
          credentialsRevision++;
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
    return false;
  }
  return { render, click, submit, input: schedule.input, reset, load,
    setVisible: sourceSync.setVisible, cancelRecoveryNavigation: () => sourceSync.cancelRecoveryNavigation(), dismissRecoveryHighlight: () => sourceSync.dismissRecoveryHighlight(), loadSourceSync: () => sourceSync.load(), toggle: sourceSync.toggle };
}
