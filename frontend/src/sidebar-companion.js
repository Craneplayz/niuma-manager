import { readCompanionPreferences, writeCompanionPreferences } from "./completion-companion.js";
import { motionFrames, framePosition, lookFrame, fitDock, snapEdge } from "./companion-motion.js";

const sprites = {
  cow: new URL("./assets/companions/pet-v2/cow.webp", import.meta.url).href,
  horse: new URL("./assets/companions/pet-v2/horse.webp", import.meta.url).href,
};
const fallback = {
  cow: {
    arrive: new URL("./assets/companions/cow-arrive.webp", import.meta.url).href,
    settle: new URL("./assets/companions/cow-settle.webp", import.meta.url).href,
    celebrate: new URL("./assets/companions/cow-celebrate.webp", import.meta.url).href,
    wave: new URL("./assets/companions/cow-wave.webp", import.meta.url).href,
    dance: new URL("./assets/companions/cow-dance.webp", import.meta.url).href,
  },
  horse: {
    settle: new URL("./assets/companions/horse-settle.webp", import.meta.url).href,
    checklist: new URL("./assets/companions/horse-checklist.webp", import.meta.url).href,
    celebrate: new URL("./assets/companions/horse-celebrate.webp", import.meta.url).href,
    wave: new URL("./assets/companions/horse-wave.webp", import.meta.url).href,
  },
};
const names = { cow: "牛牛", horse: "马马" };
const dotsIcon = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="currentColor"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>';

