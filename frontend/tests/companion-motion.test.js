import test from "node:test";
import assert from "node:assert/strict";
import { motionFrames, framePosition, lookFrame, fitDock, snapEdge } from "../src/companion-motion.js";

test("each action uses authored limb frames, is finite and remains inside the atlas", () => {
  assert.ok(new Set(motionFrames.celebrate.map(([cell]) => cell)).size >= 5);
  assert.ok(new Set(motionFrames.wave.map(([cell]) => cell)).size >= 4);
  for (const frames of Object.values(motionFrames)) {
    for (const [cell, duration] of frames) {
      assert.ok(Number.isInteger(cell) && cell >= 0 && cell < 16);
      assert.ok(duration > 0 && duration < 400);
    }
    assert.ok(frames.reduce((total, [, duration]) => total + duration, 0) < 2000);
  }
  assert.equal(framePosition(0), "0% 0%");
  assert.equal(framePosition(15), "100% 100%");
  assert.equal(framePosition(-2), "0% 0%");
  assert.equal(framePosition(50), "100% 100%");
});

test("near-pointer gaze chooses direction and returns to the actual mood outside its radius", () => {
  assert.equal(lookFrame(-100, 20), 2);
  assert.equal(lookFrame(100, 20), 3);
  assert.equal(lookFrame(5, -100), 4);
  assert.equal(lookFrame(5, 100), 5);
  assert.equal(lookFrame(300, 0, 12), 12);
  assert.equal(lookFrame(0, 0, 12), 12);
  assert.equal(lookFrame(NaN, 0, 12), 12);
});

test("dragging and viewport changes keep the whole character and caption reachable", () => {
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
