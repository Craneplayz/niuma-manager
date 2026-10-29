import test from "node:test";
import assert from "node:assert/strict";
import { calendarSurfaceRegions, renderCalendarSurface } from "../src/calendar-surface.js";

function area(points) {
  return Math.abs(points.reduce((sum, [x, y], index) => {
    const next = points[(index + 1) % points.length];
    return sum + x * next[1] - next[0] * y;
  }, 0)) / 2;
}

function contains(points, x, y) {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [xi, yi] = points[i], [xj, yj] = points[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function sample(regions, size = 220) {
  const counts = regions.map(() => 0);
  for (let row = 0; row < size; row++) {
    for (let column = 0; column < size; column++) {
      // Noncentral offsets avoid sampling exactly on a shared diagonal.
      const x = (column + 0.371) / size, y = (row + 0.613) / size;
      const hits = regions.flatMap((region, index) => contains(region.points, x, y) ? [index] : []);
      assert.equal(hits.length, 1, `one semantic region at (${x}, ${y})`);
      counts[hits[0]]++;
    }
  }
  return counts.map(count => count / (size * size));
}

function verticalRange(points, x) {
  const intersections = [];
  for (let i = 0; i < points.length; i++) {
    const [x0, y0] = points[i], [x1, y1] = points[(i + 1) % points.length];
    if (x0 === x1 || x < Math.min(x0, x1) || x > Math.max(x0, x1)) continue;
    intersections.push(y0 + (y1 - y0) * (x - x0) / (x1 - x0));
  }
  return intersections.length ? [Math.min(...intersections), Math.max(...intersections)] : null;
}

test("semantic region order stays green, blue, orange and zero categories occupy no space", () => {
  const counts = Object.freeze({ overdue: 1, pending: 2, done: 3, unknown: 100 });
  const regions = calendarSurfaceRegions(counts);
  assert.deepEqual(regions.map(({ kind, count, level }) => ({ kind, count, level })), [
    { kind: "done", count: 3, level: 2 },
    { kind: "pending", count: 2, level: 2 },
    { kind: "overdue", count: 1, level: 1 },
  ]);
  assert.deepEqual(calendarSurfaceRegions({ done: 0, pending: 7, overdue: 0 }).map(region => region.kind), ["pending"]);
  assert.deepEqual(calendarSurfaceRegions({ done: 1, pending: 0, overdue: 7 }).map(region => region.level), [1, 4]);
  assert.equal(regions.at(-1).thresholdTo, 2);
});

for (const [name, counts] of [
  ["one to three", { done: 1, pending: 3 }],
  ["three to two to one", { done: 3, pending: 2, overdue: 1 }],
  ["one to a hundred", { done: 1, overdue: 100 }],
]) test(`${name}: actual SVG polygons have proportional area and sampled points form a complete disjoint partition`, () => {
  const regions = calendarSurfaceRegions(counts);
  const sampled = sample(regions);
  for (const [index, region] of regions.entries()) {
    assert.ok(Math.abs(area(region.points) - region.share) < 2e-8, `${region.kind} exact polygon area`);
    assert.ok(Math.abs(region.area - region.share) < 2e-8, `${region.kind} reported area`);
    assert.ok(Math.abs(sampled[index] - region.share) < 0.002, `${region.kind} sampled area`);
    assert.ok(region.points.every(point => point.every(value => Number.isFinite(value) && value >= 0 && value <= 1)));
    const pathCoordinates = [...region.path.matchAll(/(?:M|L)([\d.]+) ([\d.]+)/g)].map(match => [Number(match[1]), Number(match[2])]);
    assert.deepEqual(pathCoordinates, region.points, "paint, clip and tested geometry use identical coordinates");
  }
  assert.ok(Math.abs(regions.reduce((sum, region) => sum + area(region.points), 0) - 1) < 2e-8);
  assert.deepEqual(calendarSurfaceRegions(counts), regions, "geometry remains deterministic");
});

test("both wavy boundaries are parallel where unclipped and adjacent regions share the exact boundary", () => {
  const regions = calendarSurfaceRegions({ done: 3, pending: 2, overdue: 1 });
  const [done, pending, overdue] = regions;
  assert.equal(done.thresholdTo, pending.thresholdFrom);
  assert.equal(pending.thresholdTo, overdue.thresholdFrom);
  const thickness = pending.thresholdTo - pending.thresholdFrom;
  for (const x of [0.51, 0.6, 0.7, 0.8, 0.9, 0.98]) {
    const green = verticalRange(done.points, x), blue = verticalRange(pending.points, x), orange = verticalRange(overdue.points, x);
    assert.ok(Math.abs(green[1] - blue[0]) < 2e-8);
    assert.ok(Math.abs(blue[1] - orange[0]) < 2e-8);
    assert.ok(Math.abs((blue[1] - blue[0]) - thickness) < 2e-8, "constant threshold separation");
  }
  // The wave is present, rather than silently substituting a straight diagonal.
  assert.ok(Math.abs(verticalRange(done.points, 0.25)[1] - (done.thresholdTo - 0.25)) > 0.03);
});

test("a single status paints the entire unit square; no activity produces a white, undecorated cell", () => {
  for (const kind of ["done", "pending", "overdue"]) {
    const [region] = calendarSurfaceRegions({ [kind]: 5 });
    assert.equal(region.share, 1);
    assert.equal(region.area, 1);
    assert.deepEqual(region.points, [[0, 0], [1, 0], [1, 1], [0, 1]]);
    const html = renderCalendarSurface({ date: "2026-10-29", counts: { [kind]: 5 } });
    assert.equal((html.match(/class="calendar-activity-region"/g) || []).length, 1);
    assert.doesNotMatch(html, /feGaussianBlur/);
  }
  for (const counts of [undefined, null, {}, { done: 0, pending: 0, overdue: 0 }]) {
    assert.deepEqual(calendarSurfaceRegions(counts), []);
    assert.equal(renderCalendarSurface({ date: "2026-10-29", counts }), "");
  }
});

test("invalid counts cannot invent activity, overflow geometry, or mutate supplied values", () => {
  for (const counts of [null, [], "unknown", { done: -2, pending: "4", overdue: Infinity }, { done: NaN, pending: null, overdue: undefined }, { unknown: 5 }]) {
    assert.deepEqual(calendarSurfaceRegions(counts), []);
  }
  assert.equal(calendarSurfaceRegions({ pending: 2.9 })[0].count, 2);
  const veryLarge = calendarSurfaceRegions({ done: Number.MAX_VALUE, overdue: Number.MAX_VALUE });
  assert.ok(veryLarge.every(region => region.count === Number.MAX_SAFE_INTEGER && region.share === 0.5));
  assert.ok(veryLarge.every(region => Number.isFinite(region.area) && Math.abs(region.area - 0.5) < 2e-8));
});

test("surface markup exposes date, status and exact count on clipped sibling buttons with safe unique local IDs", () => {
  const html = renderCalendarSurface({ date: "2026-10-29", mode: "week", counts: { done: 3, pending: 2, overdue: 1 }, selectedKind: "pending" });
  assert.match(html, /class="calendar-activity-surface"[^>]*aria-hidden="true"/);
  assert.equal((html.match(/clipPathUnits="objectBoundingBox"/g) || []).length, 3);
  assert.equal((html.match(/class="calendar-activity-region"/g) || []).length, 3);
  assert.equal((html.match(/tabindex="-1"/g) || []).length, 3);
  assert.match(html, /data-calendar-activity="pending"[^>]*data-calendar-day="2026-10-29"[^>]*data-count="2"[^>]*aria-label="优先显示 2026 年 10 月 29 日，待完成 2 项"[^>]*aria-pressed="true"/);
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, new Set(ids).size);
  assert.ok(ids.every(id => /^calendar-surface-week-2026-10-29-[a-z-]+$/.test(id)));
  for (const reference of [...html.matchAll(/(?:url\(#|href="#)([a-z\d-]+)/g)].map(match => match[1])) assert.ok(ids.includes(reference));
  assert.match(html, /feGaussianBlur/);
  assert.doesNotMatch(html, /<image|https?:|data:image|<script/);
  let buttonDepth = 0;
  for (const [tag] of html.matchAll(/<\/?button\b[^>]*>/g)) {
    if (tag.startsWith("</")) buttonDepth--;
    else { assert.equal(buttonDepth, 0, "region buttons are siblings"); buttonDepth++; }
  }
  assert.equal(buttonDepth, 0);
  assert.notEqual(renderCalendarSurface({ date: "2026-10-29", mode: "month", counts: { done: 1 } }), renderCalendarSurface({ date: "2026-10-29", mode: "week", counts: { done: 1 } }));
});

test("untrusted dates and modes never enter SVG identifiers or event-bearing markup", () => {
  for (const date of [undefined, null, "", "2026-02-30", "2026-2-09", '2026-10-29"><img src=x>', "2026-10-29T00:00:00Z"]) {
    assert.equal(renderCalendarSurface({ date, counts: { done: 1 } }), "");
  }
  const html = renderCalendarSurface({ date: "2026-10-29", mode: '"><svg onload=alert(1)>', counts: { done: 1 }, selectedKind: '\" onfocus=alert(1)' });
  assert.match(html, /calendar-surface-month-2026-10-29-done-clip/);
  assert.doesNotMatch(html, /onload|onfocus|alert/);
});
