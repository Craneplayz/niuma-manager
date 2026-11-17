import test from "node:test";
import assert from "node:assert/strict";
import { activityLevel, activitySummary, buildCalendarActivity, calendarDayTasks, calendarActivityOf, nextCalendarActivityChange, calendarDayAppearance } from "../src/calendar-activity.js";

const today = "2026-10-29";
const task = (id, fields = {}) => ({ id, title: id, content_type: "task", status: "todo", due: today, ...fields });
const empty = { pending: 0, done: 0, overdue: 0 };
const counts = (activity, date) => activity.get(date) || { ...empty };

test("early and late completions appear only on their actual completion day, never their deadline", () => {
  const activity = buildCalendarActivity([
    task("early", { status: "done", due: "2026-11-03", completedAt: "2026-10-27" }),
    task("late", { status: "completed", due: "2026-10-24", completedAt: "2026-10-28" }),
    task("same-day", { status: "done", completedAt: today }),
    task("pending", { due: "2026-11-03" }),
  ], { today });
  assert.deepEqual(counts(activity, "2026-10-27"), { pending: 0, done: 1, overdue: 0 });
  assert.deepEqual(counts(activity, "2026-10-28"), { pending: 0, done: 1, overdue: 0 });
  assert.deepEqual(counts(activity, today), { pending: 0, done: 1, overdue: 0 });
  assert.deepEqual(counts(activity, "2026-11-03"), { pending: 1, done: 0, overdue: 0 });
  assert.equal(activity.has("2026-10-24"), false);
});

test("today can contain pending, completed and explicitly overdue timed tasks", () => {
  const activity = buildCalendarActivity([
    task("old", { due: "2026-10-28", dueTime: "23:59", status: "doing" }),
    task("today-early", { dueTime: "00:01" }),
    task("today-late", { dueTime: "23:59", status: "in_progress" }),
    task("future", { due: "2026-10-30", status: "not_started" }),
    task("finished-today", { status: "done", due: "2026-11-01", completedAt: today }),
    task("finished-earlier", { status: "completed", due: "2026-11-02", completedAt: "2026-10-28" }),
  ], { today, now: new Date(2026, 9, 29, 12, 0) });
  assert.deepEqual(counts(activity, "2026-10-28"), { pending: 0, done: 1, overdue: 1 });
  assert.deepEqual(counts(activity, today), { pending: 1, done: 1, overdue: 1 });
  assert.deepEqual(counts(activity, "2026-10-30"), { pending: 1, done: 0, overdue: 0 });
});

test("completed records without a valid completion date are omitted rather than assigned an invented day", () => {
  const invalid = [undefined, null, "", "2026-02-29", "2026-10-32", "2026-10-29T00:00:00Z", " 2026-10-29 ", { date: today }];
  const tasks = invalid.map((completedAt, index) => task(`missing-${index}`, { status: "done", due: "2026-10-20", completedAt }));
  tasks.push(task("undated-done", { due: "", status: "completed", completedAt: "2026-10-28" }));
  const activity = buildCalendarActivity(tasks, { today });
  assert.equal(activity.size, 1);
  assert.deepEqual(counts(activity, "2026-10-28"), { pending: 0, done: 1, overdue: 0 });
  assert.equal(activity.has("2026-10-20"), false);
  assert.equal(activity.has(today), false);
});

test("date validation accepts leap days and server due objects without timezone conversion", () => {
  const invalidDates = ["2026-02-30", "2026-02-29", "2026-04-31", "2026-13-01", "2026-00-10", "2026-10-00", "2026-1-09", "2026-10-29T00:00:00+08:00", "", null];
  const tasks = invalidDates.map((due, index) => task(`invalid-${index}`, { due }));
  tasks.push(task("leap", { due: { date: "2024-02-29", precision: "date" } }));
  tasks.push(task("server", { status: "not_started", due: { date: today, time: "00:01", precision: "datetime" } }));
  tasks.push(task("cross-year", { due: "2027-01-01" }));
  const activity = buildCalendarActivity(tasks, { today, now: new Date(2026, 9, 29, 0, 0) });
  assert.equal(activity.size, 3);
  assert.deepEqual(counts(activity, "2024-02-29"), { pending: 0, done: 0, overdue: 1 });
  assert.deepEqual(counts(activity, today), { pending: 1, done: 0, overdue: 0 });
  assert.deepEqual(counts(activity, "2027-01-01"), { pending: 1, done: 0, overdue: 0 });
});

