import { readCompanionPreferences, writeCompanionPreferences } from "./completion-companion.js";
import { motionFrames, presentationDurations, createMotionTimeline, framePosition, fitDock, snapEdge } from "./companion-motion.js";

const sprites = {
  cow: {
    base: new URL("./assets/companions/pet-v2/cow.webp", import.meta.url).href,
    action: new URL("./assets/companions/pet-v3/cow-action.webp", import.meta.url).href,
    lifecycle: new URL("./assets/companions/pet-v3/cow-lifecycle.webp", import.meta.url).href,
  },
  horse: {
    base: new URL("./assets/companions/pet-v2/horse.webp", import.meta.url).href,
    action: new URL("./assets/companions/pet-v3/horse-action.webp", import.meta.url).href,
    lifecycle: new URL("./assets/companions/pet-v3/horse-lifecycle.webp", import.meta.url).href,
  },
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

/** Saved task state, completion intent and the visible performance are independent. */
export function createSidebarCompanion({ canInteract = () => true, canShow = () => true,
  isCompact = () => false, onOpen = () => {}, onCompletionDismiss } = {}) {
  const doc = document, reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  let preferences = readCompanionPreferences();
  let current = null, completion = null, displayed = null, presentation = null;
  let node, button, sprite, image, speech, announcement, menuButton, menu, slot;
  let open = false, pinned = false, suppressed = false, hovered = false, menuOpen = false;
  let idleTimer, hideTimer, showTimer, timeline = null, playing = false, motionCount = 0;
  let pendingCompletionMotion = false, queuedInteraction = false, cell = 0, atlas = "lifecycle";
  let drag = null, dragTargetCause = null, edge = "left", dockY = null, userDocked = false, ignoreClick = false;
  const loaded = new Map(), loading = new Map();
  const desired = () => completion ? { ...completion, kind: "completion", pose: "celebrate" }
    : current ? { ...current, kind: "mood" } : null;
  const character = () => displayed?.character || desired()?.character || "cow";
  const motionAllowed = () => preferences.animated && !reducedMotion?.matches && doc.visibilityState !== "hidden";
  const available = () => preferences.enabled && canShow() && canInteract() && Boolean(current || completion);
  const isChecklist = value => value?.kind === "mood" && value?.pose === "checklist";
  const owns = flow => timeline === flow && flow.active;

  function setPhase(phase) { if (node) node.dataset.phase = phase; }
  function setCaption(phase) { if (node) node.dataset.caption = phase; }
  function setFrame(next, sheet = atlas) {
    cell = next; atlas = sheet;
    if (!sprite) return;
    sprite.style.backgroundPosition = framePosition(next);
    node.dataset.frame = String(next); node.dataset.atlas = sheet;
    paintArtwork();
  }
  function setRestFrame() { setFrame(isChecklist(displayed) ? 5 : 0, isChecklist(displayed) ? "action" : "lifecycle"); }
  function stopMotion({ settle = true } = {}) {
    clearTimeout(idleTimer);
    timeline?.cancel(); timeline = null; playing = false;
    if (node) node.dataset.playing = "false";
    if (settle) setRestFrame();
  }
  function cancelPresentation({ adoptTarget = false } = {}) {
    stopMotion({ settle: false }); presentation = null; queuedInteraction = false;
    if (adoptTarget) {
      displayed = desired(); setPhase("steady"); setCaption("steady"); setRestFrame(); paintSpeech();
    }
  }
  function abortDrag() {
    const pointerId = drag?.pointerId;
    drag = null; dragTargetCause = null;
    if (node) node.dataset.dragging = "false";
    if (pointerId != null) { try { button.releasePointerCapture(pointerId); } catch {} }
    stopMotion();
  }
  function armBlink() {
    clearTimeout(idleTimer);
    if (!available() || !motionAllowed() || playing || presentation || drag || isChecklist(displayed)) return;
    idleTimer = setTimeout(() => play("blink"), 10000 + Math.random() * 5000);
  }
  function loadAtlas(who, sheet) {
    const key = `${who}:${sheet}`;
    if (loaded.get(key)) return Promise.resolve(true);
    if (loading.has(key)) return loading.get(key);
    const promise = new Promise(resolve => {
      const preload = new Image();
      preload.onload = () => {
        loaded.set(key, true); resolve(true);
        // This callback may belong to an abandoned performance; never select its character.
        if (node && character() === who && atlas === sheet) paintArtwork();
      };
      preload.onerror = () => resolve(false);
      preload.src = sprites[who][sheet];
    });
    loading.set(key, promise);
    return promise;
  }
  function paintArtwork() {
    if (!node) return;
    const who = character(), pose = displayed?.pose || "settle";
    const fallbackPose = displayed?.kind === "completion" ? "celebrate" : pose;
    const url = fallback[who]?.[fallbackPose] || fallback[who]?.settle || fallback.cow.arrive;
    if (image.getAttribute("src") !== url) image.src = url;
    const ready = Boolean(loaded.get(`${who}:${atlas}`));
    sprite.style.backgroundImage = `url("${sprites[who][atlas]}")`;
    node.dataset.spriteReady = String(ready); node.dataset.character = who;
    button.setAttribute("aria-label", `${names[who]}，点击打个招呼；方向键移动，Home 回到左侧`);
    if (!ready) void loadAtlas(who, atlas);
  }
  function paintSpeech() {
    if (!node) return;
    speech.textContent = displayed?.message || ""; speech.dataset.speaker = names[character()];
    const visible = open || displayed?.kind === "completion";
    speech.hidden = !visible;
    button.setAttribute("aria-expanded", String(visible)); node.dataset.open = String(visible);
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
    if (stop) cancelPresentation();
  }
  function reveal({ pin = false } = {}) {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    if (!available() || suppressed || drag) return;
    onOpen(); open = true; pinned ||= pin; paintSpeech();
  }
  function beginFlow(kind) {
    stopMotion({ settle: false });
    const flow = timeline = createMotionTimeline(); playing = true;
    node.dataset.playing = "true"; node.dataset.action = kind; node.dataset.motionCount = String(++motionCount);
    return flow;
  }
  async function sequence(flow, name) {
    if (!owns(flow)) return false;
    return flow.sequence(motionFrames[name], (next, sheet) => { if (owns(flow)) setFrame(next, sheet); });
  }
  async function putAwayChecklist(flow, value = displayed) {
    if (isChecklist(value) && atlas === "action" && cell <= 5) return sequence(flow, "putAway");
    return owns(flow);
  }
  async function recoverFacing(flow) {
    if (atlas !== "lifecycle") return owns(flow);
    const cells = cell >= 8 && cell <= 11
      ? [...Array.from({ length: cell - 7 }, (_, i) => cell - i), 7, 0]
      : cell >= 12 && cell < 15 ? Array.from({ length: 16 - cell }, (_, i) => cell + i) : [];
    return flow.sequence(cells.map(next => [next, 55, "lifecycle"]), (next, sheet) => { if (owns(flow)) setFrame(next, sheet); });
  }
  async function settleBeforeExit(flow, value) {
    if (atlas === "action" && cell < 15 && !isChecklist(value)) {
      const tail = Array.from({ length: 15 - cell }, (_, i) => [cell + i + 1, 45, "action"]);
      return flow.sequence(tail, (next, sheet) => { if (owns(flow)) setFrame(next, sheet); });
    }
    if (atlas === "lifecycle" && cell >= 12 && cell < 15) return recoverFacing(flow);
    return owns(flow);
  }
  async function finishMood(flow) {
    if (!owns(flow)) return false;
    if (isChecklist(displayed)) return sequence(flow, "checklist");
    setRestFrame(); return true;
  }
  function finishFlow(flow) {
    if (!owns(flow)) return;
    flow.cancel(); timeline = null; presentation = null; playing = false;
    node.dataset.playing = "false"; setPhase("steady"); setCaption("steady"); setRestFrame(); paintSpeech();
    if (queuedInteraction) { queuedInteraction = false; play("wave"); }
    else armBlink();
  }
  async function play(kind) {
    if (!node || !available() || !motionAllowed() || presentation || drag) return;
    const flow = beginFlow(kind);
    const who = character(), sheet = kind === "wave" || kind === "celebrate" ? "action" : "lifecycle";
    if (!await loadAtlas(who, sheet) || !owns(flow)) return finishFlow(flow);
    if ((kind === "wave" || kind === "celebrate") && !await putAwayChecklist(flow)) return;
    if (!await sequence(flow, kind)) return;
    if (!await finishMood(flow)) return;
    finishFlow(flow);
    // A drag can postpone a new mood's entrance, but never changes its saved source state.
    const next = desired();
    const queuedCause = dragTargetCause; dragTargetCause = null;
    if (queuedCause) present(next, queuedCause);
    else if (next && (next.character !== displayed?.character || next.kind !== displayed?.kind || next.pose !== displayed?.pose))
      present(next, next.kind === "completion" ? "completion" : "mood");
  }
  function adoptImmediately(next) {
    cancelPresentation(); displayed = next ? { ...next } : null;
    setPhase("steady"); setCaption("steady"); setRestFrame(); paint(); armBlink();
  }
  function present(next, cause = "mood") {
    if (!next) return adoptImmediately(null);
    if (drag?.moved) { dragTargetCause = cause; paint(); return; }
    if (!displayed || !node) { displayed = { ...next }; setRestFrame(); paint(); return; }
    if (!available() || !motionAllowed()) return adoptImmediately(next);
    const outgoing = { ...displayed }, changedCharacter = outgoing.character !== next.character;
    const concealed = ["leaving", "enter-start"].includes(node.dataset.phase);
    cancelPresentation();
    const flow = beginFlow(cause === "completion" ? "celebrate" : "return");
    presentation = { target: { ...next }, cause, changedCharacter };
    pendingCompletionMotion = false;
    setCaption(cause === "completion" && !changedCharacter ? "steady" : "leaving");
    setPhase(changedCharacter ? "goodbye" : concealed ? "enter-start" : "settling");
    paint();
    void (async () => {
      const ready = await Promise.all([loadAtlas(outgoing.character, "lifecycle"), loadAtlas(next.character, "lifecycle"), loadAtlas(next.character, "action")]);
      if (!owns(flow)) return;
      if (!ready.every(Boolean)) { adoptImmediately(desired()); return; }
      if (changedCharacter) {
        if (!concealed) {
          if (!await settleBeforeExit(flow, outgoing) || !await putAwayChecklist(flow) || !await sequence(flow, "depart")) return;
          setPhase("leaving");
          if (!await flow.wait(presentationDurations.leave) || !owns(flow)) return;
        }
        displayed = { ...presentation.target };
        setPhase("enter-start"); setFrame(12, "lifecycle"); paint();
        if (!await flow.wait(16) || !owns(flow)) return;
        setPhase("entering");
        if (!await sequence(flow, "arrive")) return;
      } else {
        // Same actor stays in place; there is no source or whole-picture replacement.
        if (cause === "completion") {
          if (!concealed && !await recoverFacing(flow) || !await putAwayChecklist(flow, outgoing)) return;
          displayed = { ...presentation.target }; paintSpeech();
        }
        if (concealed) {
          setPhase("enter-start");
          if (!await flow.wait(16) || !owns(flow)) return;
          setPhase("entering");
          if (!await sequence(flow, "arrive")) return;
        }
      }
      if (!owns(flow)) return;
      if (cause === "completion") {
        setCaption("steady"); setPhase("steady"); paintSpeech();
        if (!await putAwayChecklist(flow) || !await sequence(flow, "celebrate")) return;
      } else {
        if (!changedCharacter && !await sequence(flow, "settle")) return;
        displayed = { ...presentation.target };
      }
      if (!await finishMood(flow)) return;
      finishFlow(flow); paint();
    })();
  }
  function viewport() {
    const visual = globalThis.visualViewport;
    return { width: visual?.width || innerWidth, height: visual?.height || innerHeight,
      offsetX: visual?.offsetLeft || 0, offsetY: visual?.offsetTop || 0 };
  }
  function reposition() {
    if (!node) return;
    const wasHidden = node.hidden, shown = available(); node.hidden = !shown; slot?.setAttribute("data-visible", String(shown));
    if (!shown) { cancelPresentation({ adoptTarget: true }); abortDrag(); return; }
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
    if (pendingCompletionMotion && motionAllowed() && !presentation && !playing) {
      pendingCompletionMotion = false;
      present(desired(), "completion");
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
    if (!cancelled) play("land");
    else {
      setRestFrame();
      const queuedCause = dragTargetCause; dragTargetCause = null;
      if (queuedCause) present(desired(), queuedCause);
      else {
        const next = desired();
        if (next && (next.character !== displayed?.character || next.kind !== displayed?.kind || next.pose !== displayed?.pose))
          present(next, next.kind === "completion" ? "completion" : "mood");
      }
    }
    setTimeout(() => { ignoreClick = false; }, 0);
  }
  function updateSharedPreference(value) {
    preferences = writeCompanionPreferences({ ...preferences, ...value });
    globalThis.window?.dispatchEvent(new CustomEvent("niuma:companion-preferences", { detail: { ...preferences } }));
  }
  function makeNode() {
    node = doc.createElement("section"); node.id = "sidebar-companion"; node.className = "sidebar-companion";
    node.setAttribute("aria-label", "牛马伙伴"); node.dataset.playing = "false"; node.dataset.motionCount = "0"; node.dataset.completion = "false"; node.dataset.phase = "steady"; node.dataset.caption = "steady";
    node.innerHTML = `<div class="companion-pet-stage"><button type="button" id="sidebar-companion-button" class="sidebar-companion-button" data-sidebar-companion-button aria-describedby="sidebar-companion-speech" aria-expanded="false"><span class="companion-pet-figure" aria-hidden="true"><span class="companion-pet-sprite"></span><img class="companion-pet-fallback" width="136" height="136" alt="" decoding="async" draggable="false"></span></button><button type="button" class="companion-pet-options" aria-label="伙伴选项" aria-expanded="false" aria-controls="companion-pet-menu">${dotsIcon}</button></div><p id="sidebar-companion-speech" class="sidebar-companion-speech" data-companion-message hidden></p><div id="companion-pet-menu" class="companion-pet-menu" aria-label="伙伴选项" hidden><button type="button" data-pet-action="motion"></button><button type="button" data-pet-action="home">回到左下角</button><button type="button" data-pet-action="hide">关闭牛马伙伴</button></div><span class="sr-only" data-pet-announcement role="status" aria-live="polite" aria-atomic="true"></span>`;
    button = node.querySelector("#sidebar-companion-button"); sprite = node.querySelector(".companion-pet-sprite"); image = node.querySelector("img");
    speech = node.querySelector("#sidebar-companion-speech"); menuButton = node.querySelector(".companion-pet-options"); menu = node.querySelector(".companion-pet-menu"); announcement = node.querySelector("[data-pet-announcement]");
    doc.body.append(node);
    node.addEventListener("pointerenter", event => {
      if (event.pointerType === "touch") return;
      hovered = true; suppressed = false; clearTimeout(hideTimer);
      if (!open && !completion) showTimer = setTimeout(() => reveal(), 350);
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
      if (presentation) queuedInteraction = true;
      else play(completion || current?.pose === "dance" || current?.pose === "checklist" ? "celebrate" : "wave");
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
        drag.moved = true; cancelPresentation(); setPhase("steady"); closeMenu(); node.dataset.dragging = "true"; node.dataset.action = "drag";
        if (motionAllowed()) setFrame(13, "base"); else setRestFrame();
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
    const previous = current; current = next;
    if (!completion) {
      const target = desired();
      if (!displayed) { displayed = target; setRestFrame(); }
      else if (presentation) {
        if (presentation.target.character !== target?.character) present(target, "mood");
        else presentation.target = target;
      } else if (displayed.character !== target?.character || previous?.pose !== next?.pose) present(target, "mood");
      else displayed = { ...target };
    }
    paint(); if (!playing) armBlink();
  }
  function showCompletion({ character: who = "cow", message: copy = "", count = 1, remaining = 1, replay = true } = {}) {
    completion = { character: who === "horse" ? "horse" : "cow", message: typeof copy === "string" ? copy : copy?.line || "",
      count, remaining, mode: typeof copy === "object" ? copy?.mode || "ordinary" : "ordinary" };
    suppressed = false; open = true; closeMenu();
    const mergesIntoEntrance = !replay && presentation?.cause === "completion" && presentation.target.character === completion.character;
    if (mergesIntoEntrance || !replay && displayed?.kind === "completion" && displayed.character === completion.character) {
      const target = desired();
      if (displayed?.kind === "completion" && displayed.character === completion.character) displayed = target;
      if (mergesIntoEntrance) presentation.target = target;
      paint();
    } else {
      pendingCompletionMotion = preferences.animated && !reducedMotion?.matches && !available();
      present(desired(), "completion");
    }
    if (announcement) announcement.textContent = `${typeof copy === "object" ? copy?.title || "任务已完成" : "任务已完成"}。${names[completion.character]}说：${completion.message}`;
    return preferences.enabled;
  }
  function endCompletion() {
    if (!completion) return;
    completion = null; pendingCompletionMotion = false;
    if (announcement) announcement.textContent = "";
    open = hovered || node?.contains(doc.activeElement); pinned = false;
    present(desired(), "return"); paint();
  }
  // The fixed dock stays outside app.innerHTML and the mobile sidebar's inert subtree.
  function detach() { slot = null; }
  function mount(nextSlot) {
    if (!nextSlot && !node) return;
    slot = nextSlot; if (!node) makeNode(); setFrame(cell, atlas); paint(); armBlink();
    for (const who of ["cow", "horse"]) for (const sheet of ["lifecycle", "action", "base"]) void loadAtlas(who, sheet);
  }
  function reset() {
    dismiss({ stop: true }); abortDrag(); current = null; completion = null; displayed = null; presentation = null;
    pendingCompletionMotion = false; queuedInteraction = false; edge = "left"; dockY = null; userDocked = false; motionCount = 0; suppressed = false;
    if (announcement) announcement.textContent = "";
    setPhase("steady"); setCaption("steady"); paint();
  }
  function setPreferences(value) {
    preferences = { ...preferences, ...value };
    if (!motionAllowed() || !preferences.enabled) { pendingCompletionMotion = false; cancelPresentation({ adoptTarget: true }); abortDrag(); }
    paint(); armBlink();
  }
  // Pointer movement has no gaze or pose effect; only the figure's drag handler consumes it.
  doc.addEventListener("click", event => { if (node && !node.contains(event.target)) dismiss(); }, true);
  doc.addEventListener("keydown", event => {
    if (event.key !== "Escape" || (!open && !menuOpen) || !canInteract()) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (menuOpen) { closeMenu({ focus: true }); return; }
    if (completion) { if (onCompletionDismiss) onCompletionDismiss(); else endCompletion(); }
    dismiss({ suppress: true });
  }, true);
  doc.addEventListener("visibilitychange", () => {
    if (doc.visibilityState === "hidden") { cancelPresentation({ adoptTarget: true }); abortDrag(); }
    else { reposition(); armBlink(); }
  });
  globalThis.window?.addEventListener("resize", () => { cancelPresentation({ adoptTarget: true }); abortDrag(); reposition(); armBlink(); });
  globalThis.visualViewport?.addEventListener("resize", reposition);
  globalThis.visualViewport?.addEventListener("scroll", reposition);
  globalThis.window?.addEventListener("niuma:companion-preferences", event => setPreferences(event.detail || {}));
  reducedMotion?.addEventListener?.("change", () => { pendingCompletionMotion = false; cancelPresentation({ adoptTarget: true }); abortDrag(); paint(); armBlink(); });
  return { mount, detach, update, dismiss, reset, reposition, preferences: setPreferences, showCompletion, endCompletion };
}
