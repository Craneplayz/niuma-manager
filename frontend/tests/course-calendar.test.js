import test from "node:test";
import assert from "node:assert/strict";
import { renderCalendar } from "../src/calendar.js";
import { courseIdentity, syncCourseColors } from "../src/course-identity.js";

const today = "2026-10-26";
const task = (id, course, extra = {}) => ({
  id, title: `任务 ${id}`, course, due: today, status: "todo", ...extra,
});
const dateButton = html => html.match(new RegExp(`<button[^>]*data-calendar-day="${today}"[^>]*>[\\s\\S]*?<\\/button>`))?.[0] || "";

test("collapsed date summaries show at most three distinct course markers without losing accessible course names", () => {
  const tasks = [
    task("a", "数据结构"), task("b", " 数据结构 "), task("c", "概率论"),
    task("d", "线性代数"), task("e", "大学英语"),
  ];
  syncCourseColors(tasks.map(item => item.course), { scope: "calendar-markers", storage: null });
  const html = renderCalendar({ tasks, today, anchorDate: today });
  const button = dateButton(html);
  const dots = button.match(/class="course-dot"/g) || [];
  assert.equal(dots.length, 3);
  for (const name of ["数据结构", "概率论", "线性代数", "大学英语"])
    assert.ok(button.includes(name), `date accessible name includes ${name}`);
  assert.match(button, /5 项任务/);
  assert.equal((html.match(/data-task-open=/g) || []).length, 2);
  assert.match(html, /\+3 项/);
});

test("course names stay escaped in task and day labels while click and drag identities stay intact", () => {
  const course = '"><svg onload="unsafe">';
  const item = task('quoted"id', course);
  const html = renderCalendar({ tasks: [item], today, anchorDate: today, allowDateDrag: true });
  assert.doesNotMatch(html, /<svg|onload="unsafe"/);
  assert.match(html, /&lt;svg/);
  assert.match(html, /data-task-open="quoted&quot;id"/);
  assert.match(html, /data-calendar-drag-task="quoted&quot;id"/);
  assert.match(html, /课程：&quot;&gt;&lt;svg/);
  assert.equal((html.match(/<button\b/g) || []).length, (html.match(/<\/button>/g) || []).length);
});

test("month and week use the same course identity and completed tasks do not contribute markers", () => {
  syncCourseColors(["数据结构", "已完成课程"], { scope: "calendar-identity", storage: null });
  const course = "数据结构";
  const tone = courseIdentity(course).tone;
  for (const mode of ["month", "week"]) {
    const html = renderCalendar({
      tasks: [task("open", course), task("done", "已完成课程", { status: "done" })],
      today, anchorDate: today, mode,
    });
    assert.ok(html.includes(`data-course-tone="${tone}"`));
    assert.match(html, /课程：数据结构/);
    assert.doesNotMatch(html, /已完成课程|data-task-open="done"/);
  }
  const empty = renderCalendar({ tasks: [], today, anchorDate: today });
  assert.doesNotMatch(dateButton(empty), /calendar-day-course-dots/);
});
