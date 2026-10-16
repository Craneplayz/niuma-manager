import { api, session, accountVersion } from "./team-store.js";

const API = "/api/sources/sync-schedule";
const VERSION = "source-sync/0.1";
const INTERVALS = [
  [15, "每 15 分钟"], [30, "每 30 分钟"], [60, "每小时"],
  [180, "每 3 小时"], [360, "每 6 小时"], [720, "每 12 小时"],
  [1440, "每天"], [10080, "每周"],
];

function checkSettings(value) {
  if (value?.contract_version !== VERSION || typeof value.enabled !== "boolean" ||
      typeof value.available !== "boolean" || !Number.isInteger(value.revision) ||
      !Number.isInteger(value.interval_minutes) || value.interval_minutes < 15 || value.interval_minutes > 10080) {
    throw new Error("暂时无法读取同步设置，请稍后重试。");
  }
  return value;
}

export function createSyncScheduleUI(host) {
  const esc = host.escapeHtml;
  let state = null, draft = null, reading = null, phase = "";
  let requested = false, generation = 0, error = "", needsReload = false;
  const stamp = () => [generation, accountVersion()];
  const current = value => value[0] === generation && value[1] === accountVersion() &&
    session.user && !session.changing && !host.isDemo();
  const minutes = () => draft?.interval === "once" ? null : Number(draft?.interval === "custom" ? draft.minutes : draft?.interval);
  const unchanged = () => state?.enabled && minutes() === state.interval_minutes;
  const makeDraft = value => ({
    interval: !value.enabled ? "once" : INTERVALS.some(([n]) => n === value.interval_minutes) ? String(value.interval_minutes) : "custom",
    minutes: String(value.interval_minutes), consent: false,
  });

  function reset() {
    generation++;
    state = draft = reading = null;
    phase = error = "";
    requested = needsReload = false;
  }

  function load() {
    if (reading) return reading;
    if (!current(stamp()) || phase) return Promise.resolve(false);
    requested = true;
    const value = stamp();
    reading = (async () => {
      try {
        const next = checkSettings(await api(API));
        if (!current(value)) return false;
        state = next;
        draft ??= makeDraft(next);
        error = "";
        needsReload = false;
        return true;
      } catch (cause) {
        if (current(value)) {
          error = cause.message || "暂时无法读取设置，请稍后重试。";
          needsReload = true;
        }
        return false;
      } finally {
        if (current(value)) { reading = null; host.render(); }
      }
    })();
    return reading;
  }

  async function save(enabled, interval, value) {
    const next = checkSettings(await api(API, {
      contract_version: VERSION, revision: state.revision, enabled,
      interval_minutes: interval, sources: ["teaching_web"], consent: enabled && draft.consent,
    }, "PUT"));
    if (!current(value)) return false;
    state = next;
    draft = enabled ? makeDraft(next) : { ...draft, consent: false };
    return true;
  }

  async function start() {
    if (!current(stamp()) || phase || host.isBusy()) return false;
    const value = stamp();
    if (!state && !await load() || !current(value) || phase || host.isBusy() || needsReload) return false;
    const interval = minutes(), stopping = unchanged();
    error = "";
    if (!host.isConnected()) return false;
    if (interval !== null && (!Number.isInteger(interval) || interval < 15 || interval > 10080)) error = "请输入 15～10080 之间的整数分钟。";
    else if (interval !== null && !stopping && !state.available) error = "当前暂不能定时同步，请选择单次识别。";
    else if (interval !== null && !stopping && !draft.consent) error = "请先同意按所选频率获取并识别教学网内容。";
    if (error) { host.render(); return false; }
    phase = stopping ? "stopping" : "starting";
    host.render();
    try {
      if (stopping || state.enabled || interval !== null) {
        if (!await save(!stopping && interval !== null, interval ?? state.interval_minutes, value)) return false;
      }
      if (stopping) { host.toast("定时同步已停止"); return true; }
      return await host.start();
    } catch (cause) {
      if (current(value)) {
        needsReload = cause.status === 409;
        error = needsReload ? "同步设置已发生变化，请重新读取后再操作。" : cause.message || "启动未完成，请稍后重试。";
      }
      return false;
    } finally {
      if (current(value)) { phase = ""; host.render(); }
    }
  }

  async function stop() {
    if (!current(stamp()) || phase) return false;
    const value = stamp();
    if (reading) await reading;
    if (!state && !await load() || !current(value) || phase || needsReload) return false;
    if (!state.enabled) return true;
    phase = "stopping";
    host.render();
    try {
      return await save(false, state.interval_minutes, value);
    } catch {
      if (current(value)) { error = "定时同步尚未停止，请重新读取后重试。"; needsReload = true; }
      return false;
    } finally {
      if (current(value)) { phase = ""; host.render(); }
    }
  }

  function input(event) {
    const target = event.target, key = target.dataset?.syncScheduleField;
    if (!["interval", "minutes", "consent"].includes(key)) return false;
    if (!draft || reading || phase || host.isBusy()) return true;
    const next = key === "consent" ? target.checked : target.value;
    if (draft[key] === next) return true;
    draft[key] = next;
    if (key !== "consent") draft.consent = false;
    if (!needsReload) error = "";
    host.render();
    return true;
  }

  async function click(button) {
    if (button.dataset.syncScheduleAction !== "reload") return false;
    if (!phase) await load();
    return true;
  }

  function render() {
    const connected = host.isConnected();
    if (current(stamp()) && !requested) {
      requested = true;
      const value = stamp();
      queueMicrotask(() => { if (current(value)) void load(); });
    }
    const disabled = Boolean(phase || reading || host.isBusy());
    const stopping = host.isStopping() || phase === "stopping", canStop = host.canStop();
    const actionDisabled = stopping || Boolean(phase) || !canStop && (disabled || connected && (!state || needsReload));
    const label = stopping ? "正在停止…" : canStop ? state?.enabled ? "停止同步" : "停止导入"
      : !connected ? "连接教学网" : phase === "starting" || host.isRunning() ? "正在启动…"
      : unchanged() ? "停止同步" : minutes() === null || !draft ? "开始识别" : "启动并识别";
    const spinning = Boolean(stopping || phase === "starting" || host.isRunning());
    const progress = spinning ? stopping ? "正在停止导入…" : host.getProgressLabel?.() || "正在启动识别…" : "";
    const stopIcon = canStop || unchanged() || stopping;
    const indicator = spinning || stopIcon ? `<span class="teaching-action-indicator" aria-hidden="true">${spinning ? '<span class="teaching-progress-ring"></span>' : ""}${stopIcon ? '<span class="teaching-stop-icon"></span>' : ""}</span>` : "";
    return `<div class="teaching-controls"><div class="teaching-action-row">
      ${connected ? `<select id="teaching-frequency" aria-label="执行频率" data-sync-schedule-field="interval" ${disabled || !draft ? "disabled" : ""}><option value="once" ${!draft || draft.interval === "once" ? "selected" : ""}>单次</option>${INTERVALS.map(([n, text]) => `<option value="${n}" ${draft?.interval === String(n) ? "selected" : ""} ${state && !state.available ? "disabled" : ""}>${text}</option>`).join("")}<option value="custom" ${draft?.interval === "custom" ? "selected" : ""} ${state && !state.available ? "disabled" : ""}>自定义间隔</option></select>${draft?.interval === "custom" ? `<label class="teaching-custom"><input id="teaching-interval" aria-label="间隔分钟数" type="number" min="15" max="10080" step="1" value="${esc(draft.minutes)}" data-sync-schedule-field="minutes" ${disabled ? "disabled" : ""}>分钟</label>` : ""}` : ""}
      <button class="btn primary" type="button" aria-busy="${spinning}" ${progress ? `aria-describedby="teaching-run-status" title="${esc(progress)}"` : ""} data-import-action="sync" ${actionDisabled ? "disabled" : ""}>${indicator}${label}</button></div>
      ${progress ? `<span id="teaching-run-status" class="sr-only" role="status">${esc(progress)}</span>` : ""}
      ${connected && draft?.interval !== "once" && draft && !unchanged() ? `<label class="teaching-consent"><input id="teaching-consent" type="checkbox" data-sync-schedule-field="consent" ${draft.consent ? "checked" : ""} ${disabled ? "disabled" : ""}><span>允许定期获取教学网内容（含附件）用于识别，关闭网页后仍会运行。</span></label>` : ""}
      ${connected && state && !state.available ? '<p class="status-note">定时同步暂不可用，可进行单次识别。</p>' : ""}
      ${connected && state?.error && !error ? `<p class="status-note">${esc(state.error.message)}</p>` : ""}
      ${error ? `<p class="error" role="alert">${esc(error)}${needsReload ? '<button class="btn quiet" type="button" data-sync-schedule-action="reload">重新读取</button>' : ""}</p>` : ""}
    </div>`;
  }

  return { render, click, input, start, stop, load, reset, isBusy: () => Boolean(phase) };
}
