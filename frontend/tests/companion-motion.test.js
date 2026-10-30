import test from "node:test";
import assert from "node:assert/strict";
import { motionFrames, framePosition, fitDock, snapEdge, createMotionTimeline } from "../src/companion-motion.js";

test("completion uses sixteen consecutive authored cells and lifecycle gestures stay finite", () => {
  assert.deepEqual(motionFrames.celebrate.map(([cell]) => cell), Array.from({ length: 16 }, (_, i) => i));
  assert.deepEqual(motionFrames.settle.map(([cell]) => cell), [4, 5, 6, 7]);
  assert.deepEqual(motionFrames.depart.map(([cell]) => cell), [8, 9, 10, 11]);
  assert.deepEqual(motionFrames.arrive.map(([cell]) => cell), [12, 13, 14, 15]);
  for (const frames of Object.values(motionFrames)) {
    for (const [cell, duration, atlas] of frames) {
      assert.ok(Number.isInteger(cell) && cell >= 0 && cell < 16);
      assert.ok(duration > 0 && duration < 400);
      assert.ok(["action", "lifecycle"].includes(atlas));
    }
    assert.ok(frames.reduce((total, [, duration]) => total + duration, 0) < 2000);
  }
  assert.equal(framePosition(0), "0% 0%");
  assert.equal(framePosition(15), "100% 100%");
  assert.equal(framePosition(-2), "0% 0%");
  assert.equal(framePosition(50), "100% 100%");
});

test("cancelling a transition resolves every wait and cannot repaint its next frame", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const oldFrames = [], timeline = createMotionTimeline();
  const result = timeline.sequence([[8, 80], [9, 80], [10, 80]], cell => oldFrames.push(cell));
  assert.deepEqual(oldFrames, [8]);
  const waiting = timeline.wait(500);
  timeline.cancel();
  assert.equal(await result, false);
  assert.equal(await waiting, false);
  assert.equal(timeline.active, false);
  t.mock.timers.tick(5000);
  assert.deepEqual(oldFrames, [8]);
});

test("a new completion timeline is independent of the abandoned return", async t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const frames = [], old = createMotionTimeline();
  const stale = old.sequence([[8, 80], [9, 80]], cell => frames.push(`old:${cell}`));
  old.cancel();
  const next = createMotionTimeline();
  const active = next.sequence([[0, 80], [1, 80]], cell => frames.push(`new:${cell}`));
  t.mock.timers.tick(80); await Promise.resolve();
  t.mock.timers.tick(80); await Promise.resolve();
  assert.equal(await stale, false);
  assert.equal(await active, true);
  assert.deepEqual(frames, ["old:8", "new:0", "new:1"]);
  next.cancel();
});

test("dragging and viewport changes keep the character and caption reachable", () => {
  for (const [viewportWidth, viewportHeight, width, height] of [[1440,900,208,200],[390,844,172,168],[320,400,172,168]]) {
    for (const x of [-500, 12, 1500, NaN]) for (const y of [-200, 30, 2000, NaN]) {
      const fitted = fitDock({ x, y, width, height, viewportWidth, viewportHeight });
      assert.ok(fitted.x >= 0 && fitted.y >= 0);
      assert.ok(fitted.x + width <= viewportWidth);
      assert.ok(fitted.y + height <= viewportHeight);
    }
    assert.equal(snapEdge(10, viewportWidth), "left");
    assert.equal(snapEdge(viewportWidth - 10, viewportWidth), "right");
  }
});
