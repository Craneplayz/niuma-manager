import { readCompanionPreferences, writeCompanionPreferences } from "./completion-companion.js";
import { motionFrames, storyFrames, gestureFrames, idleFrames, createGestureBag, createMotionTimeline, framePosition, fitDock, snapEdge } from "./companion-motion.js";

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
    new URL("./assets/companions/pet-v5/cow-to-horse-1.webp", import.meta.url).href,
    new URL("./assets/companions/pet-v5/cow-to-horse-2.webp", import.meta.url).href,
    new URL("./assets/companions/pet-v5/cow-to-horse-3.webp", import.meta.url).href,
  ],
  "horse-to-cow": [
    new URL("./assets/companions/pet-v5/horse-to-cow-1.webp", import.meta.url).href,
    new URL("./assets/companions/pet-v5/horse-to-cow-2.webp", import.meta.url).href,
    new URL("./assets/companions/pet-v5/horse-to-cow-3.webp", import.meta.url).href,
  ],
};
const sceneSprites = {
  "cow-dance": [new URL("./assets/companions/pet-v5/cow-dance-1.webp", import.meta.url).href, new URL("./assets/companions/pet-v5/cow-dance-2.webp", import.meta.url).href],
  "cow-cake": [new URL("./assets/companions/pet-v5/cow-cake-1.webp", import.meta.url).href, new URL("./assets/companions/pet-v5/cow-cake-2.webp", import.meta.url).href, new URL("./assets/companions/pet-v5/cow-cake-3.webp", import.meta.url).href],
  "cow-rest": [new URL("./assets/companions/pet-v5/cow-rest-1.webp", import.meta.url).href, new URL("./assets/companions/pet-v5/cow-rest-2.webp", import.meta.url).href],
  "horse-inspect": [new URL("./assets/companions/pet-v5/horse-inspect-1.webp", import.meta.url).href, new URL("./assets/companions/pet-v5/horse-inspect-2.webp", import.meta.url).href],
  "horse-desk": [new URL("./assets/companions/pet-v5/horse-desk-1.webp", import.meta.url).href, new URL("./assets/companions/pet-v5/horse-desk-2.webp", import.meta.url).href, new URL("./assets/companions/pet-v5/horse-desk-3.webp", import.meta.url).href],
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

/** One scheduler owns every performance; updates only replace the latest intent. */
export function createSidebarCompanion({ canInteract = () => true, canShow = () => true,
  isCompact = () => false, onOpen = () => {}, onCompletionDismiss, onStoryChange = () => {},
  onActionChange = () => {}, onGestureChange = () => {}, random = Math.random, idleAfterMs = 90000 } = {}) {
  const doc = document, reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  const bags = createGestureBag(random), idleDelay = Number.isFinite(idleAfterMs) ? Math.max(1, idleAfterMs) : 90000;
  let preferences = readCompanionPreferences(), current = null, completion = null, displayed = null;
  let policyKnown = false, policyCharacter = "cow", storyCount = 0, gestureCount = 0;
  let performance = null, story = null, pendingGesture = null, actionActive = false;
  let node, button, sprite, storySprite, image, speech, announcement, menuButton, menu, slot;
  let open = false, pinned = false, suppressed = false, hovered = false, menuOpen = false, moodPeek = false;
  let idleTimer, blinkTimer, hideTimer, showTimer, moodTimer, captionTimer, clickTimer, expiringCaption = null;
  let lastActivity = Date.now(), cell = 0, atlas = "lifecycle", motionCount = 0;
  let drag = null, edge = "left", dockY = null, userDocked = false, ignoreClick = false;
  const loaded = new Map(), loading = new Map();
  const getCharacter = () => policyKnown ? policyCharacter : current?.character === "horse" ? "horse" : "cow";
  const desired = (actor = getCharacter()) => current ? { ...current, ...(completion || {}), character: actor,
    kind: completion ? "completion" : "mood", pose: actor === "horse" ? "checklist" : current.pose || "settle" } : null;
  const character = () => displayed?.character || getCharacter();
  const motionAllowed = () => preferences.animated && !reducedMotion?.matches && doc.visibilityState !== "hidden";
  const available = () => preferences.enabled && canShow() && canInteract() && Boolean(current || completion);
  const owns = run => performance === run && run.flow.active;
  const sources = id => storySprites[id] || sceneSprites[id];

  function setPhase(phase) { if (node) node.dataset.phase = phase; }
  function setCaption(phase) { if (node) node.dataset.caption = phase; }
  function setActionActive(active) {
    active = Boolean(active);
    if (node) node.dataset.actionActive = String(active);
    if (actionActive === active) return;
    actionActive = active; onActionChange(active);
  }
  function setFrame(next, sheet = atlas) {
    cell = next; atlas = sheet;
    if (!sprite) return;
    sprite.style.backgroundPosition = framePosition(next);
    node.dataset.frame = String(next); node.dataset.atlas = sheet; paintArtwork();
  }
  function setRestFrame() { setFrame(character() === "horse" ? 5 : 0, character() === "horse" ? "action" : "lifecycle"); }
  function stopQuietTimers() { clearTimeout(idleTimer); clearTimeout(blinkTimer); }
  function clearScene() {
    if (!node) return;
    node.dataset.sceneActive = "false"; node.dataset.sceneId = "";
  }
  function notifyGesture(run, phase) {
    if (phase === "start") run.notified = true;
    else if (!run.notified) return;
    else run.notified = false;
    onGestureChange({ id: run.id, phase });
  }
  function cancelPresentation({ adoptTarget = false, preserveUnstartedCompletion = false } = {}) {
    const run = performance;
    // A modal may temporarily hide a saved completion; its controller still owns expiry.
    // Preserve only an unseen intent, never the performance that was already on screen.
    const deferred = preserveUnstartedCompletion && preferences.enabled && motionAllowed() && completion && (!canShow() || !canInteract())
      ? pendingGesture?.reason === "completion" ? pendingGesture
        : run?.kind === "gesture" && !run.started && run.reason === "completion" ? { reason: "completion" } : null
      : null;
    stopQuietTimers(); clearTimeout(showTimer); clearTimeout(hideTimer); clearTimeout(captionTimer); clearTimeout(moodTimer); clearTimeout(clickTimer);
    expiringCaption = null; moodPeek = false; pendingGesture = deferred; ignoreClick = false;
    performance = null; story = null;
    run?.flow.cancel();
    if (run?.notified) notifyGesture(run, "cancel");
    if (run?.kind === "story" && run.started) onStoryChange(false);
    setActionActive(false); clearScene();
    if (node) { node.dataset.playing = "false"; node.dataset.storyActive = "false"; node.dataset.story = ""; node.dataset.idle = ""; node.dataset.pendingGesture = deferred?.reason || ""; }
    setPhase("steady"); setCaption("steady");
    if (adoptTarget) { displayed = desired(); setRestFrame(); paintSpeech(); }
  }
  function abortDrag() {
    const pointerId = drag?.pointerId;
    drag = null;
    if (node) node.dataset.dragging = "false";
    if (pointerId != null) { try { button.releasePointerCapture(pointerId); } catch {} }
  }
  function markActivity() {
    lastActivity = Date.now();
    if (!performance && !drag) armTimers();
  }
  function armTimers() {
    stopQuietTimers();
    if (!node || performance || drag || !available() || !motionAllowed() || !current?.ready) return;
    const delay = Math.max(1, idleDelay - (Date.now() - lastActivity));
    idleTimer = setTimeout(() => {
      if (Date.now() - lastActivity >= idleDelay && !performance && available() && motionAllowed()) startIdle();
      else armTimers();
    }, delay);
    if (character() === "cow" && delay > 12000) blinkTimer = setTimeout(() => startSmallMotion("blink"), 12000);
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
  function loadScene(id) {
    return Promise.all(sources(id).map((url, part) => loadUrl(`full:${id}:${part}`, url))).then(results => results.every(Boolean));
  }
  function paintArtwork() {
    if (!node) return;
    const who = character(), pose = displayed?.pose || "settle";
    const url = fallback[who]?.[pose] || fallback[who]?.settle || fallback.cow.arrive;
    if (image.getAttribute("src") !== url) image.src = url;
    const ready = Boolean(loaded.get(`${who}:${atlas}`));
    sprite.style.backgroundImage = `url("${sprites[who][atlas]}")`;
    node.dataset.spriteReady = String(ready); node.dataset.character = who;
    button.setAttribute("aria-label", `${names[who]}，点击回应或唤醒；方向键移动，Home 回到左侧`);
    if (!ready) void loadAtlas(who, atlas);
  }
  function paintSpeech() {
    if (!node) return;
    const cake = completion && performance?.kind === "gesture" && performance.id === "cow-cake" && performance.started;
    speech.textContent = cake ? "真不错又做完了一项，来块儿蛋糕吧~" : expiringCaption?.message || desired()?.message || "";
    speech.dataset.speaker = story ? "" : names[character()];
    const visible = open || moodPeek || Boolean(completion) || Boolean(story?.started) || Boolean(expiringCaption);
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
    closeMenu();
    if (stop) { cancelPresentation({ adoptTarget: true }); abortDrag(); }
    paintSpeech();
  }
  function reveal({ pin = false } = {}) {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    if (!available() || suppressed || drag) return;
    onOpen(); open = true; pinned ||= pin; paintSpeech();
  }
  function beginRun(kind, id, actor, foreground = false) {
    stopQuietTimers();
    const run = { kind, id, actor, flow: createMotionTimeline(), started: false, notified: false, wakeRequested: false };
    performance = run;
    node.dataset.playing = "true"; node.dataset.action = id;
    node.dataset.motionCount = String(++motionCount); setPhase(kind);
    setActionActive(foreground); return run;
  }
  function setFullFrame(run, index) {
    if (!owns(run)) return;
    const part = Math.floor(index / 16), frame = index % 16;
    storySprite.style.backgroundImage = `url("${sources(run.id)[part]}")`;
    storySprite.style.backgroundPosition = framePosition(frame);
    node.dataset.sceneActive = "true"; node.dataset.sceneId = run.id;
    node.dataset.sceneFrame = String(index); node.dataset.scenePart = String(part + 1);
    if (run.kind === "story") {
      node.dataset.storyFrame = String(index); node.dataset.storyPart = String(part + 1);
      const actor = index < (run.id === "cow-to-horse" ? 30 : 36) ? run.actor : run.to;
      if (displayed?.character !== actor) { displayed = desired(actor); paintArtwork(); }
    }
  }
  async function fullSequence(run, frames) {
    for (const [frame, duration] of frames) {
      if (!owns(run)) return false;
      setFullFrame(run, frame);
      if (!await run.flow.wait(duration)) return false;
    }
    return owns(run);
  }
  async function smallSequence(run, name) {
    return run.flow.sequence(motionFrames[name], (frame, sheet) => { if (owns(run)) setFrame(frame, sheet); });
  }
  function showMoodAfterStory() {
    clearTimeout(moodTimer); moodPeek = true;
    moodTimer = setTimeout(() => { moodPeek = false; paintSpeech(); }, 4000);
  }
  function finishRun(run, actor = run.actor) {
    if (!owns(run)) return;
    run.flow.cancel(); performance = null; story = null;
    if (run.notified) notifyGesture(run, "end");
    clearScene();
    displayed = desired(actor); setRestFrame();
    node.dataset.playing = "false"; node.dataset.storyActive = "false"; node.dataset.story = ""; node.dataset.idle = "";
    setPhase("steady"); setCaption("steady"); paint();
    if (run.kind === "story" && run.started) { showMoodAfterStory(); onStoryChange(false); }
    setActionActive(false);
    if (run.kind !== "blink") lastActivity = Date.now();
    paintSpeech(); drain();
  }
  function queueGesture(reason) {
    if (reason === "click" && pendingGesture?.reason === "completion") return;
    pendingGesture = { reason };
    if (node) node.dataset.pendingGesture = reason;
  }
  function requestWake() {
    if (performance?.kind !== "idle") return;
    performance.wakeRequested = true;
    // Waiting for the current loop's safe endpoint belongs to the foreground wake request.
    setActionActive(true);
  }
  function abandonUnstarted(run, intent = pendingGesture) {
    const actor = run.actor;
    cancelPresentation(); displayed = desired(actor); setRestFrame();
    pendingGesture = intent;
    if (node) node.dataset.pendingGesture = intent?.reason || "";
    lastActivity = Date.now(); paint(); drain();
  }
  function drain() {
    if (!node || drag) return;
    if (!available() || !motionAllowed()) {
      cancelPresentation({ adoptTarget: true, preserveUnstartedCompletion: true }); return;
    }
    if (performance) {
      const run = performance, needsStory = character() !== getCharacter();
      if (!run.started && (run.kind === "idle" && (needsStory || pendingGesture || run.wakeRequested) || run.kind === "gesture" && needsStory)) {
        const intent = pendingGesture || (run.kind === "gesture" ? { reason: run.reason } : null);
        abandonUnstarted(run, intent); return;
      }
      if (performance.kind === "idle" && (pendingGesture || character() !== getCharacter())) requestWake();
      return;
    }
    if (displayed?.character !== getCharacter()) { startStory(character(), getCharacter()); return; }
    if (pendingGesture) {
      const intent = pendingGesture; pendingGesture = null; node.dataset.pendingGesture = "";
      startGesture(intent); return;
    }
    armTimers();
  }
  async function startGesture(intent) {
    if (performance || !available() || !motionAllowed()) return;
    const actor = getCharacter(), selected = bags.next(actor);
    displayed = desired(actor);
    const run = beginRun("gesture", selected, actor, true);
    run.reason = intent.reason;
    let ready = selected === "horse-ack" ? await loadAtlas(actor, "action") : await loadScene(selected);
    if (!owns(run)) return;
    if (!ready) {
      run.id = actor === "horse" ? "horse-ack" : "cow-clap";
      ready = await loadAtlas(actor, "action");
      if (!owns(run)) return;
      node.dataset.assetFallback = selected;
    }
    if (!ready) { finishRun(run); return; }
    run.started = true; gestureCount++;
    node.dataset.action = run.id; node.dataset.gestureCount = String(gestureCount);
    notifyGesture(run, "start"); paintSpeech();
    const ok = run.id === "horse-ack" || run.id === "cow-clap"
      ? await smallSequence(run, run.id === "horse-ack" ? "horseAck" : "celebrate")
      : await fullSequence(run, gestureFrames[run.id]);
    if (ok) finishRun(run);
  }
  async function startSmallMotion(id) {
    if (performance || !available() || !motionAllowed() || drag) return;
    const actor = character(), run = beginRun(id, id, actor);
    if (!await loadAtlas(actor, "lifecycle") || !owns(run)) { if (owns(run)) finishRun(run); return; }
    if (await smallSequence(run, id)) finishRun(run);
  }
  async function startIdle() {
    if (performance || !available() || !motionAllowed() || drag) return;
    const actor = character(), id = actor === "cow" ? "cow-rest" : "horse-desk";
    const run = beginRun("idle", id, actor, true), frames = idleFrames[id];
    node.dataset.idle = id; setPhase("idle-entry");
    if (!await loadScene(id) || !owns(run)) { if (owns(run)) finishRun(run); return; }
    run.started = true;
    if (!await fullSequence(run, frames.entry)) return;
    while (owns(run) && !run.wakeRequested) {
      setPhase("idle-loop"); setActionActive(false);
      if (!await fullSequence(run, frames.loop)) return;
    }
    if (!owns(run)) return;
    setPhase("wake"); setActionActive(true);
    if (await fullSequence(run, frames.wake)) finishRun(run);
  }
  async function startStory(from, to) {
    if (performance || from === to) return drain();
    const id = from === "cow" ? "cow-to-horse" : "horse-to-cow";
    const run = beginRun("story", id, from, true); run.to = to; story = run;
    node.dataset.story = id; setCaption("steady"); paint();
    const ready = await loadScene(id);
    if (!owns(run)) return;
    if (!ready || !available() || !motionAllowed()) {
      cancelPresentation({ adoptTarget: true }); paint(); armTimers(); return;
    }
    if (getCharacter() !== to) { abandonUnstarted(run); return; }
    run.started = true; storyCount++;
    node.dataset.storyActive = "true"; node.dataset.storyCount = String(storyCount);
    onStoryChange(true); setActionActive(false); paintSpeech();
    if (await fullSequence(run, storyFrames[id])) finishRun(run, to);
  }
  function handlePetClick() {
    markActivity(); suppressed = false; reveal({ pin: true });
    if (performance?.kind === "idle") { requestWake(); drain(); return; }
    queueGesture("click"); drain();
  }
  function viewport() {
    const visual = globalThis.visualViewport;
    return { width: visual?.width || innerWidth, height: visual?.height || innerHeight,
      offsetX: visual?.offsetLeft || 0, offsetY: visual?.offsetTop || 0 };
  }
  function reposition() {
    if (!node) return;
    const wasHidden = node.hidden, shown = available(); node.hidden = !shown; slot?.setAttribute("data-visible", String(shown));
    if (!shown) { cancelPresentation({ adoptTarget: true, preserveUnstartedCompletion: true }); abortDrag(); return; }
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
    if (wasHidden && !performance) drain();
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
    if (!cancelled) startSmallMotion("land");
    else { setRestFrame(); drain(); }
    clearTimeout(clickTimer); clickTimer = setTimeout(() => { ignoreClick = false; }, 0);
  }
  function updateSharedPreference(value) {
    preferences = writeCompanionPreferences({ ...preferences, ...value });
    globalThis.window?.dispatchEvent(new CustomEvent("niuma:companion-preferences", { detail: { ...preferences } }));
  }
  function makeNode() {
    node = doc.createElement("section"); node.id = "sidebar-companion"; node.className = "sidebar-companion";
    node.setAttribute("aria-label", "牛马伙伴"); node.dataset.playing = "false"; node.dataset.motionCount = "0"; node.dataset.completion = "false"; node.dataset.phase = "steady"; node.dataset.caption = "steady"; node.dataset.storyActive = "false"; node.dataset.storyCount = "0"; node.dataset.sceneActive = "false"; node.dataset.actionActive = "false"; node.dataset.gestureCount = "0";
    node.innerHTML = `<div class="companion-pet-stage"><button type="button" id="sidebar-companion-button" class="sidebar-companion-button" data-sidebar-companion-button aria-describedby="sidebar-companion-speech" aria-expanded="false"><span class="companion-pet-figure" aria-hidden="true"><span class="companion-pet-sprite"></span><img class="companion-pet-fallback" width="136" height="136" alt="" decoding="async" draggable="false"></span><span class="companion-story-sprite companion-scene-sprite" aria-hidden="true"></span></button><button type="button" class="companion-pet-options" aria-label="伙伴选项" aria-expanded="false" aria-controls="companion-pet-menu">${dotsIcon}</button></div><p id="sidebar-companion-speech" class="sidebar-companion-speech" data-companion-message hidden></p><div id="companion-pet-menu" class="companion-pet-menu" aria-label="伙伴选项" hidden><button type="button" data-pet-action="motion"></button><button type="button" data-pet-action="home">回到左下角</button><button type="button" data-pet-action="hide">关闭牛马伙伴</button></div><span class="sr-only" data-pet-announcement role="status" aria-live="polite" aria-atomic="true"></span>`;
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
      handlePetClick();
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
    node.dataset.policyCharacter = getCharacter(); node.dataset.completion = String(Boolean(completion));
    node.dataset.count = completion?.count || ""; node.dataset.mode = completion?.mode || "";
    paintArtwork(); paintSpeech(); reposition();
  }
  function adoptImmediately(next) {
    cancelPresentation(); displayed = next ? { ...next } : null;
    setRestFrame(); paint(); armTimers();
  }
  function update(next) {
    const wasKnown = policyKnown; current = next;
    if (next?.ready) {
      policyCharacter = Number.isFinite(next.overdue) ? next.overdue > 0 ? "horse" : "cow" : next.character === "horse" ? "horse" : "cow";
      policyKnown = true;
    }
    if (completion) completion.character = getCharacter();
    if (!wasKnown || !displayed) { adoptImmediately(desired()); return; }
    if (!performance && character() === getCharacter()) { displayed = desired(); setRestFrame(); }
    paint(); drain();
  }
  function showCompletion({ message: copy = "", count = 1, remaining = 1, replay = true } = {}) {
    markActivity(); clearTimeout(captionTimer); expiringCaption = null; setCaption("steady");
    completion = { character: getCharacter(), message: typeof copy === "string" ? copy : copy?.line || "",
      count, remaining, mode: typeof copy === "object" ? copy?.mode || "ordinary" : "ordinary" };
    suppressed = false; open = true; closeMenu();
    // A merged count or voice refresh does not append a second performance to the same burst.
    if (replay) queueGesture("completion");
    if (!performance && character() === getCharacter()) displayed = desired();
    paint(); drain();
    if (announcement) announcement.textContent = `${typeof copy === "object" ? copy?.title || "任务已完成" : "任务已完成"}。${names[getCharacter()]}说：${completion.message}`;
    return preferences.enabled;
  }
  function endCompletion() {
    if (!completion) return;
    const previous = { ...completion };
    completion = null;
    if (pendingGesture?.reason === "completion") { pendingGesture = null; if (node) node.dataset.pendingGesture = ""; }
    if (announcement) announcement.textContent = "";
    open = hovered || Boolean(node?.contains(doc.activeElement)); pinned = false;
    if (!performance) {
      displayed = desired();
      if (!open && !moodPeek && motionAllowed()) {
        expiringCaption = previous; setCaption("leaving"); clearTimeout(captionTimer);
        captionTimer = setTimeout(() => { expiringCaption = null; setCaption("steady"); paintSpeech(); }, 180);
      }
    }
    paint();
  }
  function play(kind) {
    if (kind === "land" || kind === "blink") { startSmallMotion(kind); return; }
    if (performance?.kind === "idle") { requestWake(); return; }
    queueGesture("click"); drain();
  }
  function detach() { slot = null; }
  function mount(nextSlot) {
    if (!nextSlot && !node) return;
    slot = nextSlot; if (!node) makeNode();
    if (!performance) setRestFrame(); paint();
    // Only the two small resident atlases are warm; large v5 scenes load on demand.
    for (const actor of ["cow", "horse"]) for (const sheet of ["action", "lifecycle"]) void loadAtlas(actor, sheet);
    drain();
  }
  function reset() {
    cancelPresentation(); abortDrag(); clearTimeout(clickTimer);
    current = null; completion = null; displayed = null; policyKnown = false; policyCharacter = "cow";
    storyCount = 0; gestureCount = 0; pendingGesture = null; bags.reset();
    lastActivity = Date.now(); edge = "left"; dockY = null; userDocked = false; motionCount = 0; suppressed = false;
    if (announcement) announcement.textContent = "";
    if (node) { node.dataset.storyCount = "0"; node.dataset.gestureCount = "0"; node.dataset.pendingGesture = ""; }
    paint();
  }
  function setPreferences(value) {
    preferences = { ...preferences, ...value };
    if (!motionAllowed() || !preferences.enabled) { cancelPresentation({ adoptTarget: true }); abortDrag(); }
    paint(); if (available() && motionAllowed()) { lastActivity = Date.now(); drain(); }
  }
  // Passing pointers never affect the clock, look direction, sleeping pose, or loop.
  for (const event of ["pointerdown", "keydown", "input", "wheel"]) doc.addEventListener(event, markActivity, { capture: true, passive: true });
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
    else { lastActivity = Date.now(); reposition(); drain(); }
  });
  globalThis.window?.addEventListener("resize", () => {
    if (drag) { cancelPresentation({ adoptTarget: true }); abortDrag(); }
    reposition(); if (!performance) drain();
  });
  globalThis.visualViewport?.addEventListener("resize", reposition);
  globalThis.visualViewport?.addEventListener("scroll", reposition);
  globalThis.window?.addEventListener("niuma:companion-preferences", event => setPreferences(event.detail || {}));
  reducedMotion?.addEventListener?.("change", () => {
    // A release event can arrive after a new performance has already started.
    // Only entering reduced motion is an interrupt; leaving it resumes scheduling.
    if (reducedMotion.matches) { cancelPresentation({ adoptTarget: true }); abortDrag(); }
    lastActivity = Date.now(); paint(); drain();
  });
  return { mount, detach, update, dismiss, reset, reposition, preferences: setPreferences, showCompletion, endCompletion, getCharacter };
}
