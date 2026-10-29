import dialogues from "./assets/companions/dialogue-bank.json" with { type: "json" };

export const COMPANION_STORAGE_KEY = "niuma.completion-companion.v1";
const defaults = { enabled: true, animated: true };
function browserStorage() {
  try { return globalThis.localStorage; } catch { return null; }
}

export function readCompanionPreferences(storage = browserStorage()) {
  try {
    const value = JSON.parse(storage?.getItem(COMPANION_STORAGE_KEY) || "{}");
    return { enabled: value?.enabled !== false, animated: value?.animated !== false };
  } catch { return { ...defaults }; }
}

export function writeCompanionPreferences(value, storage = browserStorage()) {
  const preferences = { enabled: value?.enabled !== false, animated: value?.animated !== false };
  try { storage?.setItem(COMPANION_STORAGE_KEY, JSON.stringify(preferences)); } catch {}
  return preferences;
}

export function countRemainingTasks(tasks = []) {
  return tasks.filter(task => task && !task.recycleBin && task.content_type !== "notice" &&
    !["done", "completed", "cancelled"].includes(task.status)).length;
}

export function selectCompanionMessage({ character = "cow", count = 1, remaining = 1, index = 0 } = {}) {
  character = character === "horse" ? "horse" : "cow";
  count = Number.isFinite(count) ? Math.max(1, Math.trunc(count)) : 1;
  const category = remaining === 0 ? "allDone" : count > 1 ? "batch" : "ordinary";
  const bank = dialogues[character][category];
  const position = Number.isFinite(index) ? ((Math.trunc(index) % bank.length) + bank.length) % bank.length : 0;
  const selected = bank[position];
  return {
    character, count, id: selected.id,
    mode: category === "allDone" ? "all-done" : category,
    title: category === "allDone" ? "清单完成啦" : count > 1 ? `已完成 ${count} 项任务` : "任务已完成",
    line: selected.line,
  };
}

// Completion scheduling is separate from the persistent pet and the undo control.
export function createCompletionCompanion({ accountVersion = () => 0,
  canShow = () => true, onShow = () => {}, onDismiss = () => {},
  onPreferences = () => {} } = {}) {
  const doc = document;
  let preferences = readCompanionPreferences(), active = null, turn = 0, timer;
  let remainingTime = 7000, deadline = 0;
  const indices = new Map();
  function stop() { clearTimeout(timer); }
  function paused() {
    const pet = doc.querySelector('#sidebar-companion');
    return doc.visibilityState === 'hidden' || Boolean(pet &&
      (pet.matches?.(':hover') || (doc.activeElement && pet.contains?.(doc.activeElement))));
  }
  function pause() {
    if (deadline) remainingTime = Math.max(0, deadline - Date.now());
    deadline = 0; stop();
  }
  function resume() {
    if (deadline) remainingTime = Math.max(0, deadline - Date.now());
    deadline = 0;
    stop();
    if (!active || paused()) return;
    deadline = Date.now() + remainingTime;
    timer = setTimeout(dismiss, remainingTime);
  }
  function dismiss() { stop(); deadline = 0; active = null; onDismiss(); }
  function reset() { dismiss(); turn = 0; indices.clear(); }
  function show({ remaining = 1, epoch = accountVersion() } = {}) {
    if (!preferences.enabled || !canShow() || epoch !== accountVersion()) return false;
    const now = Date.now();
    const merged = active && active.epoch === epoch && now - active.lastCompletion <= 2000;
    const character = merged ? active.character : turn++ % 2 ? 'horse' : 'cow';
    const count = merged ? active.count + 1 : 1;
    const category = remaining === 0 ? 'all-done' : count > 1 ? 'batch' : 'ordinary';
    const key = character + ':' + category;
    const index = indices.get(key) || 0;
    let message = selectCompanionMessage({ character, count, remaining, index });
    if (merged && active.message.mode === category) {
      message = { ...message, id: active.message.id, line: active.message.line };
    } else indices.set(key, index + 1);
    active = { character, count, remaining, epoch, message, lastCompletion: now };
    onShow({ ...active, replay: !merged });
    remainingTime = 7000; deadline = 0; resume();
    return true;
  }
  function syncPreferences(value) {
    preferences = { ...preferences, ...value };
    onPreferences({ ...preferences });
    if (!preferences.enabled) dismiss();
  }
  function preferenceMarkup() {
    return `<section class="companion-preferences" aria-labelledby="companion-preferences-heading"><h3 id="companion-preferences-heading">牛马伙伴</h3><label><input type="checkbox" data-companion-preference="enabled" ${preferences.enabled ? 'checked' : ''}><span>显示牛马伙伴<small>在页面角落陪伴你，完成时会回应</small></span></label><label><input type="checkbox" data-companion-preference="animated" ${preferences.animated ? 'checked' : ''}><span>播放伙伴小动画<small>跟随系统减少动态效果，可保留静态角色与话语</small></span></label></section>`;
  }
  function change(event) {
    const setting = event.target?.dataset?.companionPreference;
    if (!['enabled', 'animated'].includes(setting)) return false;
    const next = writeCompanionPreferences({ ...preferences, [setting]: event.target.checked });
    syncPreferences(next);
    globalThis.window?.dispatchEvent(new CustomEvent('niuma:companion-preferences', { detail: next }));
    return true;
  }
  // Delegation survives app re-renders without moving keyboard focus.
  doc.addEventListener('pointerover', event => { if (event.target?.closest?.('#sidebar-companion')) pause(); });
  doc.addEventListener('pointerout', event => {
    if (event.target?.closest?.('#sidebar-companion') && !event.relatedTarget?.closest?.('#sidebar-companion')) resume();
  });
  doc.addEventListener('focusin', event => { if (event.target?.closest?.('#sidebar-companion')) pause(); });
  doc.addEventListener('focusout', () => queueMicrotask(resume));
  doc.addEventListener('visibilitychange', () => doc.visibilityState === 'hidden' ? pause() : resume());
  globalThis.window?.addEventListener('niuma:companion-preferences', event => syncPreferences(event.detail || {}));
  globalThis.window?.addEventListener('storage', event => {
    if (event.key !== COMPANION_STORAGE_KEY) return;
    syncPreferences(readCompanionPreferences());
    for (const setting of ['enabled', 'animated']) {
      const input = doc.querySelector(`[data-companion-preference="${setting}"]`);
      if (input) input.checked = preferences[setting];
    }
  });
  return { show, dismiss, reset, preferenceMarkup, change };
}
