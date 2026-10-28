import dialogues from "./assets/companions/dialogue-bank.json" with { type: "json" };

export const COMPANION_STORAGE_KEY = "niuma.completion-companion.v1";
const defaults = { enabled: true, animated: true };
const names = { cow: "牛牛", horse: "马马" };
const images = {
  cow: {
    celebrate: new URL("./assets/companions/cow-celebrate.webp", import.meta.url).href,
    wave: new URL("./assets/companions/cow-wave.webp", import.meta.url).href,
    settle: new URL("./assets/companions/cow-settle.webp", import.meta.url).href,
  },
  horse: {
    celebrate: new URL("./assets/companions/horse-celebrate.webp", import.meta.url).href,
    wave: new URL("./assets/companions/horse-wave.webp", import.meta.url).href,
    settle: new URL("./assets/companions/horse-settle.webp", import.meta.url).href,
  },
};

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

// One reusable feedback surface; timers never queue celebrations behind later work.
export function createCompletionCompanion({ escapeHtml, icon, accountVersion = () => 0,
  onError = () => {}, getContainer = () => document.body, canShow = () => true } = {}) {
  const esc = escapeHtml || (value => String(value).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]));
  const closeIcon = icon?.("close") || '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.65"><path d="m6 6 12 12M6 18 18 6"/></svg>';
  const doc = document;
  const root = () => doc.querySelector("#toast");
  const motion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  let preferences = readCompanionPreferences(), active = null, turn = 0, timer;
  let remainingTime = 7000, deadline = 0;
  const indices = new Map();

  function detach() {
    const node = root();
    if (node && node.parentElement && node.parentElement !== doc.body) doc.body.append(node);
  }
  function stopTimer() { clearTimeout(timer); }
  function paused() {
    const panel = doc.querySelector("#completion-companion");
    return doc.visibilityState === "hidden" || Boolean(panel &&
      (panel.matches?.(":hover") || panel.contains?.(doc.activeElement)));
  }
  function armTimer(duration = remainingTime) {
    stopTimer();
    remainingTime = Math.max(0, duration);
    if (!active || paused()) return;
    deadline = Date.now() + remainingTime;
    timer = setTimeout(() => dismiss(), remainingTime);
  }
  function pause() {
    if (deadline) remainingTime = Math.max(0, deadline - Date.now());
    deadline = 0;
    stopTimer();
  }
  function resume() {
    if (active && !paused()) armTimer(Math.max(remainingTime, 2000));
  }
  function dismiss() {
    const node = root();
    stopTimer();
    active = null;
    deadline = 0;
    if (node?.classList.contains?.("companion-toast")) {
      node.classList.remove("visible", "companion-toast");
      node.innerHTML = "";
      node.setAttribute("role", "status");
      node.setAttribute("aria-live", "polite");
      node.removeAttribute("aria-label");
      node.style.removeProperty("--companion-bottom");
      detach();
    }
  }
  function reset() { dismiss(); turn = 0; indices.clear(); }
  function relocate() {
    if (!active) return;
    if (!canShow()) return dismiss();
    const node = root(), container = getContainer() || doc.body;
    if (node?.parentElement !== container) container.append(node);
    node?.style.removeProperty("--companion-bottom");
    const viewportHeight = globalThis.innerHeight;
    const panel = node.getBoundingClientRect();
    const footer = container !== doc.body && container.querySelector(".drawer-footer");
    if (footer) {
      const footerTop = footer.getBoundingClientRect().top;
      const headerBottom = container.querySelector(".drawer-header")?.getBoundingClientRect().bottom || 0;
      const bottom = viewportHeight - footerTop + 12;
      if (footerTop - 12 - panel.height < Math.max(16, headerBottom + 12)) return dismiss();
      node.style.setProperty("--companion-bottom", `${Math.max(24, bottom)}px`);
    }
    // A rating question can coexist without either control being covered.
    const rating = doc.querySelector("#usage-rating:not(.is-closing)");
    if (rating && container === doc.body) {
      const b = rating.getBoundingClientRect();
      if (panel.right > b.left && panel.left < b.right) {
        const bottom = Math.max(24, viewportHeight - b.top + 12);
        if (viewportHeight - bottom - panel.height < 16) return dismiss();
        node.style.setProperty("--companion-bottom", `${bottom}px`);
      }
    }
  }
  function staticPose() {
    const panel = doc.querySelector("#completion-companion");
    if (panel) panel.dataset.animated = String(preferences.animated && !motion?.matches && doc.visibilityState !== "hidden");
  }
  function render(replay) {
    const node = root();
    if (!node || !active) return;
    const { character, count, remaining, undo, epoch, message } = active;
    const animated = preferences.animated && !motion?.matches;
    const pose = remaining === 0 ? "wave" : character === "horse" ? "settle" : "celebrate";
    const oldFigure = !replay && node.querySelector(".companion-figure");
    const focusedAction = !replay && node.contains(doc.activeElement)
      ? doc.activeElement?.hasAttribute("data-companion-undo") ? "[data-companion-undo]"
        : doc.activeElement?.hasAttribute("data-companion-dismiss") ? "[data-companion-dismiss]" : null
      : null;
    node.classList.add("companion-toast", "visible");
    node.removeAttribute("role");
    node.removeAttribute("aria-live");
    node.setAttribute("aria-label", "任务完成反馈");
    node.innerHTML = `<section id="completion-companion" data-character="${character}" data-count="${count}" data-mode="${message.mode}" data-animated="${animated}" aria-label="${names[character]}的完成反馈">
      <div class="companion-figure" aria-hidden="true"><img src="${esc(images[character][pose])}" width="112" height="112" alt="" decoding="async"></div>
      <div class="companion-copy"><div class="companion-message" role="status" aria-live="polite" aria-atomic="true"><strong>${esc(message.title)}</strong><p data-companion-message><span class="sr-only">${names[character]}说：</span>${esc(message.line)}</p></div><div class="companion-actions">${undo ? `<button type="button" id="toast-undo" data-companion-undo>${count > 1 ? "撤销最近一项" : "撤销"}</button>` : ""}<span class="companion-signature" aria-hidden="true">${names[character]}陪你</span></div></div>
      <button type="button" class="companion-dismiss" data-companion-dismiss aria-label="关闭牛马伙伴提示">${closeIcon}</button>
    </section>`;
    const panel = node.querySelector("#completion-companion");
    // Merging updates does not replay the gesture or move focus away from undo.
    if (oldFigure) {
      const oldImage = oldFigure.querySelector("img");
      if (oldImage && oldImage.src !== images[character][pose]) oldImage.src = images[character][pose];
      panel.querySelector(".companion-figure").replaceWith(oldFigure);
    }
    const image = panel.querySelector("img");
    if (image) image.onerror = () => { image.style.visibility = "hidden"; };
    panel.querySelector("[data-companion-dismiss]").onclick = dismiss;
    const undoButton = panel.querySelector("[data-companion-undo]");
    if (undoButton) undoButton.onclick = async () => {
      if (!active || epoch !== accountVersion()) return reset();
      dismiss();
      try { await undo(); }
      catch (error) { if (epoch === accountVersion()) onError(error); }
    };
    panel.addEventListener("pointerenter", pause);
    panel.addEventListener("pointerleave", resume);
    panel.addEventListener("focusin", pause);
    panel.addEventListener("focusout", () => queueMicrotask(resume));
    relocate();
    if (focusedAction) panel.querySelector(focusedAction)?.focus({ preventScroll: true });
  }
  function show({ remaining = 1, undo, epoch = accountVersion() } = {}) {
    if (!preferences.enabled || !canShow() || epoch !== accountVersion()) return false;
    const now = Date.now(), merged = active && active.epoch === epoch && now - active.lastCompletion <= 2000;
    const character = merged ? active.character : turn++ % 2 ? "horse" : "cow";
    const count = merged ? active.count + 1 : 1;
    const category = remaining === 0 ? "all-done" : count > 1 ? "batch" : "ordinary";
    const key = `${character}:${category}`;
    const index = indices.get(key) || 0;
    let message = selectCompanionMessage({ character, count, remaining, index });
    if (merged && active.message.mode === category) {
      message = { ...message, id: active.message.id, line: active.message.line };
    } else indices.set(key, index + 1);
    active = { character, count, remaining, undo, epoch, message, lastCompletion: now };
    render(!merged);
    armTimer(7000);
    return true;
  }
  function preferenceMarkup() {
    return `<section class="companion-preferences" aria-labelledby="companion-preferences-heading"><h3 id="companion-preferences-heading">牛马伙伴</h3><label><input type="checkbox" data-companion-preference="enabled" ${preferences.enabled ? "checked" : ""}><span>完成时显示牛马伙伴<small>小牛夸一夸，小马陪你看下一步</small></span></label><label><input type="checkbox" data-companion-preference="animated" ${preferences.animated ? "checked" : ""}><span>播放伙伴小动画<small>侧栏和完成反馈共用，跟随系统减少动态效果</small></span></label></section>`;
  }
  function change(event) {
    const setting = event.target?.dataset?.companionPreference;
    if (!["enabled", "animated"].includes(setting)) return false;
    preferences = writeCompanionPreferences({ ...preferences, [setting]: event.target.checked });
    if (!preferences.enabled) dismiss();
    else staticPose();
    globalThis.window?.dispatchEvent(new CustomEvent("niuma:companion-preferences", { detail: { ...preferences } }));
    return true;
  }
  doc.addEventListener("visibilitychange", () => {
    if (doc.visibilityState === "hidden") { pause(); staticPose(); }
    else resume();
  });
  // Once a person starts answering, the rating form gets the available space.
  const prioritizeRating = event => {
    if (event.target?.closest?.("#usage-rating")) dismiss();
  };
  doc.addEventListener("focusin", prioritizeRating, true);
  doc.addEventListener("click", prioritizeRating, true);
  globalThis.window?.addEventListener("resize", relocate);
  globalThis.window?.addEventListener("storage", event => {
    if (event.key !== COMPANION_STORAGE_KEY) return;
    preferences = readCompanionPreferences();
    if (!preferences.enabled) dismiss(); else staticPose();
    for (const setting of ["enabled", "animated"]) {
      const input = doc.querySelector(`[data-companion-preference="${setting}"]`);
      if (input) input.checked = preferences[setting];
    }
    globalThis.window?.dispatchEvent(new CustomEvent("niuma:companion-preferences", { detail: { ...preferences } }));
  });
  motion?.addEventListener?.("change", staticPose);
  return { show, dismiss, reset, detach, relocate, preferenceMarkup, change };
}
