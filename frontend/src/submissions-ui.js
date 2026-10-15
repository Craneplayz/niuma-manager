import { api, session, accountVersion, flushStore } from "./team-store.js";
import { newId } from "./id.js";

const API = "/api/sources/submissions", VERSION = "submission-operations/0.1";
const states = { submitted: "已提交", active: "未提交", unknown: "状态待确认" };
const decisions = { pending: "待核对", confirmed: "未完成", edited: "已编辑", completed: "已完成", ignored: "已忽略", deleted: "已删除" };
const readStates = { complete: "已读取", partial: "部分读取", missing: "未取得", unread: "未读取" };

export function createSubmissionsUI(host) {
  const esc = host.escapeHtml;
  let rows = [], detail = null, source = null, opened = false, dialogOpen = false, loaded = false;
  let busy = false, error = "", notice = "", generation = 0, scope = "unknown", links = {}, operation = null;
  const stamp = () => [generation, accountVersion()];
  const valid = value => value[0] === generation && value[1] === accountVersion() && session.user && !session.changing && !host.isDemo();
  const ownsDialog = () => dialogOpen && host.isDialogOpen?.("[data-submission-dialog]") !== false;
  function reset() {
    generation++;
    rows = []; detail = source = null;
    opened = dialogOpen = loaded = busy = false;
    error = notice = ""; scope = "unknown"; links = {}; operation = null;
  }
  async function directory(current) {
    const credentials = await api("/api/sources/credentials");
    if (!current()) return [];
    if (!credentials.source_account_id) throw new Error("请先同步教学网，再查看作业关联。");
    let cursor = null, result = [];
    do {
      const page = await api(API + "?source_account_id=" + encodeURIComponent(credentials.source_account_id) + (cursor ? "&cursor=" + encodeURIComponent(cursor) : ""));
      if (!current()) return [];
      result.push(...page.records); cursor = page.next_cursor;
    } while (cursor);
    return result;
  }
  async function read(id, current) {
    const [next, original] = await Promise.all([api(API + "/" + encodeURIComponent(id)), api(API + "/" + encodeURIComponent(id) + "/source")]);
    if (!current()) return;
    detail = next; source = original; scope = next.observation.scope;
    links = Object.fromEntries(next.observation.actions.map(action => [action.action_id, action.relation]));
    operation = null;
  }
  const completeSource = () => source?.records?.length && source.records.every(record => record.text_quality === "complete" &&
    (record.upstream_hints?.references || []).every(reference => reference.read_state === "complete"));
  function sourceMarkup() {
    return `<details class="source-disclosure submission-source"><summary>查看原文与附件</summary>${(source?.records || []).map(record => {
      let url = null;
      try { const parsed = new URL(record.source_url); if (["http:", "https:"].includes(parsed.protocol) && !parsed.username && !parsed.password) url = parsed.href; } catch {}
      return `<p>${esc(record.text || "暂未取得正文")}</p>${(record.upstream_hints?.references || []).map(reference => `<h4>${esc(reference.label || "附件")} · ${esc(readStates[reference.read_state] || "读取待确认")}</h4><p>${esc(reference.text || "暂未取得内容")}</p>`).join("")}${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看原网页</a>` : ""}`;
    }).join("")}</details>`;
  }
  function showDialog(force = false) {
    if (!dialogOpen || !force && !ownsDialog()) return;
    const observation = detail?.observation;
    const labels = Object.fromEntries((detail?.action_labels || []).map(action => [action.action_id, action.label]));
    host.openDialog(`<section class="submission-dialog" data-submission-dialog>
      <header class="dialog-header"><h2>作业提交关联</h2><button type="button" class="icon-button" data-submission-action="close" aria-label="关闭作业关联">${host.icon?.("close") || "×"}</button></header>
      ${observation ? `<p class="submission-name">${esc(detail.label)}</p><p class="status-note">${esc([detail.course_or_project, states[observation.snapshot.state]].filter(Boolean).join(" · "))}</p>
      ${sourceMarkup()}<p class="source-intro">作业提交后，只自动完成下方标记为“随作业提交”的任务。</p>
      ${observation.review_pending ? `<div class="submission-review"><p>提交状态发生过变化，请先核对原文。</p><button class="btn quiet" data-submission-action="resolve_source_review" ${busy ? "disabled" : ""}>已核对当前状态</button></div>` : ""}
      <div class="submission-actions">${observation.actions.map(action => `<article class="submission-action"><strong>${esc(labels[action.action_id] || "待核对任务")}</strong><p class="status-note">${esc(decisions[action.decision] || "待确认")}${action.source_completion_paused ? " · 已暂停自动完成" : ""}</p><label class="field">提交关系<select data-submission-field="${esc(action.action_id)}" aria-label="${esc(labels[action.action_id] || "任务")}的提交关系" ${busy ? "disabled" : ""}>${[["unknown", "尚未确认"], ["assignment_submission", "随作业提交"], ["other", "独立任务"]].map(([value, label]) => `<option value="${value}" ${links[action.action_id] === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>${action.kind === "task" && action.relation === "assignment_submission" && !["deleted", "ignored"].includes(action.decision) ? `<button class="btn quiet" data-submission-action="${action.source_completion_paused ? "resume_source_completion" : "reopen_actions"}" data-action-id="${esc(action.action_id)}" ${busy ? "disabled" : ""}>${action.source_completion_paused ? "恢复自动完成" : "重新打开任务"}</button>` : ""}</article>`).join("")}</div>
      <details class="source-disclosure submission-scope"><summary>整份通知的范围</summary><label class="field">通知包含<select data-submission-field="scope" ${busy ? "disabled" : ""}>${[["unknown", "尚未确认"], ["submission_only", "全部任务都随作业提交"], ["mixed", "还包含其他独立任务"]].map(([value, label]) => `<option value="${value}" ${scope === value ? "selected" : ""} ${value === "submission_only" && !completeSource() ? "disabled" : ""}>${label}</option>`).join("")}</select></label>${!completeSource() ? '<p class="status-note">原文或附件尚未读全，可先逐项关联任务。</p>' : ""}</details>` : error ? "" : '<p class="status-note" role="status">正在读取作业…</p>'}
      ${error ? `<p class="error" role="alert">${esc(error)}</p>` : ""}${notice ? `<p class="status-note" role="status">${esc(notice)}</p>` : ""}
      <div class="dialog-actions">${detail ? `<button class="btn primary" data-submission-action="associate_actions" ${busy ? "disabled" : ""}>${busy ? "正在保存…" : "保存关联"}</button>` : ""}<button class="btn quiet" data-submission-action="close">关闭</button></div>
    </section>`);
  }
  async function click(button) {
    const action = button.dataset.submissionAction;
    if (!action) return false;
    if (action === "close") { dialogOpen = false; host.closeDialog(); return true; }
    if (busy || !session.user || session.changing || host.isDemo()) return true;
    if (action !== "load" && action !== "detail" && (!detail || !ownsDialog())) return true;
    const value = stamp(), current = () => valid(value);
    busy = true; error = notice = ""; opened = true;
    if (action === "detail") { detail = source = null; dialogOpen = true; showDialog(true); }
    else showDialog();
    host.render();
    let saved = false;
    try {
      await flushStore(); if (!current()) return true;
      if (action === "load") {
        const next = await directory(current);
        if (current()) { rows = next; loaded = true; }
      } else if (action === "detail") await read(button.dataset.record, current);
      else {
        const observation = detail.observation;
        const body = { contract_version: VERSION, record_id: detail.record_id,
          expected_snapshot_id: observation.snapshot.snapshot_id, expected_control_revision: observation.control_revision, kind: action };
        if (action === "associate_actions") Object.assign(body, { scope, links: observation.actions.map(item => ({ action_id: item.action_id, relation: links[item.action_id] })) });
        else body.action_ids = action === "resolve_source_review" ? [] : [button.dataset.actionId];
        const signature = JSON.stringify(body);
        if (operation?.signature !== signature) operation = { signature, id: newId() };
        body.operation_id = operation.id;
        await api(API + "/" + encodeURIComponent(detail.record_id) + "/operations", body);
        if (!current()) return true;
        saved = true; operation = null;
        await host.refresh(); if (!current()) return true;
        await read(detail.record_id, current);
        if (current()) notice = action === "reopen_actions" ? "任务已重新打开，并暂停自动完成。" : "关联已保存，下次同步时生效。";
      }
    } catch (cause) {
      if (current()) error = saved ? "关联已保存，但列表暂未更新，请重新打开查看。" : cause.message;
    } finally {
      if (current()) { busy = false; host.render(); showDialog(); }
    }
    return true;
  }
  function input(event) {
    const key = event.target.dataset?.submissionField;
    if (!key) return false;
    if (busy || !ownsDialog()) return true;
    if (key === "scope") scope = event.target.value; else links[key] = event.target.value;
    operation = null; return true;
  }
  function toggle(event) {
    const target = event.target;
    if (target?.dataset?.submissionDirectory === undefined || target.isConnected === false) return false;
    opened = target.open;
    if (opened && !loaded && !busy && !error) void click({ dataset: { submissionAction: "load" } });
    return true;
  }
  function render() {
    return `<details class="import-history submission-directory" data-submission-directory ${opened ? "open" : ""}><summary>作业与提交状态</summary>
      <div class="submission-directory-heading"><p class="status-note">关联作业与任务，提交后自动更新完成状态。</p><button class="btn quiet" data-submission-action="load" ${busy ? "disabled" : ""}>刷新</button></div>
      ${error && !ownsDialog() ? `<p class="error" role="alert">${esc(error)}</p>` : ""}
      ${loaded && !rows.length ? '<p class="source-empty">同步教学网后，可在这里查看作业。</p>' : ""}
      <div class="submission-records">${rows.map(record => `<button class="submission-record" data-submission-action="detail" data-record="${esc(record.record_id)}" ${busy ? "disabled" : ""}><span><strong>${esc(record.label)}</strong><small>${esc(record.course_or_project || "教学网")}</small></span><span class="status-note">${host.icon?.("chevron") || "查看"}</span></button>`).join("")}</div>
    </details>`;
  }
  return { render, click, input, toggle, reset };
}
