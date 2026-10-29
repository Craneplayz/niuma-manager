/** One latest-operation undo, independent of the optional mascot. */
export function createUndoFeedback({ escapeHtml, icon, accountVersion = () => 0,
  onError = () => {}, onUndo = () => {}, getContainer = () => document.body } = {}) {
  const doc = document;
  const esc = escapeHtml || (value => String(value).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]));
  const undoIcon = icon?.('undo') || '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m8 4-5 5 5 5M3 9h10a7 7 0 0 1 0 14" transform="translate(0 -2)"/></svg>';
  const root = () => doc.querySelector('#toast');
  let active = null, timer, deadline = 0, remaining = 7000;
  function stop() { clearTimeout(timer); timer = null; }
  function pause() {
    if (deadline) remaining = Math.max(0, deadline - Date.now());
    deadline = 0;
    stop();
  }
  function paused() {
    const el = root();
    return doc.visibilityState === 'hidden' || Boolean(el &&
      (el.matches?.(':hover') || (doc.activeElement && el.contains?.(doc.activeElement))));
  }
  function arm() {
    if (deadline) remaining = Math.max(0, deadline - Date.now());
    deadline = 0;
    stop();
    if (!active || paused()) return;
    deadline = Date.now() + remaining;
    timer = setTimeout(clear, remaining);
  }
  function detach() {
    const el = root();
    if (el?.parentElement && el.parentElement !== doc.body) doc.body.append(el);
  }
  function clear() {
    stop(); deadline = 0; active = null;
    const el = root();
    if (el?.classList.contains?.('undo-toast')) {
      el.classList.remove('visible', 'undo-toast');
      el.innerHTML = '';
      el.removeAttribute('aria-label');
      el.setAttribute('role', 'status');
      el.setAttribute('aria-live', 'polite');
      el.style.removeProperty('--undo-bottom');
      detach();
    }
  }
  function relocate() {
    if (!active) return;
    const el = root(), container = getContainer() || doc.body;
    if (!el) return;
    if (el.parentElement !== container) container.append(el);
    el.style.removeProperty?.('--undo-bottom');
    const height = globalThis.innerHeight;
    const footer = container !== doc.body && container.querySelector('.drawer-footer');
    const rating = container === doc.body && doc.querySelector('#usage-rating:not(.is-closing)');
    const obstacles = [footer, rating].filter(Boolean);
    if (!Number.isFinite(height)) return;
    const rect = el.getBoundingClientRect();
    for (const obstacle of obstacles) {
      const b = obstacle.getBoundingClientRect();
      if (rect.right > b.left && rect.left < b.right && rect.bottom > b.top - 12)
        el.style.setProperty('--undo-bottom', `${height - b.top + 12}px`);
    }
  }
  function show({ message = '操作已完成', undo, epoch = accountVersion() } = {}) {
    clear();
    if (typeof undo !== 'function' || epoch !== accountVersion()) return false;
    const el = root();
    if (!el) return false;
    const entry = active = { undo, epoch };
    remaining = 7000;
    el.classList.add('visible', 'undo-toast');
    el.removeAttribute('role'); el.removeAttribute('aria-live');
    el.innerHTML = `<span class="sr-only" role="status">${esc(message)}，可以撤回最近一次操作。</span><button type="button" id="toast-undo" aria-label="撤回最近一次操作" aria-describedby="undo-description">${undoIcon}</button><span id="undo-description" class="undo-description" role="tooltip">${esc(message)}<small>撤回最近一次操作</small></span>`;
    const button = el.querySelector('button');
    button.onclick = async () => {
      if (active !== entry) return;
      if (epoch !== accountVersion()) return clear();
      clear(); onUndo();
      try { await undo(); }
      catch (error) { if (epoch === accountVersion()) onError(error); }
    };
    el.onpointerenter = pause; el.onpointerleave = arm;
    el.onfocusin = pause; el.onfocusout = () => queueMicrotask(arm);
    relocate(); arm();
    return true;
  }
  doc.addEventListener('visibilitychange', () => doc.visibilityState === 'hidden' ? pause() : arm());
  globalThis.window?.addEventListener('resize', relocate);
  return { show, clear, detach, relocate };
}
