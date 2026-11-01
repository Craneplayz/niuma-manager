/** Real consecutive cells, never a whole-picture wiggle. All sheets use 4 × 4 cells. */
const cells = (indices, duration, atlas) => indices.map(cell => [cell, duration, atlas]);
const actionDuration = [80, 70, 70, 80, 80, 80, 70, 110, 100, 80, 90, 120, 110, 90, 80, 120];
const action = actionDuration.map((duration, cell) => [cell, duration, "action"]);
export const motionFrames = Object.freeze({
  blink: [[0, 120, "lifecycle"], [1, 70, "lifecycle"], [2, 95, "lifecycle"], [3, 110, "lifecycle"]],
  wave: action,
  celebrate: action,
  horseAck: [[5, 70, "action"], [6, 70, "action"], [7, 90, "action"], [8, 90, "action"],
    [9, 85, "action"], [10, 85, "action"], [11, 100, "action"], [12, 120, "action"],
    [11, 65, "action"], [10, 65, "action"], [8, 65, "action"], [6, 65, "action"], [5, 80, "action"]],
  settle: [[4, 100, "lifecycle"], [5, 130, "lifecycle"], [6, 130, "lifecycle"], [7, 140, "lifecycle"]],
  checklist: cells([0, 1, 2, 3, 4, 5], 65, "action"),
  putAway: cells([5, 4, 3, 2, 1, 0], 55, "action"),
  land: cells([14, 15], 110, "lifecycle"),
});
export { storyFrames } from "./companion-story.js";

const fullSequence = (length, duration, holds = {}) => Array.from({ length }, (_, frame) => [frame, holds[frame] ?? duration]);
export const gestureFrames = Object.freeze({
  "cow-dance": fullSequence(32, 90, { 0: 120, 20: 100, 21: 100, 31: 120 }),
  "cow-cake": fullSequence(48, 90, { 8: 130, 16: 140, 20: 140, 31: 140, 36: 140, 42: 130, 47: 140 }),
  "horse-inspect": fullSequence(32, 110, { 0: 120, 12: 160, 21: 180, 23: 160, 31: 170 }),
});
export const idleFrames = Object.freeze({
  "cow-rest": {
    entry: fullSequence(8, 110, { 7: 180 }),
    loop: fullSequence(8, 180).map(([frame, duration]) => [frame + 8, duration]),
    wake: fullSequence(16, 110, { 8: 170, 9: 170, 15: 150 }).map(([frame, duration]) => [frame + 16, duration]),
  },
  "horse-desk": {
    entry: fullSequence(12, 110, { 0: 140, 11: 130 }),
    loop: fullSequence(20, 110, { 0: 150, 5: 140, 10: 180, 11: 220, 13: 160, 19: 150 }).map(([frame, duration]) => [frame + 12, duration]),
    wake: fullSequence(16, 110, { 3: 130, 10: 130, 15: 160 }).map(([frame, duration]) => [frame + 32, duration]),
  },
});
export const gestureChoices = Object.freeze({ cow: ["cow-dance", "cow-cake"], horse: ["horse-inspect", "horse-ack"] });

/** Independent shuffle bags also avoid repeating across a refill boundary. */
export function createGestureBag(random = Math.random) {
  const bags = new Map(), previous = new Map();
  const sample = () => {
    const value = Number(random());
    return Number.isFinite(value) ? Math.max(0, Math.min(.999999999, value)) : .5;
  };
  return {
    next(actor) {
      actor = actor === "horse" ? "horse" : "cow";
      let bag = bags.get(actor);
      if (!bag?.length) {
        bag = [...gestureChoices[actor]];
        for (let i = bag.length - 1; i > 0; i--) {
          const j = Math.floor(sample() * (i + 1));
          [bag[i], bag[j]] = [bag[j], bag[i]];
        }
        if (bag.length > 1 && bag[0] === previous.get(actor)) [bag[0], bag[1]] = [bag[1], bag[0]];
        bags.set(actor, bag);
      }
      const id = bag.shift(); previous.set(actor, id); return id;
    },
    reset() { bags.clear(); previous.clear(); },
  };
}

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
