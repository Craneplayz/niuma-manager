import test from "node:test";
import assert from "node:assert/strict";
import { motionFrames, storyFrames, gestureFrames, idleFrames, createGestureBag, framePosition, fitDock, snapEdge, createMotionTimeline } from "../src/companion-motion.js";

test("completion uses sixteen consecutive authored cells and lifecycle gestures stay finite", () => {
  assert.deepEqual(motionFrames.celebrate.map(([cell]) => cell), Array.from({ length: 16 }, (_, i) => i));
  assert.deepEqual(motionFrames.settle.map(([cell]) => cell), [4, 5, 6, 7]);
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

test("both policy stories contain all 48 authored frames across three atlases and last five to seven seconds", () => {
  assert.deepEqual(Object.keys(storyFrames), ["cow-to-horse", "horse-to-cow"]);
  for (const sequence of Object.values(storyFrames)) {
    assert.deepEqual(sequence.map(([frame]) => frame), Array.from({ length: 48 }, (_, i) => i));
    const duration = sequence.reduce((total, [, ms]) => total + ms, 0);
    assert.ok(duration >= 5000 && duration <= 7000);
    assert.deepEqual([...new Set(sequence.map(([frame]) => Math.floor(frame / 16)))], [0, 1, 2]);
    for (const [, ms] of sequence) assert.ok(ms >= 40 && ms <= 200);
  }
});


test("the task list flies to the cow promptly without cutting the authored story", () => {
  assert.equal(storyFrames["cow-to-horse"].filter(([frame]) => frame >= 3 && frame <= 7).reduce((sum, [, ms]) => sum + ms, 0), 225);
  assert.equal(storyFrames["cow-to-horse"].length, 48);
});

test("actor shuffle bags cover each choice and never repeat across refills", () => {
  for (const random of [() => 0, () => .99999, () => NaN, () => -3, () => 7]) {
    const bag = createGestureBag(random), seen = { cow: [], horse: [] };
    for (let i = 0; i < 30; i++) for (const actor of ["cow", "horse"]) seen[actor].push(bag.next(actor));
    for (const values of Object.values(seen)) {
      for (let i = 1; i < values.length; i++) assert.notEqual(values[i], values[i - 1]);
      for (let i = 0; i < values.length; i += 2) assert.equal(new Set(values.slice(i, i + 2)).size, 2);
    }
    assert.ok(seen.cow.every(id => id.startsWith("cow-")));
    assert.ok(seen.horse.every(id => id.startsWith("horse-")));
    bag.reset(); assert.ok(bag.next("cow").startsWith("cow-"));
  }
});

test("new full gestures run every frame from standing start to standing endpoint", () => {
  for (const [id, frames] of Object.entries(gestureFrames)) {
    const count = id === "cow-cake" ? 48 : 32;
    assert.deepEqual(frames.map(([frame]) => frame), Array.from({ length: count }, (_, i) => i));
    const ms = frames.reduce((total, [, duration]) => total + duration, 0);
    assert.ok(ms >= 2800 && ms <= 4800);
  }
  assert.equal(gestureFrames["cow-dance"].reduce((sum, [, duration]) => sum + duration, 0), 2960);
});

test("idle ranges have separate entry, closed loop and a complete safe exit", () => {
  for (const [id, data] of Object.entries(idleFrames)) {
    const entryEnd = id === "cow-rest" ? 7 : 11, loopEnd = id === "cow-rest" ? 15 : 31, end = id === "cow-rest" ? 31 : 47;
    assert.deepEqual(data.entry.map(([frame]) => frame), Array.from({ length: entryEnd + 1 }, (_, i) => i));
    assert.deepEqual(data.loop.map(([frame]) => frame), Array.from({ length: loopEnd - entryEnd }, (_, i) => entryEnd + 1 + i));
    assert.deepEqual(data.wake.map(([frame]) => frame), Array.from({ length: end - loopEnd }, (_, i) => loopEnd + 1 + i));
    assert.ok(data.loop.reduce((sum, [, duration]) => sum + duration, 0) <= 3500);
  }
});
