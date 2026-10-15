import { api, session, accountVersion } from "./team-store.js";

const API = "/api/sources/sync-schedule";
const VERSION = "source-sync/0.1";
const INTERVALS = [
  [15, "每 15 分钟"], [30, "每 30 分钟"], [60, "每小时"],
  [180, "每 3 小时"], [360, "每 6 小时"], [720, "每 12 小时"],
  [1440, "每天"], [10080, "每周"],
];

export function scheduleIntervalLabel(minutes) {
  return INTERVALS.find(([value]) => value === minutes)?.[1] || `每 ${minutes} 分钟`;
}

function checkSettings(value) {
  if (value?.contract_version !== VERSION || typeof value.enabled !== "boolean" ||
      typeof value.available !== "boolean" || !Number.isInteger(value.revision) ||
      !Number.isInteger(value.interval_minutes) || value.interval_minutes < 15 || value.interval_minutes > 10080) {
    throw new Error("暂时无法读取自动同步设置，请稍后重试。");
  }
  return value;
}

export function createSyncScheduleUI(host) {
  const esc = host.escapeHtml;
  let state = null, draft = null, loading = false, saving = false, opened = false;
  let requested = false, generation = 0, error = "", readPromise = null;
  const stamp = () => [generation, accountVersion()];
  const current = value => value[0] === generation && value[1] === accountVersion() &&
    session.user && !session.changing && !host.isDemo();
  const ownsDialog = () => opened && host.isDialogOpen?.('[data-import-form="schedule"]') !== false;
  const makeDraft = value => ({
    enabled: value.enabled,
    interval: INTERVALS.some(([minutes]) => minutes === value.interval_minutes) ? String(value.interval_minutes) : "custom",
    minutes: String(value.interval_minutes),
    consent: false,
  });

  function reset() {
    generation++;
    state = draft = readPromise = null;
    loading = saving = opened = requested = false;
    error = "";
  }

  function showDialog(force = false) {
    if (!opened || !force && !ownsDialog()) return;
    const disabled = loading || saving, unavailable = !state?.available;
    const enabled = Boolean(draft?.enabled);
    const fieldsDisabled = disabled || unavailable || !enabled;
    host.openDialog(`<form data-import-form="schedule" class="schedule-form">
      <header class="dialog-header"><h2>自动同步</h2><button type="button" class="icon-button" data-sync-schedule-action="close" aria-label="关闭自动同步设置">${host.icon("close")}</button></header>
      <p class="schedule-description">定期获取教学网作业与通知，识别后加入任务。</p>
      ${draft ? `<label class="schedule-enable"><span>开启自动同步</span><input type="checkbox" role="switch" name="enabled" data-sync-schedule-field="enabled" ${enabled ? "checked" : ""} ${disabled || unavailable && !enabled ? "disabled" : ""}></label>
      ${unavailable ? '<p class="status-note">自动同步暂未开放，你仍可手动同步。</p>' : ""}
      ${state.error ? `<p class="error" role="alert">${esc(state.error.message)}</p>` : ""}
      <label class="field schedule-frequency">同步频率<select name="interval" data-sync-schedule-field="interval" data-schedule-setting ${fieldsDisabled ? "disabled" : ""}>${INTERVALS.map(([minutes, label]) => `<option value="${minutes}" ${draft.interval === String(minutes) ? "selected" : ""}>${label}</option>`).join("")}<option value="custom" ${draft.interval === "custom" ? "selected" : ""}>自定义间隔</option></select></label>
      <label class="field schedule-custom" data-schedule-custom ${draft.interval === "custom" ? "" : "hidden"}>间隔（分钟）<input type="number" name="minutes" min="15" max="10080" step="1" value="${esc(draft.minutes)}" data-sync-schedule-field="minutes" data-schedule-setting ${fieldsDisabled || draft.interval !== "custom" ? "disabled" : ""}><span class="field-hint">15 分钟至 7 天</span></label>
      <div data-schedule-consent ${enabled ? "" : "hidden"}><label class="field check-field"><input type="checkbox" name="consent" data-sync-schedule-field="consent" data-schedule-setting ${draft.consent ? "checked" : ""} ${fieldsDisabled ? "disabled" : ""}>允许定期获取教学网内容（含附件），并用于任务识别。</label><p class="schedule-hint">关闭网页后也会继续同步。首次同步将在所选间隔后开始。</p></div>` : error ? "" : '<p class="status-note" role="status">正在读取设置…</p>'}
      <p class="error" role="alert" data-schedule-error ${error ? "" : "hidden"}>${esc(error)}</p>
      <div class="dialog-actions">${draft ? `<button class="btn primary" type="submit" ${disabled ? "disabled" : ""}>${saving ? "正在保存…" : "保存"}</button>` : ""}${error ? `<button class="btn quiet" type="button" data-sync-schedule-action="reload" ${disabled ? "disabled" : ""}>重新读取</button>` : ""}<button class="btn quiet" type="button" data-sync-schedule-action="close">取消</button></div>
    </form>`);
  }

  function load() {
    if (loading) return readPromise;
    if (!session.user || session.changing || host.isDemo() || saving) return Promise.resolve();
    requested = true;
    const value = stamp();
    loading = true;
    error = "";
    readPromise = (async () => {
      try {
        const next = checkSettings(await api(API));
        if (!current(value)) return;
        state = next;
        if (ownsDialog()) draft = makeDraft(next);
      } catch (cause) {
        if (current(value)) error = cause.message || "暂时无法读取设置，请稍后重试。";
      } finally {
        if (current(value)) {
          loading = false;
          readPromise = null;
          host.render();
          showDialog();
        }
      }
    })();
    return readPromise;
  }

  async function click(button) {
    const action = button.dataset.syncScheduleAction;
    if (!action) return false;
    if (action === "close") {
      opened = false;
      host.closeDialog();
      return true;
    }
    if (!session.user || session.changing || host.isDemo()) return true;
    if (action === "open") {
      if (saving) { host.toast("正在保存设置，请稍候"); return true; }
      if (!host.isConnected()) { host.toast("请先连接教学网"); return true; }
      opened = true;
      draft = null;
      error = "";
      showDialog(true);
      await load();
    } else if (action === "reload" && ownsDialog()) {
      draft = null;
      showDialog();
      await load();
    }
    return true;
  }

  function input(event) {
    const target = event.target, key = target.dataset?.syncScheduleField;
    if (!key) return false;
    if (!draft || loading || saving || !ownsDialog()) return true;
    draft[key] = ["enabled", "consent"].includes(key) ? target.checked : target.value;
    // Keep the focused control and any custom interval in place while editing.
    const form = target.closest?.("form");
    if (form) {
      const blocked = !draft.enabled || !state.available;
      form.querySelectorAll("[data-schedule-setting]").forEach(field => {
        field.disabled = blocked || field.name === "minutes" && draft.interval !== "custom";
      });
      form.querySelector("[data-schedule-custom]").hidden = draft.interval !== "custom";
      form.querySelector("[data-schedule-consent]").hidden = !draft.enabled;
      form.querySelector("[data-schedule-error]").hidden = true;
    }
    error = "";
    return true;
  }

  async function submit(form) {
    if (form.dataset.importForm !== "schedule") return false;
    if (!draft || !state || loading || saving || !ownsDialog() || !current(stamp())) return true;
    let minutes = Number(draft.interval === "custom" ? draft.minutes : draft.interval);
    if (!draft.enabled && (!Number.isInteger(minutes) || minutes < 15 || minutes > 10080)) minutes = state.interval_minutes;
    if (!Number.isInteger(minutes) || minutes < 15 || minutes > 10080) error = "请输入 15～10080 之间的整数分钟。";
    else if (draft.enabled && !host.isConnected()) error = "请先连接教学网。";
    else if (draft.enabled && !draft.consent) error = "请先同意定期获取并识别教学网内容。";
    if (error) { showDialog(); return true; }
    const value = stamp();
    const body = { contract_version: VERSION, revision: state.revision, enabled: draft.enabled,
      interval_minutes: minutes, sources: ["teaching_web"], consent: draft.enabled && draft.consent };
    saving = true;
    showDialog();
    try {
      const next = checkSettings(await api(API, body, "PUT"));
      if (!current(value)) return true;
      state = next;
      if (ownsDialog()) {
        opened = false;
        host.closeDialog();
      }
      host.toast(next.enabled ? "自动同步已开启" : "自动同步已关闭");
    } catch (cause) {
      if (current(value)) error = cause.status === 409
        ? "设置已发生变化，请重新读取后再保存。" : cause.message || "保存未完成，请稍后重试。";
    } finally {
      if (current(value)) {
        saving = false;
        host.render();
        if (error) showDialog();
      }
    }
    return true;
  }

  function render() {
    if (!session.user || host.isDemo()) return "";
    if (!requested) {
      requested = true;
      const value = stamp();
      queueMicrotask(() => { if (current(value)) void load(); });
    }
    const connected = host.isConnected();
    const label = !connected ? "连接后可开启" : !state ? error ? "暂时无法读取" : "正在读取…"
      : !state.available ? "暂不可用" : state.error ? "已暂停"
      : state.enabled ? scheduleIntervalLabel(state.interval_minutes) : "未开启";
    const nextRun = state?.enabled && state.available && state.next_run_at && connected
      ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(state.next_run_at * 1000)) : "";
    return `<div class="teaching-schedule"><div class="teaching-schedule-copy"><span class="teaching-schedule-label">自动同步</span><span class="status-note" role="status">${esc(label)}${nextRun ? `<span class="schedule-next">下次 ${esc(nextRun)}</span>` : ""}</span></div><button type="button" class="btn quiet" data-sync-schedule-action="open" aria-label="设置自动同步" ${!connected || saving ? "disabled" : ""}>设置</button></div>`;
  }

  return { render, click, input, submit, load, reset };
}
