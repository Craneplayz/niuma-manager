import {
  api,
  session,
  accountVersion,
  refreshStore,
  flushStore,
  dataVersion,
} from "./team-store.js";
import { newId } from "./id.js";

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
  if (values.reviewed !== "on") throw new Error("请确认已核对原文和变更");
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

function friendlyError(error) {
  const message = String(error?.message || error || "操作未完成，请重试");
  if (error?.status === 409) return "内容刚刚发生变化，请刷新后重新核对";
  if (/账号已切换/.test(message)) return "";
  if (/v0\.|契约|协议|JSON|contract|request_id/i.test(message))
    return "返回内容暂时无法读取，请刷新结果或重试";
  if (/crawler|Node|依赖未安装|加密密钥|尚未提供.*接口/i.test(message))
    return "采集服务尚未就绪，请稍后重试";
  return message.replaceAll("DDL", "截止时间").replaceAll("后端", "服务");
}
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

export function createImportUI(host) {
  const esc = host.escapeHtml;
  let source = "text",
    text = "",
    published = shanghaiNow(),
    batches = [],
    credentials = null,
    loaded = false,
    loading = false,
    busy = false,
    error = "",
    notice = "",
    progress = "",
    request = null,
    modal = null,
    controller = null,
    revision = 0;
  const current = () => ({ epoch: accountVersion(), revision });
  const valid = (stamp) =>
    stamp.epoch === accountVersion() && stamp.revision === revision;
  const refresh = () => host.render();
  const demo = () => Boolean(host.isDemo?.());
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
  function reset() {
    revision++;
    controller?.abort();
    controller = null;
    source = "text";
    text = "";
    published = shanghaiNow();
    batches = [];
    credentials = null;
    loaded = false;
    loading = false;
    busy = false;
    error = "";
    notice = "";
    progress = "";
    request = null;
    modal = null;
  }
  globalThis.window?.addEventListener("niuma:account-reset", reset);
  async function refreshWorkspace(stamp) {
    if (host.refresh) await host.refresh();
    else {
      const data = await refreshStore();
      if (valid(stamp)) host.setState?.(data);
    }
  }
  async function load() {
    if (loading || busy || !session.user || demo()) return;
    const stamp = current();
    loading = true;
    error = "";
    refresh();
    const results = await Promise.allSettled([
      api("/api/recognition/batches"),
      api("/api/sources/credentials"),
    ]);
    if (!valid(stamp)) return;
    if (results[0].status === "fulfilled" && Array.isArray(results[0].value))
      batches = results[0].value;
    else error = friendlyError(results[0].reason || "导入结果暂时无法读取");
    if (results[1].status === "fulfilled") credentials = results[1].value;
    else if (!error) error = friendlyError(results[1].reason);
    loading = false;
    loaded = true;
    refresh();
  }
  function deadlineLabel(deadline = {}) {
    if (deadline.kind === "reference") return "参考时间，待核对";
    if (deadline.status === "not_provided") return "无截止日期";
    if (deadline.status === "conflict") return "截止时间有冲突";
    const due = candidateDue(deadline);
    if (!due.date) return "截止日期待确认";
    return (
      due.date.replaceAll("-", " / ") +
      (due.time ? " " + due.time : "") +
      (deadline.status === "inferred" ? " · 待确认" : "")
    );
  }
  function evidence(item) {
    const candidate = item.candidate,
      record = item.batch.request?.records?.find(
        (record) => record.input_id === candidate.input_id,
      );
    const quotes = [
      ...new Set(
        (candidate.evidence || []).map((e) => e.quote).filter(Boolean),
      ),
    ];
    const original = record?.text;
    return `<details class="candidate-evidence"><summary>查看原文</summary>${quotes.length ? quotes.map((quote) => `<blockquote>${esc(quote)}</blockquote>`).join("") : original ? `<blockquote>${esc(original)}</blockquote>` : '<p class="status-note">原文暂不可用</p>'}${candidate.deadline?.basis ? `<p class="status-note">${esc(candidate.deadline.basis)}</p>` : ""}${(
      candidate.review_reasons || []
    )
      .filter((reason) => reason !== candidate.deadline?.basis)
      .map((reason) => `<p class="status-note">${esc(reason)}</p>`)
      .join("")}</details>`;
  }
  function candidateRow(item) {
    const c = item.candidate,
      record = item.batch.request?.records?.find(
        (record) => record.input_id === c.input_id,
      ),
      label = platforms[record?.platform] || c.sources?.[0]?.label || "通知";
    return `<article class="candidate-row"><div class="candidate-copy"><p class="status-note">${esc(actions[c.notice_type] || "待确认")} · ${esc(deadlineLabel(c.deadline))}</p><h3>${esc(c.title)}</h3><p class="status-note">${esc([c.course_or_project, label].filter(Boolean).join(" · "))}</p>${item.state === "pending" ? evidence(item) : ""}</div><div class="candidate-actions">${item.state === "pending" ? `<button class="btn" type="button" data-import-review="${esc(item.id)}" ${busy ? "disabled" : ""}>核对${c.notice_type === "create" ? "并加入" : ""}</button><button class="btn quiet" type="button" data-import-ignore="${esc(item.id)}" ${busy ? "disabled" : ""}>忽略</button>` : item.state === "ignored" ? `<button class="btn quiet" type="button" data-import-reopen="${esc(item.id)}" ${busy ? "disabled" : ""}>撤回忽略</button>` : '<span class="status-note">已处理</span>'}</div></article>`;
  }
  function render() {
    if (session.user && !demo() && !loaded && !loading && !busy)
      queueMicrotask(load);
    const items = allItems(),
      pending = items.filter((item) => item.state === "pending"),
      ignored = items.filter((item) => item.state === "ignored"),
      applied = items.filter((item) => item.state === "applied");
    const failures = batches.filter((batch) =>
      ["failed", "partial_success"].includes(batch.result?.status),
    );
    return `<header class="page-heading"><h1>导入</h1></header><div class="import-source-tabs" role="group" aria-label="导入来源"><button type="button" class="btn ${source === "text" ? "selected" : "quiet"}" data-import-source="text" aria-pressed="${source === "text"}" ${busy ? "disabled" : ""}>文本</button><button type="button" class="btn ${source === "teaching" ? "selected" : "quiet"}" data-import-source="teaching" aria-pressed="${source === "teaching"}" ${busy ? "disabled" : ""}>教学网</button><button type="button" class="btn quiet" disabled title="邮箱尚未接入">邮箱 <span class="status-note">尚未接入</span></button></div><section class="import-input">${source === "text" ? `<form data-import-form="text"><label class="field" for="import-text">通知内容<textarea id="import-text" name="text" rows="6" maxlength="4000" placeholder="粘贴通知、邮件正文或任务内容" ${busy ? "disabled" : ""}>${esc(text)}</textarea></label><details class="import-time"><summary>通知时间</summary><label class="field" for="import-published">发布时间<input id="import-published" name="published" type="datetime-local" step="1" value="${esc(published)}" ${busy ? "disabled" : ""}></label><p class="status-note">按此时间理解“今天”“明天”。</p></details><div class="import-form-actions"><button class="btn primary" type="submit" ${busy ? "disabled" : ""}>${busy && source === "text" ? "正在识别…" : "识别任务"}</button></div></form>` : `<div class="teaching-import"><div><h2>教学网作业</h2><p class="status-note">${credentials?.configured ? "已连接 " + esc(credentials.username) : loading ? "正在读取连接…" : "连接账号后采集作业与通知"}</p></div><div class="import-form-actions"><button class="btn primary" type="button" data-import-action="sync" ${busy || loading ? "disabled" : ""}>${busy ? "正在采集…" : credentials?.configured ? "采集并识别" : "连接教学网"}</button>${credentials?.configured ? `<button class="btn quiet" type="button" data-import-action="credentials" ${busy ? "disabled" : ""}>管理连接</button>` : ""}</div></div>`}${busy ? `<div class="import-progress" role="status"><span class="status-note">${esc(progress || "正在处理…")}</span><button class="btn quiet" type="button" data-import-action="stop">停止等待</button></div>` : ""}${error ? `<p class="error" role="alert">${esc(error)}</p>` : ""}${notice && !busy ? `<p class="status-note" role="status">${esc(notice)}</p>` : ""}</section><section class="import-results"><div class="section-heading"><h2>待核对${pending.length ? ` <span class="status-note">${pending.length}</span>` : ""}</h2><button class="btn quiet" type="button" data-import-action="refresh" ${busy || loading ? "disabled" : ""}>${loading ? "读取中…" : "刷新"}</button></div>${pending.length ? pending.map(candidateRow).join("") : `<p class="empty-state">${loading ? "正在读取导入结果…" : "暂无待核对任务"}</p>`}${failures.map((batch) => `<div class="import-failure"><p class="status-note">${incomplete(batch) ? "有通知未完整取得，请核对来源内容后重新采集" : "部分通知识别失败"}</p>${!incomplete(batch) ? `<button class="btn quiet" type="button" data-import-retry="${esc(batch.request.request_id)}" ${busy ? "disabled" : ""}>重试识别</button>` : ""}</div>`).join("")}${ignored.length ? `<details class="import-history"><summary>已忽略 · ${ignored.length}</summary>${ignored.map(candidateRow).join("")}</details>` : ""}${applied.length ? `<details class="import-history"><summary>已处理 · ${applied.length}</summary>${applied.map(candidateRow).join("")}</details>` : ""}</section>`;
  }
  function dialogHeader(title) {
    return `<header class="dialog-header"><h2>${esc(title)}</h2><button class="icon-button" type="button" data-import-action="close" aria-label="关闭">${host.icon("close")}</button></header>`;
  }
  function openCredentials() {
    modal = { kind: "credentials" };
    host.openDialog(
      `<form data-import-form="credentials">${dialogHeader("连接教学网")}<label class="field">教学网账号<input name="username" required maxlength="200" autocomplete="username" value="${esc(credentials?.username || "")}"></label><label class="field">教学网密码<input name="password" type="password" required maxlength="200" autocomplete="current-password"></label><p class="error" data-import-error role="alert" hidden></p><div class="dialog-actions"><button class="btn primary" type="submit">保存连接</button>${credentials?.configured ? '<button class="btn quiet" type="button" data-import-action="remove-credentials">移除连接</button>' : ""}</div></form>`,
    );
  }
  function openReview(item, values) {
    if (!item || item.state !== "pending") return;
    const due = candidateDue(item.candidate.deadline);
    modal = {
      kind: "review",
      id: item.id,
      version: item.version,
      tasksVersion: dataVersion("tasks"),
      values: values || {
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
        reviewed: "",
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
      }${action && action !== "create" ? `<label class="field">目标任务<select name="target_id" required data-import-field="target"><option value="">选择已有任务</option>${tasks.map((task) => `<option value="${esc(task.id)}" ${task.id === v.target_id ? "selected" : ""}>${esc(task.title)}</option>`).join("")}</select></label>${target ? `<p class="status-note">当前任务：${esc(target.title)}${target.due ? " · " + esc(target.due) + (target.dueTime ? " " + esc(target.dueTime) : "") : ""}</p>` : ""}` : ""}${["create", "update"].includes(action) ? `<label class="field">任务名称<input name="title" required maxlength="180" value="${esc(v.title)}"></label><label class="field">课程<input name="course" maxlength="200" value="${esc(v.course)}" placeholder="可不填写"></label><label class="field">说明<textarea name="description" rows="3">${esc(v.description)}</textarea></label>` : ""}${action && action !== "cancel" ? `<div class="form-grid"><label class="field">截止日期<input name="date" type="date" value="${esc(v.date)}" ${action === "postpone" ? "required" : ""}></label><label class="field">截止时刻<input name="time" type="time" value="${esc(v.time)}"></label></div><p class="status-note">${exact ? "原文时间包含秒，请确认要保存的时刻。" : "原文只有日期时，时刻可以留空。"}</p>` : ""}${action === "cancel" ? '<p class="status-note">确认后将取消所选任务，原记录保留。</p>' : ""}${evidence(item)}<label class="field check-field"><input name="reviewed" type="checkbox" required ${v.reviewed === "on" ? "checked" : ""}>我已核对原文${action && action !== "create" ? "、目标任务" : ""}和变更</label><p class="error" data-import-error role="alert" hidden></p><div class="dialog-actions"><button class="btn primary" type="submit" ${!action ? "disabled" : ""}>${action === "create" ? "确认加入任务" : action === "cancel" ? "确认取消任务" : "确认保存"}</button><button class="btn quiet" type="button" data-import-action="refresh-review" hidden>刷新后重新核对</button></div></form>`,
    );
  }
  function formError(form, message, conflict = false) {
    const node = form.querySelector("[data-import-error]");
    if (node) {
      node.textContent = message;
      node.hidden = !message;
    }
    const retry = form.querySelector('[data-import-action="refresh-review"]');
    if (retry) retry.hidden = !conflict;
  }
  function lockForm(form, lock) {
    form.querySelectorAll("input,textarea,select,button").forEach((field) => {
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
      body,
      "POST",
      { signal: controller.signal, timeoutMs: 280000 },
    );
    if (!Array.isArray(next)) throw new Error("导入结果暂时无法读取");
    return next;
  }
  async function importText(values) {
    if (busy || !allowed()) return;
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
      batches = next;
      loaded = true;
      const result = next.find(
        (batch) => batch.request.request_id === request.body.request_id,
      )?.result;
      if (["failed", "partial_success"].includes(result?.status))
        error = "部分通知识别失败，可在下方重试";
      else
        notice =
          result?.status === "no_tasks"
            ? "未找到需要处理的任务"
            : "识别完成，请核对下方任务";
    } catch (e) {
      if (valid(stamp)) {
        if (controller?.signal.aborted)
          notice = "已停止等待，可刷新查看处理结果";
        else error = friendlyError(e);
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
    if (busy || !allowed()) return;
    if (!credentials?.configured) {
      openCredentials();
      return;
    }
    const stamp = current();
    busy = true;
    error = "";
    notice = "";
    progress = "正在采集教学网…";
    controller = new AbortController();
    refresh();
    try {
      const result = await api(
        "/api/sources/sync",
        { sources: ["teaching_web"] },
        "POST",
        { signal: controller.signal, timeoutMs: 310000 },
      );
      if (!valid(stamp)) return;
      if (!Array.isArray(result.requests))
        throw new Error("采集结果暂时无法读取");
      let failures = 0;
      for (let index = 0; index < result.requests.length; index++) {
        if (!valid(stamp) || controller.signal.aborted) break;
        progress = `正在识别通知 ${index + 1} / ${result.requests.length}`;
        refresh();
        try {
          const next = await recognize(result.requests[index]);
          if (!valid(stamp)) return;
          batches = next;
          loaded = true;
          const batch = next.find(
            (batch) =>
              batch.request.request_id === result.requests[index].request_id,
          );
          if (["failed", "partial_success"].includes(batch?.result?.status))
            failures++;
        } catch (e) {
          if (!valid(stamp)) return;
          if (controller.signal.aborted) throw e;
          failures++;
          error = friendlyError(e);
        }
      }
      if (!valid(stamp)) return;
      if (controller.signal.aborted) {
        notice = "已停止等待，可刷新查看处理结果";
        return;
      }
      if (!result.requests.length) {
        const next = await api("/api/recognition/batches");
        if (!valid(stamp)) return;
        batches = next;
        loaded = true;
      }
      notice = failures
        ? "采集完成，部分通知尚未识别成功"
        : "采集完成" +
          (result.requests.length ? "，请核对下方任务" : "，暂无新增通知");
    } catch (e) {
      if (valid(stamp)) {
        if (controller?.signal.aborted)
          notice = "已停止等待，可刷新查看处理结果";
        else error = friendlyError(e);
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
  async function resolveSmall(item, action) {
    if (
      busy ||
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
        batches = result.batches;
        host.toast(action === "ignore" ? "已忽略" : "已恢复待核对");
      }
    } catch (e) {
      if (valid(stamp)) error = friendlyError(e);
    } finally {
      if (valid(stamp)) {
        busy = false;
        refresh();
      }
    }
  }
  async function retryBatch(batch) {
    if (busy || !allowed() || !batch || incomplete(batch)) return;
    const stamp = current();
    busy = true;
    error = "";
    notice = "";
    progress = "正在重试识别…";
    controller = new AbortController();
    refresh();
    try {
      const next = await recognize(batch.request, true);
      if (valid(stamp)) {
        batches = next;
        notice = "识别结果已更新";
      }
    } catch (e) {
      if (valid(stamp))
        error = controller?.signal.aborted
          ? "已停止等待，可刷新查看结果"
          : friendlyError(e);
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
    if (d.importSource) {
      if (!busy) {
        source = d.importSource;
        error = "";
        notice = "";
        refresh();
      }
      return true;
    }
    if (d.importReview) {
      if (!busy && allowed()) openReview(itemById(d.importReview));
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
      if (allowed()) await load();
      return true;
    }
    if (action === "credentials") {
      if (!busy && allowed()) openCredentials();
      return true;
    }
    if (action === "sync") {
      await sync();
      return true;
    }
    if (action === "remove-credentials") {
      if (busy || !allowed()) return true;
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
          host.closeDialog();
          host.toast("教学网连接已移除");
        }
      } catch (e) {
        if (valid(stamp)) {
          if (form) formError(form, friendlyError(e));
          else error = friendlyError(e);
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
      if (busy || !allowed()) return true;
      const id = modal?.id,
        stamp = current(),
        form = button.closest?.("form");
      try {
        await refreshWorkspace(stamp);
        if (!valid(stamp)) return true;
        await load();
        if (valid(stamp)) {
          const item = itemById(id);
          if (item?.state === "pending") openReview(item);
          else {
            modal = null;
            host.closeDialog();
            host.toast("这项通知已处理");
          }
        }
      } catch (e) {
        if (valid(stamp)) {
          if (form) formError(form, friendlyError(e), true);
          else error = friendlyError(e);
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
    if (busy || !allowed()) return true;
    const values = Object.fromEntries(new FormData(form));
    if (kind === "text") {
      await importText(values);
      return true;
    }
    const stamp = current();
    if (kind === "credentials") {
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
          host.closeDialog();
          host.toast("教学网已连接");
        }
      } catch (e) {
        if (valid(stamp)) formError(form, friendlyError(e));
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
          dataVersion("tasks") !== modal.tasksVersion ||
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
        batches = result.batches;
        modal = null;
        host.closeDialog();
        try {
          await refreshWorkspace(stamp);
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
        if (valid(stamp)) formError(form, friendlyError(e), e.status === 409);
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
        reviewed: "",
      };
      if (target.dataset.importField === "action") modal.values.target_id = "";
      showReview();
      return true;
    }
    return false;
  }
  return { render, click, submit, input, reset, load };
}
