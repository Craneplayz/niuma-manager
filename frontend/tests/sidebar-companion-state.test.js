import test from "node:test";
import assert from "node:assert/strict";
import { selectSidebarCompanion } from "../src/sidebar-companion-state.js";

const today = "2026-10-29";
const task = (id, fields = {}) => ({ id, title: id, status: "todo", due: today, ...fields });
const finished = (id, fields = {}) => task(id, { status: "done", completedAt: today, ...fields });
const overdue = id => task(id, { due: "2026-10-28" });
const select = tasks => selectSidebarCompanion(tasks, { today });

test("unready and invalid-day states are neutral and do not report fictitious zero counts", () => {
  for (const options of [{ today, ready: false }, { today: "2026-02-30" }, { today: null }]) {
    const state = selectSidebarCompanion([overdue("one"), finished("two")], options);
    assert.equal(state.key, "unknown");
    assert.equal(state.character, "cow");
    assert.equal(state.ready, false);
    for (const key of ["completedToday", "overdue", "pending", "taskCount"]) assert.equal(state[key], null);
    assert.doesNotMatch(state.message, /(?:完成|做完) \d|逾期|超额|又多/);
  }
});

test("empty data and old completed work have distinct quiet poses without inventing today's progress", () => {
  const empty = select([]);
  assert.equal(empty.key, "idle");
  assert.equal(empty.pose, "settle");
  assert.equal(empty.pending, 0);
  const history = select([finished("old", { completedAt: "2026-10-28" })]);
  assert.equal(history.key, "all-clear");
  assert.equal(history.pose, "wave");
  assert.equal(history.completedToday, 0);
  assert.doesNotMatch(history.message, /今天.*(?:完成|做完)|下一项.*等|超额/);
  assert.equal(select(null).key, "idle");
  assert.equal(selectSidebarCompanion(null, null).key, "idle");
});

test("one or two overdue tasks get a gentle horse while the third selects the checklist pose", () => {
  for (const count of [1, 2, 3, 7]) {
    const state = select(Array.from({ length: count }, (_, index) => overdue(String(index))));
    assert.equal(state.character, "horse");
    assert.equal(state.overdue, count);
    assert.equal(state.pending, count);
    assert.equal(state.key, count >= 3 ? "overdue-high" : "overdue-low");
    assert.equal(state.pose, count >= 3 ? "checklist" : "settle");
    assert.ok(state.message.includes(`${count} 项`));
    assert.doesNotMatch(state.message, /又多|偷懒|才完成|别人|超额/);
  }
});

test("completed progress is acknowledged before overdue work and cannot override its reminder", () => {
  for (const overdueCount of [1, 3]) {
    for (const doneCount of [1, 4]) {
      const state = select([
        ...Array.from({ length: overdueCount }, (_, index) => overdue(`late-${index}`)),
        ...Array.from({ length: doneCount }, (_, index) => finished(`done-${index}`)),
      ]);
      assert.equal(state.character, "horse");
      assert.match(state.message, new RegExp(`^今天做完 ${doneCount} 项`));
      assert.ok(state.message.includes(`还有 ${overdueCount} 项逾期`));
      assert.equal(state.completedToday, doneCount);
    }
  }
});

test("three recorded completions with no overdue work select dancing cow, even with no remaining tasks", () => {
  for (const extra of [[], [task("next", { due: "2026-10-30" })]]) {
    const state = select([finished("one"), finished("two"), finished("three"), ...extra]);
    assert.equal(state.key, "progress");
    assert.equal(state.character, "cow");
    assert.equal(state.pose, "dance");
    assert.equal(state.completedToday, 3);
    assert.equal(state.overdue, 0);
    assert.match(state.message, /今天做完 3 项/);
    assert.doesNotMatch(state.message, /超额|比昨天|提前完成/);
  }
  for (const count of [1, 2]) {
    const state = select(Array.from({ length: count }, (_, index) => finished(String(index))));
    assert.equal(state.key, "small-progress");
    assert.equal(state.pose, "celebrate");
    assert.match(state.message, new RegExp(`今天做完 ${count} 项`));
  }
});

test("notifications, recycled records, cancelled tasks and pending projections never change the companion", () => {
  const state = select([
    null,
    finished("read", { content_type: "notice" }),
    task("unread", { content_type: "notice", due: "2026-10-20" }),
    task("cancelled", { due: "2026-10-20", status: "cancelled" }),
    finished("trashed", { recycleBin: { status: "done" } }),
    task("review", { due: "2026-10-20", reviewCandidateId: "candidate" }),
    task("confirmed", { reviewChanges: [{ id: "proposed-change" }] }),
  ]);
  assert.equal(state.key, "steady");
  assert.equal(state.taskCount, 1);
  assert.equal(state.pending, 1);
  assert.equal(state.overdue, 0);
  assert.equal(state.completedToday, 0);
});

test("valid business dates and client or server statuses are counted without timestamp conversion or due-date fallback", () => {
  const state = select([
    finished("valid-server", { status: "completed", due: { date: "2026-10-01", precision: "date" } }),
    finished("missing-date", { completedAt: undefined }),
    finished("invalid-date", { completedAt: "2026-02-29" }),
    finished("timestamp", { completedAt: "2026-10-28T23:00:00Z" }),
    finished("future-date", { completedAt: "2026-10-30" }),
    task("due-now", { due: { date: today, time: "00:01", precision: "datetime" }, status: "in_progress" }),
    task("bad-due", { due: "2026-02-30" }),
    task("no-due", { due: null }),
  ]);
  assert.equal(state.completedToday, 1);
  assert.equal(state.overdue, 0);
  assert.equal(state.pending, 3);
  assert.equal(state.key, "small-progress");
  const leap = selectSidebarCompanion([task("leap", { due: { date: "2024-02-29" } })], { today: "2024-03-01" });
  assert.equal(leap.overdue, 1);
});

test("reopening and day rollover derive new states without retaining false achievements or mutating inputs", () => {
  const records = Object.freeze([
    Object.freeze(finished("one")), Object.freeze(finished("two")), Object.freeze(finished("three")),
  ]);
  const before = select(records);
  assert.equal(before.key, "progress");
  assert.deepEqual(select(records), before);
  const reopened = Object.freeze([Object.freeze({ ...records[0], status: "todo", due: "2026-10-28" }), ...records.slice(1)]);
  const after = select(reopened);
  assert.equal(after.key, "overdue-low");
  assert.equal(after.completedToday, 2);
  assert.equal(after.overdue, 1);
  assert.equal(records[0].status, "done");
  assert.equal(selectSidebarCompanion(records, { today: "2026-10-30" }).completedToday, 0);
  assert.equal(selectSidebarCompanion(records, { today: "2026-10-30" }).key, "all-clear");
});
