/** Real consecutive cells, never a whole-picture wiggle. All sheets use 4 × 4 cells. */
const cells = (indices, duration, atlas) => indices.map(cell => [cell, duration, atlas]);
const actionDuration = [80, 70, 70, 80, 80, 80, 70, 110, 100, 80, 90, 120, 110, 90, 80, 120];
const action = actionDuration.map((duration, cell) => [cell, duration, "action"]);
export const motionFrames = Object.freeze({
  blink: [[0, 120, "lifecycle"], [1, 70, "lifecycle"], [2, 95, "lifecycle"], [3, 110, "lifecycle"]],
  wave: action,
  celebrate: action,
  settle: [[4, 100, "lifecycle"], [5, 130, "lifecycle"], [6, 130, "lifecycle"], [7, 140, "lifecycle"]],
  depart: [[8, 100, "lifecycle"], [9, 110, "lifecycle"], [10, 120, "lifecycle"], [11, 140, "lifecycle"]],
  arrive: [[12, 110, "lifecycle"], [13, 130, "lifecycle"], [14, 140, "lifecycle"], [15, 150, "lifecycle"]],
  checklist: cells([0, 1, 2, 3, 4, 5], 65, "action"),
  putAway: cells([5, 4, 3, 2, 1, 0], 55, "action"),
  land: cells([14, 15], 110, "lifecycle"),
});
export const presentationDurations = Object.freeze({ caption: 180, leave: 150, enter: 220 });

/** Every pending wait resolves on cancellation, so abandoned async flows cannot repaint. */
export function createMotionTimeline({ setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let cancelled = false;
  const pending = new Map();
  const wait = duration => new Promise(resolve => {
    if (cancelled) return resolve(false);
    const id = setTimer(() => { pending.delete(id); resolve(!cancelled); }, Math.max(0, duration));
    pending.set(id, resolve);
  });
  return {
    get active() { return !cancelled; },
    wait,
    async sequence(frames, paint) {
      for (const [cell, duration, atlas = "lifecycle"] of frames) {
        if (cancelled) return false;
        paint(cell, atlas);
        if (!await wait(duration)) return false;
      }
      return !cancelled;
    },
    cancel() {
      cancelled = true;
      for (const [id, resolve] of pending) { clearTimer(id); resolve(false); }
      pending.clear();
    },
  };
}
export function framePosition(frame) {
  const cell = Math.min(15, Math.max(0, Math.trunc(Number(frame) || 0)));
  return `${cell % 4 * 100 / 3}% ${Math.floor(cell / 4) * 100 / 3}%`;
}
export function fitDock({ x, y, width, height, viewportWidth, viewportHeight, margin = 8 }) {
  const left = Math.max(0, Math.min(margin, (viewportWidth - width) / 2));
  const top = Math.max(0, Math.min(margin, (viewportHeight - height) / 2));
  return {
    x: Math.max(left, Math.min(Number.isFinite(x) ? x : left, viewportWidth - width - left)),
    y: Math.max(top, Math.min(Number.isFinite(y) ? y : top, viewportHeight - height - top)),
  };
}
export const snapEdge = (centerX, viewportWidth) => centerX > viewportWidth / 2 ? "right" : "left";
