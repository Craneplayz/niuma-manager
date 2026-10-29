/** Authored cells on each 4 × 4 transparent frame sheet; milliseconds per frame. */
export const motionFrames = Object.freeze({
  blink: [[1, 130], [0, 120]],
  wave: [[6, 150], [7, 170], [6, 150], [7, 170], [8, 180], [0, 160]],
  celebrate: [[9, 170], [10, 200], [11, 200], [10, 180], [11, 180], [12, 260], [15, 170]],
  land: [[14, 140], [15, 180], [0, 150]],
});
export function framePosition(frame) {
  const cell = Math.min(15, Math.max(0, Math.trunc(Number(frame) || 0)));
  return `${cell % 4 * 100 / 3}% ${Math.floor(cell / 4) * 100 / 3}%`;
}
/** Look nearby, never change position in response to a passing pointer. */
export function lookFrame(dx, dy, neutral = 0) {
  const distance = Math.hypot(dx, dy);
  if (!Number.isFinite(distance) || distance > 230 || distance < 20) return neutral;
  if (Math.abs(dx) > Math.abs(dy) * 1.15) return dx < 0 ? 2 : 3;
  return dy < 0 ? 4 : 5;
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