test("notices, cancelled records, trash and unconfirmed projections do not contribute task activity", () => {
  const activity = buildCalendarActivity([
    null,
    task("notice", { content_type: "notice" }),
    task("read-notice", { content_type: "notice", status: "done", completedAt: today }),
    task("cancelled", { status: "cancelled", completedAt: today }),
    task("trash", { recycleBin: { status: "todo" } }),
    task("done-trash", { status: "done", completedAt: today, recycleBin: { status: "done" } }),
    task("review", { reviewCandidateId: "pending-review" }),
    task("confirmed-with-review", { reviewChanges: [{ id: "proposed-postpone" }] }),
  ], { today });
  assert.deepEqual([...activity], [[today, { pending: 1, done: 0, overdue: 0 }]]);
});

test("reopening removes the old green activity and reclassifies the existing deadline without mutating records", () => {
  const completed = Object.freeze(task("reopened", { due: "2026-10-27", status: "done", completedAt: "2026-10-28" }));
  const completedTasks = Object.freeze([completed]);
  const initial = buildCalendarActivity(completedTasks, { today });
  assert.deepEqual(counts(initial, "2026-10-28"), { pending: 0, done: 1, overdue: 0 });
  const reopened = Object.freeze({ ...completed, status: "todo" });
  const after = buildCalendarActivity(Object.freeze([reopened]), { today });
  assert.equal(after.has("2026-10-28"), false);
  assert.deepEqual(counts(after, "2026-10-27"), { pending: 0, done: 0, overdue: 1 });
  const onDueDay = buildCalendarActivity([reopened], { today: "2026-10-27" });
  assert.deepEqual(counts(onDueDay, "2026-10-27"), { pending: 1, done: 0, overdue: 0 });
  assert.equal(completed.status, "done");
  assert.equal(reopened.completedAt, "2026-10-28");
});

test("day lookup follows the same activity dates and preserves record identity and input order", () => {
  const done = Object.freeze(task("done", { due: "2026-11-01", status: "completed", completedAt: today }));
  const due = Object.freeze(task("due", { due: Object.freeze({ date: today, precision: "date" }) }));
  const otherDone = task("other", { status: "done", completedAt: "2026-10-28" });
  const source = Object.freeze([done, due, otherDone, task("notice", { content_type: "notice" })]);
  const day = calendarDayTasks(source, today, { today });
  assert.deepEqual(day.map(item => item.id), ["done", "due"]);
  assert.equal(day[0], done);
  assert.equal(day[1], due);
  assert.deepEqual(calendarDayTasks(source, "2026-11-01", { today }), []);
  assert.deepEqual(calendarDayTasks(source, "2026-02-30", { today }), []);
  assert.deepEqual(calendarDayTasks(null, today, { today }), []);
  assert.deepEqual(buildCalendarActivity(null, { today }), new Map());
});

test("density bands use stable absolute counts, with empty and invalid counts remaining neutral", () => {
  assert.deepEqual([0, 1, 2, 3, 4, 6, 7, 20, 1000].map(activityLevel), [0, 1, 2, 2, 3, 3, 4, 4, 4]);
  for (const count of [undefined, null, NaN, Infinity, -1, "4"]) assert.equal(activityLevel(count), 0);
});

test("a day's single color prioritizes overdue over pending over done and only counts the chosen category", () => {
  const mixed = Object.freeze({ done: 100, pending: 50, overdue: 1 });
  assert.deepEqual(calendarDayAppearance(mixed), { kind: "overdue", count: 1, level: 1 });
  assert.deepEqual(calendarDayAppearance({ done: 100, pending: 50, overdue: 3 }), { kind: "overdue", count: 3, level: 2 });
  assert.deepEqual(calendarDayAppearance({ done: 100, pending: 2, overdue: 0 }), { kind: "pending", count: 2, level: 2 });
  assert.deepEqual(calendarDayAppearance({ done: 4, pending: 0, overdue: 0 }), { kind: "done", count: 4, level: 3 });
  assert.deepEqual([1, 2, 3, 4, 6, 7, 20].map(count => calendarDayAppearance({ done: 1000, pending: count }).level), [1, 2, 2, 3, 3, 4, 4]);
  assert.deepEqual(mixed, { done: 100, pending: 50, overdue: 1 });
});

