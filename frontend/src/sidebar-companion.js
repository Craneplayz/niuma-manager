import { readCompanionPreferences } from "./completion-companion.js";

const artwork = {
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
  },
};

// Keep the actual button and its motion alive while the app rebuilds its sidebar.
export function createSidebarCompanion({ canInteract = () => true, isCompact = () => false, onOpen = () => {} } = {}) {
  const doc = document;
  const reducedMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  let current = null, node = null, button = null, image = null, speech = null;
  let showTimer, hideTimer, motionTimer, animation, motionCount = 0;
  let open = false, pinned = false, suppressed = false, reparenting = false, restoringFocus = false;
  let pendingCelebration = false, preferences = readCompanionPreferences();

  function stopMotion() {
    clearTimeout(motionTimer);
    animation?.cancel();
    animation = null;
    if (node) node.dataset.playing = "false";
  }
  function motionAllowed() {
    return preferences.animated && !reducedMotion?.matches && doc.visibilityState !== "hidden";
  }
  function play() {
    if (!node || !motionAllowed() || !canInteract() || !button.getClientRects().length) return;
    stopMotion();
    pendingCelebration = false;
    node.dataset.playing = "true";
    node.dataset.motionCount = String(++motionCount);
    const dancing = current?.key === "progress";
    const frames = dancing ? [
      { transform: "translateX(0) rotate(0)" },
      { transform: "translateX(-3px) rotate(-6deg)", offset: .22 },
      { transform: "translateX(3px) rotate(5deg)", offset: .48 },
      { transform: "translateX(-2px) rotate(-3deg)", offset: .72 },
      { transform: "translateX(0) rotate(0)" },
    ] : [
      { transform: "rotate(0) translateY(0)" },
      { transform: `rotate(${current?.character === "horse" ? 3 : -3}deg) translateY(-2px)`, offset: .42 },
      { transform: "rotate(0) translateY(0)" },
    ];
    const duration = dancing ? 1200 : 720;
    image.style.transformOrigin = dancing ? "50% 35%" : "50% 85%";
    animation = image.animate?.(frames, { duration, easing: "cubic-bezier(.22,.7,.3,1)", iterations: 1 });
    const finish = () => {
      animation = null;
      if (node) node.dataset.playing = "false";
      clearTimeout(motionTimer);
    };
    if (animation) animation.onfinish = finish;
    motionTimer = setTimeout(finish, duration + 80);
  }
  function dismiss({ suppress = false, stop = false } = {}) {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    open = false;
    pinned = false;
    if (suppress) suppressed = true;
    if (speech) speech.hidden = true;
    button?.setAttribute("aria-expanded", "false");
    if (node) node.dataset.open = "false";
    if (stop) stopMotion();
  }
  function reposition() {
    if (!node) return;
    const compact = isCompact();
    node.dataset.compact = String(compact);
    node.parentElement?.setAttribute("data-compact", String(compact));
    node.dataset.animated = String(motionAllowed());
    if (reparenting || !node.isConnected) return;
    if (!canInteract() || !button.getClientRects().length) return dismiss({ stop: true });
    if (pendingCelebration) play();
    if (!open || !speech) return;
    const sidebar = doc.querySelector("#sidebar");
    const rail = sidebar.getBoundingClientRect(), anchor = button.getBoundingClientRect();
    const collapsed = rail.width < 100;
    const viewportWidth = globalThis.innerWidth, viewportHeight = globalThis.innerHeight;
    speech.dataset.compact = String(compact);
    speech.style.maxWidth = `${Math.min(collapsed ? 240 : rail.width - 24, viewportWidth - 24)}px`;
    speech.style.width = `${collapsed ? Math.min(240, viewportWidth - rail.right - 20) : rail.width - 24}px`;
    const tip = speech.getBoundingClientRect();
    let left = collapsed ? rail.right + 8 : rail.left + 12;
    let top = anchor.top - tip.height - 8;
    if (collapsed) top = anchor.top + (anchor.height - tip.height) / 2;
    else {
      const navBottom = sidebar.querySelector(".task-nav")?.getBoundingClientRect().bottom || rail.top;
      const accountTop = sidebar.querySelector(".sidebar-bottom")?.getBoundingClientRect().top || rail.bottom;
      // In compact rails the bubble uses the free area above the account row.
      top = Math.min(Math.max(top, navBottom + 8), accountTop - tip.height - 8);
      if (top < navBottom + 4) {
        // Extremely short windows keep navigation reachable and allow the rail to scroll.
        top = Math.max(8, anchor.top - tip.height - 8);
      }
    }
    left = Math.max(8, Math.min(left, viewportWidth - tip.width - 8));
    top = Math.max(8, Math.min(top, viewportHeight - tip.height - 8));
    speech.style.left = `${left}px`;
    speech.style.top = `${top}px`;
  }
  function reveal(replay = true) {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    if (!node || suppressed || !canInteract() || !button.getClientRects().length) return;
    onOpen();
    open = true;
    speech.hidden = false;
    button.setAttribute("aria-expanded", "true");
    node.dataset.open = "true";
    if (replay) pendingCelebration = false;
    reposition();
    if (replay) play();
  }
  function enter(event) {
    if (reparenting) return;
    if (event.pointerType === "touch" || suppressed) return;
    clearTimeout(hideTimer);
    if (!open) showTimer = setTimeout(() => reveal(), 150);
  }
  function leave() {
    if (reparenting) return;
    clearTimeout(showTimer);
    suppressed = false;
    if (!pinned && doc.activeElement !== button) hideTimer = setTimeout(() => dismiss(), 180);
  }
  function makeNode() {
    node = doc.createElement("div");
    node.id = "sidebar-companion";
    node.className = "sidebar-companion";
    node.dataset.playing = "false";
    node.dataset.motionCount = "0";
    node.innerHTML = '<button type="button" id="sidebar-companion-button" class="sidebar-companion-button" data-sidebar-companion-button aria-describedby="sidebar-companion-speech" aria-expanded="false"><img width="112" height="112" alt="" aria-hidden="true" decoding="async"></button>';
    button = node.querySelector("button");
    image = node.querySelector("img");
    speech = doc.createElement("p");
    speech.id = "sidebar-companion-speech";
    speech.className = "sidebar-companion-speech";
    speech.setAttribute("role", "tooltip");
    speech.hidden = true;
    doc.body.append(speech);
    node.addEventListener("pointerenter", enter);
    node.addEventListener("pointerleave", leave);
    speech.addEventListener("pointerenter", () => clearTimeout(hideTimer));
    speech.addEventListener("pointerleave", leave);
    button.addEventListener("focus", () => {
      if (restoringFocus) return;
      suppressed = false;
      reveal();
    });
    button.addEventListener("blur", () => { if (!reparenting) { suppressed = false; if (!pinned) dismiss(); } });
    button.addEventListener("click", event => {
      event.stopPropagation();
      suppressed = false;
      if (pinned) dismiss();
      else { pinned = true; reveal(); }
    });
  }
  function paint() {
    if (!node || !current) return;
    node.dataset.state = current.key;
    node.dataset.character = current.character;
    node.dataset.ready = String(current.ready);
    node.dataset.overdue = current.overdue ?? "";
    node.dataset.completedToday = current.completedToday ?? "";
    const url = artwork[current.character]?.[current.pose] || artwork.cow.arrive;
    if (image.getAttribute("src") !== url) image.src = url;
    speech.textContent = current.message;
    button.setAttribute("aria-label", `${current.character === "horse" ? "马马" : "牛牛"}的心情`);
    reposition();
  }
  function update(next) {
    const enteringCelebration = next.key === "progress" && current?.key !== "progress";
    const changedMode = current?.key !== next.key;
    current = next;
    if (changedMode) stopMotion();
    if (enteringCelebration) pendingCelebration = true;
    else if (next.key !== "progress") pendingCelebration = false;
    paint();
  }
  function detach() {
    if (!node?.parentElement) return;
    restoringFocus = doc.activeElement === button;
    reparenting = true;
    node.remove();
  }
  function mount(slot) {
    if (!slot) return;
    if (!node) makeNode();
    slot.append(node);
    reparenting = false;
    paint();
    queueMicrotask(() => { restoringFocus = false; });
  }
  function reset() {
    dismiss({ stop: true });
    current = null;
    pendingCelebration = false;
    motionCount = 0;
    suppressed = false;
  }
  function setPreferences(value) {
    preferences = { ...preferences, ...value };
    if (!motionAllowed()) stopMotion();
    if (node) node.dataset.animated = String(motionAllowed());
  }
  doc.addEventListener("click", event => {
    if (!node?.contains(event.target) && !speech?.contains(event.target)) dismiss();
  }, true);
  doc.addEventListener("keydown", event => {
    if (event.key === "Escape" && open && canInteract()) {
      event.preventDefault();
      event.stopImmediatePropagation();
      dismiss({ suppress: true });
    }
  }, true);
  doc.addEventListener("scroll", event => {
    if (open) reposition();
  }, true);
  doc.addEventListener("visibilitychange", () => {
    if (doc.visibilityState === "hidden") dismiss({ stop: true });
  });
  globalThis.window?.addEventListener("resize", () => { dismiss(); reposition(); });
  globalThis.window?.addEventListener("niuma:companion-preferences", event => setPreferences(event.detail || {}));
  reducedMotion?.addEventListener?.("change", () => { if (!motionAllowed()) stopMotion(); reposition(); });
  return { mount, detach, update, dismiss, reset, reposition, preferences: setPreferences };
}
