import { activityLevel } from "./calendar-activity.js";
import { shiftDay } from "./task-model.js";

const KINDS = [["done", "已完成"], ["pending", "待完成"], ["overdue", "已逾期"]];
const AMPLITUDE = 0.045;
const STEPS = 48;
// A common sampled wave keeps every boundary parallel. At normal calendar sizes,
// its maximum deviation from the sine is well below one rendered pixel.
const WAVE = Array.from({ length: STEPS + 1 }, (_, index) => {
  const x = index / STEPS;
  return [x, -x + AMPLITUDE * Math.sin(2 * Math.PI * x)];
});
const clamp = value => Math.max(0, Math.min(1, value));
const coordinate = value => Number(clamp(value).toFixed(9));
const countValue = value => Number.isFinite(value)
  ? Math.min(Number.MAX_SAFE_INTEGER, Math.max(0, Math.trunc(value))) : 0;

function append(points, point) {
  const last = points.at(-1);
  if (last && last[0] === point[0] && last[1] === point[1]) return;
  const previous = points.at(-2);
  if (previous && (point[1] === 0 || point[1] === 1) &&
      previous[1] === point[1] && last[1] === point[1]) {
    points[points.length - 1] = point;
  } else points.push(point);
}

// Clip the *linear segments* at y=0/1 before integrating. Solving against the
// same segments used by the SVG makes the actual painted areas proportional,
// including a small category such as one task against a hundred.
function boundary(threshold) {
  if (threshold <= 0) return [[0, 0], [1, 0]];
  if (threshold >= 2) return [[0, 1], [1, 1]];
  const points = [[0, clamp(threshold)]];
  for (let index = 0; index < STEPS; index++) {
    const [x0, offset0] = WAVE[index], [x1, offset1] = WAVE[index + 1];
    const y0 = threshold + offset0, y1 = threshold + offset1;
    for (const edge of [1, 0]) {
      if (y0 > edge && y1 < edge) {
        const x = x0 + (edge - y0) * (x1 - x0) / (y1 - y0);
        append(points, [x, edge]);
      }
    }
    append(points, [x1, clamp(y1)]);
  }
  return points;
}

function cumulativeArea(threshold) {
  const points = boundary(threshold);
  let area = 0;
  for (let index = 1; index < points.length; index++) {
    const [x0, y0] = points[index - 1], [x1, y1] = points[index];
    area += (x1 - x0) * (y0 + y1) / 2;
  }
  return area;
}

function thresholdForArea(share) {
  if (share <= 0) return 0;
  if (share >= 1) return 2;
  let low = 0, high = 2;
  for (let iteration = 0; iteration < 56; iteration++) {
    const middle = (low + high) / 2;
    if (cumulativeArea(middle) < share) low = middle;
    else high = middle;
  }
  return (low + high) / 2;
}

function bandPoints(from, to) {
  const points = [];
  for (const point of [...boundary(from), ...boundary(to).reverse()]) {
    append(points, point.map(coordinate));
  }
  if (points.length > 1 && points[0][0] === points.at(-1)[0] && points[0][1] === points.at(-1)[1]) points.pop();
  return points;
}

function polygonArea(points) {
  let area = 0;
  for (let index = 0; index < points.length; index++) {
    const [x0, y0] = points[index], [x1, y1] = points[(index + 1) % points.length];
    area += x0 * y1 - x1 * y0;
  }
  return Math.abs(area) / 2;
}

/** Full-cell regions, in stable upper-left green → blue → lower-right orange order. */
export function calendarSurfaceRegions(counts = {}) {
  const entries = KINDS.map(([kind]) => ({ kind, count: countValue(counts?.[kind]) })).filter(entry => entry.count > 0);
  const total = entries.reduce((sum, entry) => sum + entry.count, 0);
  if (!total) return [];
  let cumulative = 0, from = 0;
  return entries.map((entry, index) => {
    const share = entry.count / total;
    cumulative += share;
    const to = thresholdForArea(index === entries.length - 1 ? 1 : cumulative);
    const points = bandPoints(from, to);
    const region = {
      ...entry, level: activityLevel(entry.count), share,
      path: `${points.map(([x, y], pointIndex) => `${pointIndex ? "L" : "M"}${x} ${y}`).join(" ")} Z`,
      points, thresholdFrom: from, thresholdTo: to, area: polygonArea(points),
    };
    from = to;
    return region;
  });
}

function fallbackEscape(value) {
  return String(value ?? "").replace(/[&<>"']/g, char =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

/** Paint is softly blended; the sibling button clips retain exact, disjoint hit areas. */
export function renderCalendarSurface({ date, counts = {}, mode = "month", selectedKind = "", escapeHtml = fallbackEscape } = {}) {
  if (typeof date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(date) || shiftDay(date, 0) !== date) return "";
  const regions = calendarSurfaceRegions(counts);
  if (!regions.length) return "";
  const prefix = `calendar-surface-${mode === "week" ? "week" : "month"}-${date}`;
  const [year, month, day] = date.split("-").map(Number);
  const dateLabel = `${year} 年 ${month} 月 ${day} 日`;
  const clips = regions.map(region => `<clipPath id="${prefix}-${region.kind}-clip" clipPathUnits="objectBoundingBox"><path d="${region.path}"/></clipPath>`).join("");
  const paths = regions.map(region => `<path class="calendar-surface-region" data-activity="${region.kind}" data-count="${region.count}" data-level="${region.level}" d="${region.path}"/>`).join("");
  // A sharp base underneath the blurred paint keeps the outer cell edges fully
  // covered. Only category boundaries blend; hit targets never use the filter.
  const soft = regions.length > 1
    ? `<filter id="${prefix}-blend" x="-0.03" y="-0.03" width="1.06" height="1.06" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB"><feGaussianBlur stdDeviation="0.006"/></filter>` : "";
  const paint = `<svg class="calendar-activity-surface" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true" focusable="false"><defs>${clips}${soft}</defs><g id="${prefix}-paint">${paths}</g>${soft ? `<use href="#${prefix}-paint" filter="url(#${prefix}-blend)"/>` : ""}</svg>`;
  const targets = regions.map(region => {
    const label = `优先显示 ${dateLabel}，${KINDS.find(([kind]) => kind === region.kind)[1]} ${region.count} 项`;
    return `<button type="button" class="calendar-activity-region" data-calendar-activity="${region.kind}" data-calendar-day="${date}" data-count="${region.count}" data-level="${region.level}" data-share="${region.share}" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}" aria-pressed="${selectedKind === region.kind}" tabindex="-1" style="clip-path:url(#${prefix}-${region.kind}-clip)"><span class="sr-only">${escapeHtml(label)}</span></button>`;
  }).join("");
  return `<div class="calendar-surface" data-surface-regions="${regions.length}">${paint}${targets}</div>`;
}
