import test from "node:test";
import assert from "node:assert/strict";
import { createCalendarDrag } from "../src/calendar-drag.js";
import { renderDayPanel } from "../src/task-views.js";
import { renderCalendar } from "../src/calendar.js";

class Surface {
  listeners = new Map();
  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }
  emit(type, values = {}) {
    const event = {
      type, isPrimary: true, button: 0, buttons: 1, pointerType: "mouse", pointerId: 1,
      clientX: 20, clientY: 30, detail: 1, ...values,
      preventDefault() { this.defaultPrevented = true; },
      stopImmediatePropagation() { this.stopped = true; },
    };
    for (const listener of this.listeners.get(type) || []) {
      listener(event);
      if (event.stopped) break;
    }
    return event;
  }
}
class Element extends Surface {
  dataset = {};
  isConnected = true;
  children = [];
  classes = new Set();
  classList = { add: name => this.classes.add(name), remove: name => this.classes.delete(name) };
  style = {};
  offsetWidth = 250;
  offsetHeight = 65;
  capture = null;
  setAttribute() {}
  append(...nodes) { this.children.push(...nodes); }
  remove() { this.removed = true; }
  contains(node) { return node === this || this.children.includes(node); }
  hasPointerCapture(id) { return this.capture === id; }
  setPointerCapture(id) { this.capture = id; }
  releasePointerCapture() { this.capture = null; this.emit("lostpointercapture"); }
  closest(selector) {
    if (selector === "[data-calendar-drag-task]" && this.dataset.calendarDragTask) return this;
    if (selector === ".calendar-month [data-calendar-drop-date]" && this.dataset.calendarDropDate) return this;
    return null;
  }
}
function fixture() {
  const doc = new Surface(), win = new Surface(), root = new Element(), source = new Element(), cell = new Element();
  const body = new Element(), drops = [];
  source.dataset.calendarDragTask = "undated";
  cell.dataset.calendarDropDate = "2026-10-20";
  root.append(source, cell);
  doc.body = body;
  doc.createElement = () => new Element();
  doc.elementFromPoint = () => cell;
  win.innerWidth = 1440;
  win.innerHeight = 768;
  Object.assign(globalThis, { document: doc, window: win });
  let eligible = true;
  const controller = createCalendarDrag({
    root,
    getTask: id => eligible && id === "undated" ? { title: "合成任务 <无 HTML>" } : null,
    onDrop: (id, date) => drops.push({ id, date }),
  });
  const emit = (type, values) => doc.emit(type, { target: source, ...values });
  const startDrag = () => { emit("pointerdown"); emit("pointermove", { clientX: 50 }); };
  return { doc, win, root, source, cell, body, drops, controller, emit, startDrag, disable: () => { eligible = false; } };
}

test("a click or small mouse movement keeps the original task-open click", () => {
  const f = fixture();
  f.emit("pointerdown");
  f.emit("pointermove", { clientX: 23, clientY: 34 });
  f.emit("pointerup");
  assert.equal(f.emit("click").defaultPrevented, undefined);
  assert.equal(f.body.children.length, 0);
  assert.deepEqual(f.drops, []);
});

test("a deliberate drag highlights the date, assigns once and suppresses its generated click", () => {
  const f = fixture();
  f.startDrag();
  assert.ok(f.cell.classes.has("is-drop-target"));
  assert.ok(f.source.classes.has("is-dragging"));
  const [preview] = f.body.children;
  assert.equal(preview.children[0].textContent, "合成任务 <无 HTML>");
  assert.match(preview.children[1].textContent, /2026 年 10 月 20 日截止/);
  f.emit("pointerup");
  assert.deepEqual(f.drops, [{ id: "undated", date: "2026-10-20" }]);
  assert.ok(preview.removed);
  assert.equal(f.cell.classes.has("is-drop-target"), false);
  assert.equal(f.root.capture, null);
  assert.ok(f.emit("click").stopped);
  f.emit("pointerdown");
  f.emit("pointerup");
  assert.equal(f.emit("click").stopped, undefined);
});

test("release outside cancels and recomputes the target instead of using a stale hover", () => {
  const f = fixture();
  f.startDrag();
  f.doc.elementFromPoint = () => null;
  f.emit("pointerup");
  assert.deepEqual(f.drops, []);
  assert.equal(f.cell.classes.has("is-drop-target"), false);
  assert.ok(f.emit("click").stopped);
});

test("Escape, pointer cancellation, lost capture, blur, hide and rerender safely cancel", () => {
  for (const cancel of [f => f.emit("keydown", { key: "Escape" }), f => f.emit("pointercancel"),
    f => f.root.emit("lostpointercapture"), f => f.win.emit("blur"),
    f => { f.doc.visibilityState = "hidden"; f.emit("visibilitychange"); }, f => f.controller.cancel()]) {
    const f = fixture();
    f.startDrag();
    cancel(f);
    f.emit("pointerup");
    assert.deepEqual(f.drops, []);
    assert.equal(f.source.classes.has("is-dragging"), false);
    assert.equal(f.body.classes.has("is-calendar-dragging"), false);
    assert.ok(f.body.children[0].removed);
  }
});

test("touch scrolling, secondary buttons and unrelated pointers cannot assign dates", () => {
  for (const options of [{ pointerType: "touch" }, { button: 2 }, { isPrimary: false }]) {
    const f = fixture();
    f.emit("pointerdown", options);
    f.emit("pointermove", { clientX: 100 });
    f.emit("pointerup");
    assert.deepEqual(f.drops, []);
    assert.equal(f.body.children.length, 0);
  }
  const f = fixture();
  f.startDrag();
  f.emit("pointerup", { pointerId: 2 });
  assert.deepEqual(f.drops, []);
  f.disable();
  f.emit("pointerup");
  assert.deepEqual(f.drops, []);
});

test("only month-view undated tasks gain a gesture, with no date-setting button", () => {
  const tasks = [
    { id: "undated", title: "未定日期", due: "", status: "todo" },
    { id: "notice", title: "通知", content_type: "notice", due: "", status: "todo" },
    { id: "done", title: "已完成", due: "", status: "done" },
    { id: "dated", title: "有日期", due: "2026-10-16", status: "todo" },
  ];
  const panel = renderDayPanel({ tasks, pane: "undated", allowDateDrag: true, today: "2026-10-16" });
  assert.equal((panel.match(/data-calendar-drag-task=/g) || []).length, 1);
  assert.match(panel, /data-calendar-drag-task="undated"/);
  assert.doesNotMatch(panel, /<button[^>]*>设置日期/);
  for (const options of [{ pane: "day", allowDateDrag: true }, { pane: "undated", allowDateDrag: false }])
    assert.doesNotMatch(renderDayPanel({ tasks, ...options, today: "2026-10-16" }), /data-calendar-drag-task/);
  const month = renderCalendar({ tasks, today: "2026-10-16" });
  assert.match(month, /data-calendar-drop-date="2026-10-20"/);
  assert.doesNotMatch(renderCalendar({ tasks, mode: "week", today: "2026-10-16" }), /data-calendar-drop-date/);
});
