import { readCompanionPreferences, writeCompanionPreferences } from "./completion-companion.js";
import { motionFrames, storyFrames, createMotionTimeline, framePosition, fitDock, snapEdge } from "./companion-motion.js";

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
const storySprites = {
  "cow-to-horse": [
    new URL("./assets/companions/pet-v4/cow-to-horse-1.webp", import.meta.url).href,
    new URL("./assets/companions/pet-v4/cow-to-horse-2.webp", import.meta.url).href,
    new URL("./assets/companions/pet-v4/cow-to-horse-3.webp", import.meta.url).href,
  ],
  "horse-to-cow": [
    new URL("./assets/companions/pet-v4/horse-to-cow-1.webp", import.meta.url).href,
    new URL("./assets/companions/pet-v4/horse-to-cow-2.webp", import.meta.url).href,
    new URL("./assets/companions/pet-v4/horse-to-cow-3.webp", import.meta.url).href,
  ],
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

/** The saved overdue policy chooses the actor. Only policy edges author a story. */
export function createSidebarCompanion({ canInteract = () => true, canShow = () => true,
  isCompact = () => false, onOpen = () => {}, onCompletionDismiss, onStoryChange = () => {} } = {}) {
  const doc = document, reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  let preferences = readCompanionPreferences();
  let current = null, completion = null, displayed = null, story = null;
  let policyKnown = false, policyCharacter = "cow", storyCount = 0;
  let node, button, sprite, storySprite, image, speech, announcement, menuButton, menu, slot;
  let open = false, pinned = false, suppressed = false, hovered = false, menuOpen = false;
  let idleTimer, hideTimer, showTimer, moodTimer, moodPeek = false, timeline = null, playing = false, motionCount = 0;
  let pendingCompletionMotion = false, queuedInteraction = false, cell = 0, atlas = "lifecycle";
  let drag = null, dragTargetCause = null, edge = "left", dockY = null, userDocked = false, ignoreClick = false;
  const loaded = new Map(), loading = new Map();
  const getCharacter = () => policyKnown ? policyCharacter : current?.character === "horse" ? "horse" : "cow";
  const desired = () => current ? { ...current, ...(completion || {}), character: getCharacter(),
    kind: completion ? "completion" : "mood", pose: getCharacter() === "horse" ? "checklist" : current.pose || "settle" } : null;
  const character = () => displayed?.character || getCharacter();
  const motionAllowed = () => preferences.animated && !reducedMotion?.matches && doc.visibilityState !== "hidden";
  const available = () => preferences.enabled && canShow() && canInteract() && Boolean(current || completion);
  const isChecklist = value => value?.character === "horse";
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
    clearTimeout(idleTimer); timeline?.cancel(); timeline = null; playing = false;
    if (node) node.dataset.playing = "false";
    if (settle) setRestFrame();
  }
  function clearStory() {
    const wasActive = Boolean(story?.started);
    story = null;
    if (node) { node.dataset.storyActive = "false"; node.dataset.story = ""; }
    if (wasActive) onStoryChange(false);
  }
  function showMoodAfterStory() {
    clearTimeout(moodTimer); moodPeek = true;
    moodTimer = setTimeout(() => { moodPeek = false; paintSpeech(); }, 4000);
  }
  function cancelPresentation({ adoptTarget = false } = {}) {
    stopMotion({ settle: false }); clearStory(); queuedInteraction = false;
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
    if (!available() || !motionAllowed() || playing || story || drag || isChecklist(displayed)) return;
    idleTimer = setTimeout(() => play("blink"), 10000 + Math.random() * 5000);
  }
  function loadUrl(key, url, onReady) {
    if (loaded.get(key)) return Promise.resolve(true);
    if (loading.has(key)) return loading.get(key);
    const promise = new Promise(resolve => {
      const preload = new Image();
      preload.onload = () => { loaded.set(key, true); resolve(true); onReady?.(); };
      preload.onerror = () => resolve(false);
      preload.src = url;
    });
    loading.set(key, promise); return promise;
  }
  function loadAtlas(who, sheet) {
    return loadUrl(`${who}:${sheet}`, sprites[who][sheet], () => {
      if (node && character() === who && atlas === sheet) paintArtwork();
    });
  }
  function paintArtwork() {
    if (!node) return;
    const who = character(), pose = displayed?.pose || "settle";
    const url = fallback[who]?.[pose] || fallback[who]?.settle || fallback.cow.arrive;
    if (image.getAttribute("src") !== url) image.src = url;
    const ready = Boolean(loaded.get(`${who}:${atlas}`));
    sprite.style.backgroundImage = `url("${sprites[who][atlas]}")`;
    node.dataset.spriteReady = String(ready); node.dataset.character = who;
    button.setAttribute("aria-label", `${names[getCharacter()]}，点击打个招呼；方向键移动，Home 回到左侧`);
    if (!ready) void loadAtlas(who, atlas);
  }
  function paintSpeech() {
    if (!node) return;
    const value = story ? desired() : displayed;
    speech.textContent = value?.message || "";
    // The film contains both friends. Its live status stays below the whole scene.
    speech.dataset.speaker = story ? "" : names[getCharacter()];
    const visible = open || moodPeek || Boolean(completion) || Boolean(story?.started) || displayed?.kind === "completion";
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
    if (stop) cancelPresentation({ adoptTarget: true });
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
  async function finishMood(flow) {
    if (!owns(flow)) return false;
    if (isChecklist(displayed) && (atlas !== "action" || cell !== 5)) return sequence(flow, "checklist");
    setRestFrame(); return true;
  }
  function finishFlow(flow) {
    if (!owns(flow)) return;
    flow.cancel(); timeline = null; playing = false;
    node.dataset.playing = "false"; setPhase("steady"); setCaption("steady"); setRestFrame(); paintSpeech(); armBlink();
  }
  async function play(kind) {
    if (!node || !available() || !motionAllowed() || story || drag) return;
    displayed = desired(); paint();
    const flow = beginFlow(kind), who = getCharacter();
    const sheet = kind === "wave" || kind === "celebrate" ? "action" : "lifecycle";
    if (!await loadAtlas(who, sheet) || !owns(flow)) return finishFlow(flow);
    const gesture = isChecklist(displayed) && (kind === "wave" || kind === "celebrate") ? "horseAck" : kind;
    if (!await sequence(flow, gesture) || !await finishMood(flow)) return;
    finishFlow(flow);
    const queuedCause = dragTargetCause; dragTargetCause = null;
    if (queuedCause === "completion") play("celebrate");
  }
  function adoptImmediately(next) {
    cancelPresentation(); displayed = next ? { ...next } : null;
    setPhase("steady"); setCaption("steady"); setRestFrame(); paint(); armBlink();
  }
  function present(next, cause = "mood") {
    if (drag?.moved) { dragTargetCause = cause; return; }
    adoptImmediately(next);
    if (cause === "completion" && available() && motionAllowed()) play("celebrate");
  }
  function setStoryFrame(index) {
    if (!story || !storySprite) return;
    const part = Math.floor(index / 16), frame = index % 16;
    story.frame = index;
    storySprite.style.backgroundImage = `url("${storySprites[story.id][part]}")`;
    storySprite.style.backgroundPosition = framePosition(frame);
    node.dataset.storyFrame = String(index); node.dataset.storyPart = String(part + 1);
    const actor = index < (story.id === "cow-to-horse" ? 30 : 36) ? story.from : story.to;
    if (displayed?.character !== actor) { displayed = { ...displayed, character: actor }; paintArtwork(); }
  }
  function startStory(from, to) {
    if (from === to || !node) return adoptImmediately(desired());
    if (!available() || !motionAllowed() || drag?.moved) {
      adoptImmediately(desired());
      if (available()) { showMoodAfterStory(); paintSpeech(); }
      return;
    }
    cancelPresentation();
    const flow = beginFlow("story"), id = from === "cow" ? "cow-to-horse" : "horse-to-cow";
    story = { id, from, to, frame: 0, started: false };
    node.dataset.story = id; setPhase("story"); setCaption("steady"); paint();
    void (async () => {
      const ready = await Promise.all(storySprites[id].map((url, part) => loadUrl(`story:${id}:${part}`, url)));
      if (!owns(flow) || !story) return;
      if (!ready.every(Boolean) || !available() || !motionAllowed()) return adoptImmediately(desired());
      // Coalesce changes that arrived before any frame was shown; never start a stale film.
      if (getCharacter() !== to) return adoptImmediately(desired());
      story.started = true; storyCount++;
      node.dataset.storyActive = "true"; node.dataset.storyCount = String(storyCount); onStoryChange(true); paintSpeech();
      // All authored beats run. Updates merely replace the latest policy, never queue old films.
      for (const [index, duration] of storyFrames[id]) {
        if (!owns(flow) || !story) return;
        setStoryFrame(index);
        if (!await flow.wait(duration)) return;
      }
      if (!owns(flow)) return;
      const latest = getCharacter(), finishedActor = to;
      clearStory();
      displayed = { ...desired(), character: finishedActor, pose: finishedActor === "horse" ? "checklist" : "settle" };
      showMoodAfterStory(); setRestFrame(); finishFlow(flow); paint();
      if (latest !== finishedActor) startStory(finishedActor, latest);
      else if (queuedInteraction) { queuedInteraction = false; play("wave"); }
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
    // Reserve the absolute scene's side overhang even at rest, so starting a film never moves the dock.
    const sceneMargin = compact ? 8 : 16;
    const fitted = fitDock({ x, y, width, height, viewportWidth: view.width, viewportHeight: view.height, margin: sceneMargin });
    node.style.left = `${fitted.x + view.offsetX}px`; node.style.top = `${fitted.y + view.offsetY}px`; node.dataset.edge = edge;
    if (pendingCompletionMotion && motionAllowed() && !story && !playing) {
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
    node.setAttribute("aria-label", "牛马伙伴"); node.dataset.playing = "false"; node.dataset.motionCount = "0"; node.dataset.completion = "false"; node.dataset.phase = "steady"; node.dataset.caption = "steady"; node.dataset.storyActive = "false"; node.dataset.storyCount = "0";
    node.innerHTML = `<div class="companion-pet-stage"><button type="button" id="sidebar-companion-button" class="sidebar-companion-button" data-sidebar-companion-button aria-describedby="sidebar-companion-speech" aria-expanded="false"><span class="companion-pet-figure" aria-hidden="true"><span class="companion-pet-sprite"></span><img class="companion-pet-fallback" width="136" height="136" alt="" decoding="async" draggable="false"></span><span class="companion-story-sprite" aria-hidden="true"></span></button><button type="button" class="companion-pet-options" aria-label="伙伴选项" aria-expanded="false" aria-controls="companion-pet-menu">${dotsIcon}</button></div><p id="sidebar-companion-speech" class="sidebar-companion-speech" data-companion-message hidden></p><div id="companion-pet-menu" class="companion-pet-menu" aria-label="伙伴选项" hidden><button type="button" data-pet-action="motion"></button><button type="button" data-pet-action="home">回到左下角</button><button type="button" data-pet-action="hide">关闭牛马伙伴</button></div><span class="sr-only" data-pet-announcement role="status" aria-live="polite" aria-atomic="true"></span>`;
    button = node.querySelector("#sidebar-companion-button"); sprite = node.querySelector(".companion-pet-sprite"); image = node.querySelector("img");
    storySprite = node.querySelector(".companion-story-sprite");
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
      if (story) queuedInteraction = true;
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
        drag.moved = true; cancelPresentation({ adoptTarget: true }); setPhase("steady"); closeMenu(); node.dataset.dragging = "true"; node.dataset.action = "drag";
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
    node.dataset.policyCharacter = getCharacter();
    node.dataset.completion = String(Boolean(completion)); node.dataset.count = completion?.count || ""; node.dataset.mode = completion?.mode || "";
    paintArtwork(); paintSpeech(); reposition();
  }
  function update(next) {
    const previousActor = getCharacter(), wasKnown = policyKnown;
    current = next;
    if (next?.ready) {
      policyCharacter = Number.isFinite(next.overdue) ? next.overdue > 0 ? "horse" : "cow" : next.character === "horse" ? "horse" : "cow";
      policyKnown = true;
    }
    if (completion) completion.character = getCharacter();
    if (!wasKnown || !displayed) adoptImmediately(desired());
    else if (story) { paint(); return; }
    else if (policyKnown && previousActor !== getCharacter()) startStory(previousActor, getCharacter());
    else { displayed = desired(); paint(); if (!playing) setRestFrame(); }
    if (!playing) armBlink();
  }
  function showCompletion({ message: copy = "", count = 1, remaining = 1, replay = true } = {}) {
    completion = { character: getCharacter(), message: typeof copy === "string" ? copy : copy?.line || "",
      count, remaining, mode: typeof copy === "object" ? copy?.mode || "ordinary" : "ordinary" };
    suppressed = false; open = true; closeMenu();
    if (story) paintSpeech();
    else if (drag?.moved) dragTargetCause = "completion";
    else {
      displayed = desired(); paint();
      if (replay) {
        pendingCompletionMotion = preferences.animated && !reducedMotion?.matches && !available();
        if (available() && motionAllowed()) play("celebrate");
      }
    }
    paint();
    if (announcement) announcement.textContent = `${typeof copy === "object" ? copy?.title || "任务已完成" : "任务已完成"}。${names[getCharacter()]}说：${completion.message}`;
    return preferences.enabled;
  }
  function endCompletion() {
    if (!completion) return;
    completion = null; pendingCompletionMotion = false;
    if (announcement) announcement.textContent = "";
    open = hovered || node?.contains(doc.activeElement); pinned = false;
    if (story) { paint(); return; }
    if (!node || !available() || !motionAllowed() || drag?.moved) return adoptImmediately(desired());
    const flow = beginFlow("settle"); setCaption("leaving"); paint();
    void (async () => {
      const settled = isChecklist(displayed) ? await flow.wait(180) : await sequence(flow, "settle");
      if (!settled || !owns(flow)) return;
      displayed = desired(); finishFlow(flow); paint();
    })();
  }
  // The fixed dock stays outside app.innerHTML and the mobile sidebar's inert subtree.
  function detach() { slot = null; }
  function mount(nextSlot) {
    if (!nextSlot && !node) return;
    slot = nextSlot; if (!node) makeNode(); setFrame(cell, atlas); paint(); armBlink();
    for (const who of ["cow", "horse"]) for (const sheet of ["lifecycle", "action", "base"]) void loadAtlas(who, sheet);
    if (preferences.enabled && preferences.animated && !reducedMotion?.matches)
      for (const [id, parts] of Object.entries(storySprites)) parts.forEach((url, part) => void loadUrl(`story:${id}:${part}`, url));
  }
  function reset() {
    dismiss({ stop: true }); abortDrag(); current = null; completion = null; displayed = null; clearStory();
    policyKnown = false; policyCharacter = "cow"; storyCount = 0;
    clearTimeout(moodTimer); moodPeek = false;
    pendingCompletionMotion = false; queuedInteraction = false; edge = "left"; dockY = null; userDocked = false; motionCount = 0; suppressed = false;
    if (announcement) announcement.textContent = "";
    if (node) node.dataset.storyCount = "0";
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
  globalThis.window?.addEventListener("resize", () => {
    if (!story) { cancelPresentation({ adoptTarget: true }); abortDrag(); }
    reposition(); armBlink();
  });
  globalThis.visualViewport?.addEventListener("resize", reposition);
  globalThis.visualViewport?.addEventListener("scroll", reposition);
  globalThis.window?.addEventListener("niuma:companion-preferences", event => setPreferences(event.detail || {}));
  reducedMotion?.addEventListener?.("change", () => { pendingCompletionMotion = false; cancelPresentation({ adoptTarget: true }); abortDrag(); paint(); armBlink(); });
  return { mount, detach, update, dismiss, reset, reposition, preferences: setPreferences, showCompletion, endCompletion, getCharacter };
}