/** One persistent character: mood, completion, pointer response and edge docking. */
export function createSidebarCompanion({ canInteract = () => true, canShow = () => true,
  isCompact = () => false, onOpen = () => {}, onCompletionDismiss } = {}) {
  const doc = document, reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  let preferences = readCompanionPreferences();
  let current = null, completion = null, node, button, sprite, image, speech, announcement, menuButton, menu, slot;
  let open = false, pinned = false, suppressed = false, hovered = false, menuOpen = false;
  let frameTimer, idleTimer, hideTimer, showTimer, gazeRequest;
  let animationToken = 0, playing = false, motionCount = 0, pendingCompletionMotion = false;
  let drag = null, edge = "left", dockY = null, userDocked = false, ignoreClick = false;
  const loaded = new Map();
  const character = () => completion?.character || current?.character || "cow";
  const message = () => completion?.message || current?.message || "";
  const motionAllowed = () => preferences.animated && !reducedMotion?.matches && doc.visibilityState !== "hidden";
  const available = () => preferences.enabled && canShow() && canInteract() && Boolean(current || completion);
  const restFrame = () => completion || current?.pose === "dance" || current?.pose === "checklist" ? 12 : 0;

  function setFrame(next) {
    if (!sprite) return;
    sprite.style.backgroundPosition = framePosition(next);
    node.dataset.frame = String(next);
  }
  function stopMotion({ settle = true } = {}) {
    clearTimeout(frameTimer); clearTimeout(idleTimer); animationToken++;
    playing = false;
    if (node) node.dataset.playing = "false";
    if (settle) setFrame(restFrame());
  }
  function abortDrag() {
    const pointerId = drag?.pointerId;
    drag = null;
    if (node) node.dataset.dragging = "false";
    if (pointerId != null) { try { button.releasePointerCapture(pointerId); } catch {} }
    globalThis.cancelAnimationFrame?.(gazeRequest);
    stopMotion();
  }
  function armBlink() {
    clearTimeout(idleTimer);
    if (!available() || !motionAllowed() || playing || drag) return;
    idleTimer = setTimeout(() => play("blink"), 6500 + Math.random() * 2500);
  }
  function play(kind) {
    if (!node || !available() || !motionAllowed() || !loaded.get(character())) return setFrame(restFrame());
    stopMotion({ settle: false });
    const token = animationToken, sequence = motionFrames[kind] || motionFrames.wave;
    playing = true;
    node.dataset.playing = "true"; node.dataset.action = kind;
    node.dataset.motionCount = String(++motionCount);
    let index = 0;
    const advance = () => {
      if (token !== animationToken || !available() || !motionAllowed() || drag) return stopMotion();
      if (index >= sequence.length) {
        playing = false; node.dataset.playing = "false"; setFrame(restFrame()); armBlink(); return;
      }
      const [next, duration] = sequence[index++];
      setFrame(next); frameTimer = setTimeout(advance, duration);
    };
    advance();
  }
  function loadSprite(who) {
    if (loaded.has(who)) return;
    loaded.set(who, false);
    const preload = new Image();
    preload.onload = () => {
      loaded.set(who, true);
      if (character() !== who || !node) return;
      paintArtwork();
      reposition();
      if (!playing) armBlink();
    };
    // A failed sheet retains the original authored character and all controls.
    preload.onerror = () => { if (character() === who && node) node.dataset.spriteReady = "false"; };
    preload.src = sprites[who];
  }
  function paintArtwork() {
    if (!node) return;
    const who = character(), pose = completion ? "celebrate" : current?.pose || "settle";
    const url = fallback[who]?.[pose] || fallback[who]?.settle || fallback.cow.arrive;
    if (image.getAttribute("src") !== url) image.src = url;
    sprite.style.backgroundImage = `url("${sprites[who]}")`;
    node.dataset.spriteReady = String(Boolean(loaded.get(who))); node.dataset.character = who;
    button.setAttribute("aria-label", `${names[who]}，点击打个招呼；方向键移动，Home 回到左侧`);
    loadSprite(who);
  }
  function paintSpeech() {
    if (!node) return;
    speech.textContent = message(); speech.dataset.speaker = names[character()];
    speech.hidden = !(open || completion);
    button.setAttribute("aria-expanded", String(open || Boolean(completion)));
    node.dataset.open = String(open || Boolean(completion));
  }
  function closeMenu({ focus = false } = {}) {
    menuOpen = false;
    if (menu) menu.hidden = true;
    menuButton?.setAttribute("aria-expanded", "false");
    if (focus && available()) menuButton.focus({ preventScroll: true });
  }
  function dismiss({ suppress = false, stop = false } = {}) {
    clearTimeout(showTimer); clearTimeout(hideTimer); open = false; pinned = false;
    if (suppress) suppressed = true;
    closeMenu(); paintSpeech();
    if (stop) stopMotion();
  }
  function reveal({ replay = true, pin = false } = {}) {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    if (!available() || suppressed || drag) return;
    onOpen(); open = true; pinned ||= pin; paintSpeech();
    if (replay && !completion) play(current?.pose === "dance" || current?.pose === "checklist" ? "celebrate" : "wave");
  }
  function viewport() {
    const visual = globalThis.visualViewport;
    return { width: visual?.width || innerWidth, height: visual?.height || innerHeight,
      offsetX: visual?.offsetLeft || 0, offsetY: visual?.offsetTop || 0 };
  }
  function reposition() {
    if (!node) return;
    const wasHidden = node.hidden, shown = available(); node.hidden = !shown; slot?.setAttribute("data-visible", String(shown));
    if (!shown) { abortDrag(); return; }
    const compact = isCompact() || innerWidth <= 700;
    node.dataset.compact = String(compact); node.dataset.animated = String(motionAllowed());
    if (drag) return;
    const view = viewport(), bounds = node.getBoundingClientRect();
    const width = bounds.width || (compact ? 172 : 208), height = bounds.height || (compact ? 164 : 204);
    const sidebar = doc.querySelector("#sidebar"), rail = sidebar?.getBoundingClientRect();
    const account = sidebar?.querySelector(".sidebar-bottom")?.getBoundingClientRect();
    let x = edge === "right" ? view.width - width - 12 : 12, y = dockY ?? view.height - height - 16;
    if (!userDocked && innerWidth > 700 && rail && account) {
      x = rail.left + (rail.width - width) / 2; y = account.top - height - 8;
      const navBottom = sidebar.querySelector(".task-nav")?.getBoundingClientRect().bottom || 0;
      if (y < navBottom + 12) y = view.height - height - 16;
    }
    const obstacles = [doc.querySelector("#usage-rating:not(.is-closing)"), doc.querySelector("#toast.undo-toast.visible")];
    for (const obstacle of obstacles) {
      if (!obstacle) continue;
      const b = obstacle.getBoundingClientRect();
      if (x < b.right && x + width > b.left && y < b.bottom && y + height > b.top) y = b.top - height - 12;
    }
    const fitted = fitDock({ x, y, width, height, viewportWidth: view.width, viewportHeight: view.height });
    node.style.left = `${fitted.x + view.offsetX}px`; node.style.top = `${fitted.y + view.offsetY}px`; node.dataset.edge = edge;
    if (pendingCompletionMotion && motionAllowed() && loaded.get(character())) {
      pendingCompletionMotion = false;
      play("celebrate");
    } else if (wasHidden && !playing) armBlink();
  }
  function setPosition(x, y) {
    const view = viewport(), bounds = node.getBoundingClientRect();
    const fitted = fitDock({ x, y, width: bounds.width, height: bounds.height, viewportWidth: view.width, viewportHeight: view.height });
    node.style.left = `${fitted.x + view.offsetX}px`; node.style.top = `${fitted.y + view.offsetY}px`;
  }
  function finishDrag(event, cancelled = false) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const moved = drag.moved; drag = null; node.dataset.dragging = "false";
    try { button.releasePointerCapture(event.pointerId); } catch {}
    if (!moved) return;
    ignoreClick = true; clearTimeout(hideTimer);
    const view = viewport(), bounds = node.getBoundingClientRect();
    edge = snapEdge(bounds.left + bounds.width / 2, view.width); dockY = bounds.top; userDocked = true;
    reposition();
    if (!cancelled) play("land"); else setFrame(restFrame());
    setTimeout(() => { ignoreClick = false; }, 0);
  }
  function updateSharedPreference(value) {
    preferences = writeCompanionPreferences({ ...preferences, ...value });
    globalThis.window?.dispatchEvent(new CustomEvent("niuma:companion-preferences", { detail: { ...preferences } }));
  }
  function makeNode() {
    node = doc.createElement("section"); node.id = "sidebar-companion"; node.className = "sidebar-companion";
    node.setAttribute("aria-label", "牛马伙伴"); node.dataset.playing = "false"; node.dataset.motionCount = "0"; node.dataset.completion = "false";
    node.innerHTML = `<div class="companion-pet-stage"><button type="button" id="sidebar-companion-button" class="sidebar-companion-button" data-sidebar-companion-button aria-describedby="sidebar-companion-speech" aria-expanded="false"><span class="companion-pet-sprite" aria-hidden="true"></span><img class="companion-pet-fallback" width="136" height="136" alt="" aria-hidden="true" decoding="async" draggable="false"></button><button type="button" class="companion-pet-options" aria-label="伙伴选项" aria-expanded="false" aria-controls="companion-pet-menu">${dotsIcon}</button></div><p id="sidebar-companion-speech" class="sidebar-companion-speech" data-companion-message hidden></p><div id="companion-pet-menu" class="companion-pet-menu" aria-label="伙伴选项" hidden><button type="button" data-pet-action="motion"></button><button type="button" data-pet-action="home">回到左下角</button><button type="button" data-pet-action="hide">关闭牛马伙伴</button></div><span class="sr-only" data-pet-announcement role="status" aria-live="polite" aria-atomic="true"></span>`;
    button = node.querySelector("#sidebar-companion-button"); sprite = node.querySelector(".companion-pet-sprite"); image = node.querySelector("img");
    speech = node.querySelector("#sidebar-companion-speech"); menuButton = node.querySelector(".companion-pet-options"); menu = node.querySelector(".companion-pet-menu"); announcement = node.querySelector("[data-pet-announcement]");
    doc.body.append(node);
    node.addEventListener("pointerenter", event => {
      if (event.pointerType === "touch") return;
      hovered = true; suppressed = false; clearTimeout(hideTimer);
      if (!open && !completion) showTimer = setTimeout(() => reveal(), 130);
    });
    node.addEventListener("pointerleave", () => {
      hovered = false; clearTimeout(showTimer); suppressed = false;
      if (!pinned && !menuOpen && !node.contains(doc.activeElement)) hideTimer = setTimeout(() => dismiss(), 220);
    });
    node.addEventListener("focusin", event => { if (event.target === button) { suppressed = false; reveal({ replay: false }); } });
    node.addEventListener("focusout", () => queueMicrotask(() => { if (!node.contains(doc.activeElement) && !hovered && !pinned) dismiss(); }));
    button.addEventListener("click", event => {
      event.stopPropagation(); if (ignoreClick || drag?.moved) return;
      suppressed = false; reveal({ replay: false, pin: true });
      play(completion || current?.pose === "dance" || current?.pose === "checklist" ? "celebrate" : "wave");
    });
    button.addEventListener("pointerdown", event => {
      if (event.button !== 0 || !available()) return;
      const bounds = node.getBoundingClientRect();
      drag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, x: bounds.left, y: bounds.top, moved: false };
      try { button.setPointerCapture(event.pointerId); } catch {}
    });
    button.addEventListener("pointermove", event => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const dx = event.clientX - drag.startX, dy = event.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < 7) return;
      if (!drag.moved) {
        drag.moved = true; stopMotion({ settle: false }); closeMenu(); node.dataset.dragging = "true"; node.dataset.action = "drag";
        setFrame(motionAllowed() ? 13 : restFrame());
      }
      setPosition(drag.x + dx, drag.y + dy); event.preventDefault();
    });
    button.addEventListener("pointerup", event => finishDrag(event));
    button.addEventListener("pointercancel", event => finishDrag(event, true));
    button.addEventListener("lostpointercapture", event => { if (drag?.moved) finishDrag(event, true); });
    button.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home"].includes(event.key)) return;
      event.preventDefault();
      const bounds = node.getBoundingClientRect();
      if (event.key === "Home") { edge = "left"; dockY = null; userDocked = false; }
      else {
        userDocked = true;
        if (event.key === "ArrowLeft") edge = "left";
        if (event.key === "ArrowRight") edge = "right";
        dockY = bounds.top + (event.key === "ArrowUp" ? -32 : event.key === "ArrowDown" ? 32 : 0);
      }
      reposition(); play("land");
    });
    menuButton.addEventListener("click", event => {
      event.stopPropagation(); menuOpen = !menuOpen; menu.hidden = !menuOpen; menuButton.setAttribute("aria-expanded", String(menuOpen));
      menu.querySelector('[data-pet-action="motion"]').textContent = preferences.animated ? "暂停伙伴动作" : "开启伙伴动作";
      if (menuOpen) {
        node.dataset.menuPlacement = "above";
        if (menu.getBoundingClientRect().top < 8) node.dataset.menuPlacement = "below";
        menu.querySelector("button").focus({ preventScroll: true });
      }
    });
    menu.addEventListener("click", event => {
      const action = event.target.closest("[data-pet-action]")?.dataset.petAction;
      if (!action) return;
      if (action === "motion") updateSharedPreference({ animated: !preferences.animated });
      if (action === "hide") updateSharedPreference({ enabled: false });
      if (action === "home") { edge = "left"; dockY = null; userDocked = false; reposition(); }
      closeMenu({ focus: action !== "hide" });
    });
  }
  function paint() {
    if (!node) return;
    node.dataset.state = current?.key || "unknown"; node.dataset.ready = String(current?.ready || false);
    node.dataset.overdue = current?.overdue ?? ""; node.dataset.completedToday = current?.completedToday ?? "";
    node.dataset.completion = String(Boolean(completion)); node.dataset.count = completion?.count || ""; node.dataset.mode = completion?.mode || "";
    paintArtwork(); paintSpeech(); reposition();
  }
  function update(next) {
    const changed = current?.character !== next?.character || current?.key !== next?.key;
    current = next;
    if (changed && !completion) stopMotion();
    paint(); if (!playing) armBlink();
  }
  function showCompletion({ character: who = "cow", message: copy = "", count = 1, remaining = 1, replay = true } = {}) {
    completion = { character: who === "horse" ? "horse" : "cow", message: typeof copy === "string" ? copy : copy?.line || "",
      count, remaining, mode: typeof copy === "object" ? copy?.mode || "ordinary" : "ordinary" };
    if (replay) pendingCompletionMotion = preferences.animated && !reducedMotion?.matches;
    suppressed = false; open = true; closeMenu(); paint();
    if (announcement) announcement.textContent = `${typeof copy === "object" ? copy?.title || "任务已完成" : "任务已完成"}。${names[completion.character]}说：${completion.message}`;
    return preferences.enabled;
  }
  function endCompletion() {
    if (!completion) return;
    completion = null; pendingCompletionMotion = false;
    if (announcement) announcement.textContent = "";
    open = hovered || node?.contains(doc.activeElement); stopMotion(); paint(); armBlink();
  }
  // The fixed dock stays outside app.innerHTML and the mobile sidebar's inert subtree.
  function detach() { slot = null; }
  function mount(nextSlot) {
    if (!nextSlot && !node) return;
    slot = nextSlot; if (!node) makeNode(); paint(); armBlink();
  }
  function reset() {
    dismiss({ stop: true }); abortDrag(); current = null; completion = null; pendingCompletionMotion = false; edge = "left"; dockY = null; userDocked = false; motionCount = 0; suppressed = false;
    if (announcement) announcement.textContent = "";
    paint();
  }
  function setPreferences(value) {
    preferences = { ...preferences, ...value };
    if (!motionAllowed() || !preferences.enabled) abortDrag();
    paint(); armBlink();
  }
  doc.addEventListener("pointermove", event => {
    if (event.pointerType === "touch" || !node || !available() || !motionAllowed() || playing || drag) return;
    globalThis.cancelAnimationFrame?.(gazeRequest);
    gazeRequest = globalThis.requestAnimationFrame?.(() => {
      const b = button.getBoundingClientRect(); setFrame(lookFrame(event.clientX - b.left - b.width / 2, event.clientY - b.top - b.height / 2, restFrame()));
    });
  }, { passive: true });
  doc.addEventListener("click", event => { if (node && !node.contains(event.target)) dismiss(); }, true);
  doc.addEventListener("keydown", event => {
    if (event.key !== "Escape" || (!open && !menuOpen) || !canInteract()) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (menuOpen) { closeMenu({ focus: true }); return; }
    if (completion) { if (onCompletionDismiss) onCompletionDismiss(); else endCompletion(); }
    dismiss({ suppress: true });
  }, true);
  doc.addEventListener("visibilitychange", () => {
    if (doc.visibilityState === "hidden") abortDrag(); else { reposition(); armBlink(); }
  });
  globalThis.window?.addEventListener("resize", () => { abortDrag(); reposition(); armBlink(); });
  globalThis.visualViewport?.addEventListener("resize", reposition);
  globalThis.visualViewport?.addEventListener("scroll", reposition);
  globalThis.window?.addEventListener("niuma:companion-preferences", event => setPreferences(event.detail || {}));
  reducedMotion?.addEventListener?.("change", () => { stopMotion(); reposition(); armBlink(); });
  return { mount, detach, update, dismiss, reset, reposition, preferences: setPreferences, showCompletion, endCompletion };
}