test("future deadlines produce pending color while completed-only days use their actual completion records", () => {
  const activity = buildCalendarActivity([
    task("future-one", { due: "2026-11-03" }),
    task("future-two", { due: "2026-11-03" }),
    task("completed", { status: "done", due: "2026-11-03", completedAt: "2026-10-28" }),
  ], { today });
  assert.deepEqual(calendarDayAppearance(activity.get("2026-11-03")), { kind: "pending", count: 2, level: 2 });
  assert.deepEqual(calendarDayAppearance(activity.get("2026-10-28")), { kind: "done", count: 1, level: 1 });
  assert.equal(calendarDayAppearance(activity.get("2026-10-27")), null);
});

test("empty or invalid counts keep the day white and fractional counts use the existing cleanup", () => {
  for (const value of [undefined, null, {}, [], "unknown", { done: 0, pending: 0, overdue: 0 },
    { done: Infinity, pending: "50", overdue: NaN }, { done: -1, pending: null, overdue: -3 }, { unknown: 7 }]) {
    assert.equal(calendarDayAppearance(value), null);
  }
  assert.deepEqual(calendarDayAppearance({ overdue: 0.9, pending: 2.9, done: 100 }), { kind: "pending", count: 2, level: 2 });
});

test("accessible summaries expose each present category and do not create activity from invalid counts", () => {
  assert.equal(activitySummary({ pending: 2, done: 3, overdue: 1 }), "待完成 2 项，已完成 3 项，已逾期 1 项");
  assert.equal(activitySummary({ pending: 0, done: 4, overdue: 0 }), "已完成 4 项");
  assert.equal(activitySummary({ pending: -1, done: NaN, overdue: Infinity }), "无任务活动");
  assert.equal(activitySummary(), "无任务活动");
  assert.equal(activitySummary(null), "无任务活动");
});


test("same-day deadline boundary is exact and date-only tasks never receive an invented time", () => {
  const timed = task("timed", { dueTime: "12:00" });
  assert.equal(calendarActivityOf(timed, { today, now: new Date(2026, 9, 29, 11, 59, 59) }).kind, "pending");
  assert.equal(calendarActivityOf(timed, { today, now: new Date(2026, 9, 29, 12, 0) }).kind, "overdue");
  for (const dueTime of ["", "24:00", "9:30", "12:60", null])
    assert.equal(calendarActivityOf(task("no-time", { dueTime }), { today, now: new Date(2026, 9, 29, 23, 59) }).kind, "pending");
  assert.equal(calendarActivityOf(timed, { today, now: new Date(2026, 9, 30, 23, 0) }).kind, "pending");
  assert.equal(calendarActivityOf(task("raw", { due: { date: today, time: "10:00", precision: "datetime" } }), { today, now: new Date(2026, 9, 29, 12, 0) }).kind, "overdue");
  assert.equal(calendarActivityOf(task("date-only", { due: { date: today, time: "10:00", precision: "date" } }), { today, now: new Date(2026, 9, 29, 12, 0) }).kind, "pending");
});

test("only the next eligible timed deadline schedules a calendar status refresh", () => {
  const now = new Date(2026, 9, 29, 12, 0);
  const tasks = [task("past", { dueTime: "11:59" }), task("later", { dueTime: "15:00" }),
    task("next", { dueTime: "12:01" }), task("undated-time", { due: "", dueTime: "12:00" }),
    task("notice", { dueTime: "12:00", content_type: "notice" }), task("date-only")];
  assert.equal(nextCalendarActivityChange(tasks, { today, now }), new Date(2026, 9, 29, 12, 1).getTime());
  assert.equal(nextCalendarActivityChange(tasks, { today, now: new Date(2026, 9, 29, 16, 0) }), null);
  assert.equal(nextCalendarActivityChange(tasks, { today, now: new Date(NaN) }), null);
  assert.equal(nextCalendarActivityChange(null, { today, now }), null);
});
