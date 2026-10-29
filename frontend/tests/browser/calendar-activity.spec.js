import { test, expect } from "@playwright/test";

test.use({ timezoneId: "Asia/Shanghai" });

const today = "2026-10-29";
const unsafeTitle = '<img src=x onerror="window.__calendarActivityUnsafe=true">';
const task = (id, date, fields = {}) => ({
  id, title: `合成任务 ${id}`, content_type: "task", status: "not_started",
  due: date ? { date, precision: "date" } : null,
  course: "数据结构", source: "教学网", plans: [], subtasks: [], ...fields,
});
const cell = (page, date) => page.locator(`.calendar-day[data-calendar-drop-date="${date}"]`);

function pendingReview() {
  return [{
    request: { request_id: "activity-review", contract_version: "0.2", records: [{ input_id: "source", platform: "teaching_web", text: "合成待核对任务", source_label: "教学网" }] },
    result: { status: "success", errors: [] },
    items: [{ id: "activity-pending", state: "pending", version: 1, candidate: {
      input_id: "source", title: "未入库的待核对任务", description: "确认后才成为正式任务", course_or_project: "数据结构",
      content_type: "task", notice_type: "create",
      deadline: { kind: "deadline", status: "explicit", precision: "date", value: "2026-10-30" },
      sources: [{ input_id: "source", platform: "teaching_web", label: "教学网", url: null }],
      evidence: [{ input_id: "source", quote: "合成待核对任务" }], needs_review: true, review_reasons: ["请确认"],
    } }],
  }];
}

function activityTasks() {
  const tasks = [];
  const add = (kind, date, count) => {
    for (let i = 0; i < count; i++) tasks.push(task(`${kind}-${date}-${i}`, kind === "done" ? "2026-11-12" : date, {
      course: i % 2 ? "线性代数" : "数据结构",
      ...(kind === "done" ? { status: "completed", completedAt: date } : {}),
    }));
  };
  add("done", today, 9);
  add("pending", today, 8);
  tasks.push(task("today-expired", today, { due: { date: today, time: "09:00", precision: "datetime" } }));
  for (const [date, count] of [["2026-10-25", 7], ["2026-10-27", 1], ["2026-10-28", 3]]) add("overdue", date, count);
  add("done", "2026-10-27", 1);
  add("done", "2026-10-28", 14);
  add("done", "2026-10-26", 2);
  for (const [date, count] of [["2026-10-30", 2], ["2026-10-31", 4], ["2026-11-01", 7]]) add("pending", date, count);
  tasks.push(
    task("unread-notice", "2026-10-30", { content_type: "notice" }),
    task("read-notice", "2026-10-26", { content_type: "notice", status: "completed", completedAt: "2026-10-26" }),
    task("undated", null),
    task("cancelled", "2026-10-30", { status: "cancelled" }),
    task("trashed", "2026-10-30", { status: "cancelled", recycleBin: { deletedAt: "2026-10-28T10:00:00Z", status: "not_started" } }),
  );
  return tasks;
}

async function workspace(page, { tasks: initialTasks = activityTasks(), reviews = pendingReview(), url = "/", timers = false } = {}) {
  if (timers) await page.clock.install({ time: new Date("2026-10-29T04:00:00Z") });
  else await page.clock.setFixedTime(new Date("2026-10-29T04:00:00Z"));
  let tasks = structuredClone(initialTasks), version = 1, reads = 0;
  const unknown = [], errors = [], writes = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value;
    if (path === "/api/auth/me") value = { user: { id: "activity-browser-user", name: "合成账号", email: "activity@example.test", emailVerified: true } };
    else if (path === "/api/workspace") { reads++; value = { tasks: { version, value: tasks }, trash: { version: 1, value: [] } }; }
    else if (path === "/api/data/tasks") {
      const body = structuredClone(request.postDataJSON());
      expect(body.version).toBe(version);
      writes.push(body);
      tasks = structuredClone(body.value);
      value = { version: ++version };
    } else if (path === "/api/version") value = { environment: "test", revision: "activity-synthetic" };
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/usage/guest-visit") value = { saved: true };
    else if (path === "/api/recognition/batches") value = reviews;
    else if (path === "/api/recognition/text-jobs") value = { contract_version: "text-import-jobs/0.1", jobs: [] };
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else if (path === "/api/sources/credentials") value = { configured: false };
    else if (path === "/api/sources/sync-runs") value = { contract_version: "source-sync/0.1", runs: [] };
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else if (path === "/api/materials/imports") value = { jobs: [] };
    else { unknown.push(path); return route.fulfill({ status: 404, json: { error: "Unknown synthetic API" } }); }
    await route.fulfill({ json: value });
  });
  await page.goto(url);
  await expect(page.locator(".loading-state")).toHaveCount(0);
  return { unknown, errors, writes, reads: () => reads, tasks: () => structuredClone(tasks) };
}

function noErrors(state) {
  expect(state.unknown).toEqual([]);
  expect(state.errors).toEqual([]);
}

async function expectStatus(page, date, kind, count = 0) {
  const target = cell(page, date);
  if (!kind) {
    for (const name of ["data-activity-kind", "data-activity-count", "data-activity-level"]) await expect(target).not.toHaveAttribute(name);
    return;
  }
  const level = count === 1 ? 1 : count <= 3 ? 2 : count <= 6 ? 3 : 4;
  await expect(target).toHaveAttribute("data-activity-kind", kind);
  await expect(target).toHaveAttribute("data-activity-count", String(count));
  await expect(target).toHaveAttribute("data-activity-level", String(level));
}

const color = locator => locator.evaluate(element => getComputedStyle(element).backgroundColor);
const calendarURL = "/?view=calendar&mode=month&anchor=2026-10-29&date=2026-10-29";

test("each day has one priority colour and its intensity uses only the highest-priority count", async ({ page }, info) => {
  const state = await workspace(page, { url: calendarURL });
  for (const [date, kind, count] of [
    [today, "overdue", 1], ["2026-10-27", "overdue", 1], ["2026-10-28", "overdue", 3],
    ["2026-10-25", "overdue", 7], ["2026-10-26", "done", 2], ["2026-10-30", "pending", 2],
    ["2026-10-31", "pending", 4], ["2026-11-01", "pending", 7],
  ]) await expectStatus(page, date, kind, count);
  await expectStatus(page, "2026-10-24", null);
  // Today's nine completions and eight remaining tasks cannot darken its one
  // overdue task. Another day with one overdue task has the same red.
  expect(await color(cell(page, today))).toBe(await color(cell(page, "2026-10-27")));
  expect(await color(cell(page, today))).not.toBe(await color(cell(page, "2026-10-28")));
  const tones = await Promise.all([today, "2026-10-26", "2026-10-30"].map(date => color(cell(page, date))));
  expect(new Set(tones).size).toBe(3);
  const channels = tones.map(tone => tone.match(/[\d.]+/g).slice(0, 3).map(Number));
  expect(channels[0][0]).toBeGreaterThan(channels[0][1]);
  expect(channels[1][1]).toBeGreaterThan(channels[1][0]);
  expect(channels[2][2]).toBeGreaterThan(channels[2][0]);
  for (const date of [today, "2026-10-26", "2026-10-30"]) await expect(cell(page, date)).toHaveCSS("background-image", "none");
  expect(["rgb(255, 255, 255)", "rgba(0, 0, 0, 0)"]).toContain(await color(cell(page, "2026-10-24")));
  await expect(page.locator(".calendar-surface, .calendar-activity-region, [data-calendar-priority]")).toHaveCount(0);
  expect(state.writes).toEqual([]);
  await page.screenshot({ path: info.outputPath("calendar-day-status-month.png"), fullPage: true });
  noErrors(state);
});

test("month and week agree, while clicking a date shows all its content and retains narrow course markers", async ({ page }, info) => {
  const state = await workspace(page);
  await expect(page.locator('[data-review-id="activity-pending"]')).toBeVisible();
  await page.locator("#task-search").fill("不存在的合成搜索词");
  await page.locator("#course-filter").selectOption("线性代数");
  await expect(page.locator(".task-row")).toHaveCount(0);
  await page.locator('[data-view="calendar"]').click();
  await expectStatus(page, "2026-10-30", "pending", 2);
  await expect(cell(page, "2026-10-30").locator(".calendar-day-course-dots > .course-dot")).toHaveCount(2);
  if (page.viewportSize().width <= 700) await expect(cell(page, "2026-10-30").locator(".calendar-day-course-dots")).toBeVisible();
  const grid = await page.locator(".calendar-month-grid").boundingBox();
  expect(grid.y + grid.height).toBeLessThanOrEqual(page.viewportSize().height + 1);
  await cell(page, today).locator(".calendar-date-button").click();
  await expect(page.locator(".day-panel .task-row")).toHaveCount(18);
  for (const id of ["today-expired", `pending-${today}-0`, `done-${today}-0`]) await expect(page.locator(`.day-panel [data-task-id="${id}"]`)).toHaveCount(1);
  await cell(page, "2026-10-30").locator(".calendar-date-button").click();
  await expect(page.locator(".day-panel .task-row")).toHaveCount(3);
  await expect(page.locator('.day-panel [data-task-id="unread-notice"]')).toHaveCount(1);
  await expect(page.locator("[data-calendar-priority], .calendar-activity-region")).toHaveCount(0);
  expect(new URL(page.url()).searchParams.has("activity")).toBe(false);
  expect(new URL(page.url()).searchParams.has("priority")).toBe(false);
  const dates = ["2026-10-26", "2026-10-27", "2026-10-28", today, "2026-10-30", "2026-10-31", "2026-11-01"];
  const snapshot = () => Promise.all(dates.map(date => cell(page, date).evaluate(element => ({
    kind: element.dataset.activityKind, count: element.dataset.activityCount, level: element.dataset.activityLevel,
    color: getComputedStyle(element).backgroundColor,
  }))));
  const month = await snapshot();
  await page.locator('[data-calendar-mode="week"]').click();
  expect(await snapshot()).toEqual(month);
  await expect(page.locator(".calendar-week .calendar-day")).toHaveCount(7);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("calendar-day-status-week.png"), fullPage: true });
  await page.locator('[data-view="list"]').click();
  await expect(page.locator("#task-search")).toHaveValue("不存在的合成搜索词");
  await expect(page.locator("#course-filter")).toHaveValue("线性代数");
  await expect(page.locator(".task-row")).toHaveCount(0);
  noErrors(state);
});

test("an explicit deadline turns a blue day red at its time without a refresh or task write", async ({ page }) => {
  const state = await workspace(page, { reviews: [], url: calendarURL, timers: true, tasks: [
    task("timed", today, { due: { date: today, time: "12:01", precision: "datetime" } }),
    task("date-only", today),
    task("already-done", "2026-11-12", { status: "completed", completedAt: today }),
  ] });
  await expectStatus(page, today, "pending", 2);
  const url = page.url();
  await page.clock.fastForward(60_100);
  await expectStatus(page, today, "overdue", 1);
  await expect(page.locator(".day-panel .task-row")).toHaveCount(3);
  for (const id of ["timed", "date-only", "already-done"]) await expect(page.locator(`.day-panel [data-task-id="${id}"]`)).toHaveCount(1);
  expect(page.url()).toBe(url);
  expect(state.reads()).toBe(1);
  expect(state.writes).toEqual([]);
  noErrors(state);
});

test("historical completion records support reopening and undo without inventing a completion deadline", async ({ page }, info) => {
  const state = await workspace(page, { reviews: [], url: calendarURL, tasks: [
    task("historical", "2026-10-28", { status: "completed", completedAt: "2026-10-20", title: unsafeTitle }),
    task("future", "2026-10-30"),
  ] });
  await expectStatus(page, "2026-10-20", "done", 1);
  await expectStatus(page, "2026-10-28", null);
  await cell(page, "2026-10-20").locator(".calendar-date-button").click();
  const completed = page.locator(".day-panel-completed");
  await expect(completed.locator('[data-task-id="historical"]')).toBeVisible();
  await expect(completed.locator('[data-task-open="historical"] .task-title')).toHaveText(unsafeTitle);
  await expect(completed.locator("img")).toHaveCount(0);
  await completed.locator('[data-task-open="historical"]').click();
  const drawer = page.locator("#task-drawer");
  await expect(drawer.locator("#drawer-heading")).toHaveText(unsafeTitle);
  await drawer.getByRole("button", { name: "重新打开", exact: true }).click();
  await expect.poll(() => state.tasks().find(item => item.id === "historical").status).toBe("not_started");
  await expectStatus(page, "2026-10-20", null);
  await expectStatus(page, "2026-10-28", "overdue", 1);
  await page.keyboard.press("Escape");
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "historical").status).toBe("completed");
  expect(state.tasks().find(item => item.id === "historical").completedAt).toBe("2026-10-20");
  await expectStatus(page, "2026-10-20", "done", 1);
  await expectStatus(page, "2026-10-28", null);
  await cell(page, "2026-10-30").locator(".calendar-date-button").click();
  await page.locator('.day-panel [data-task-complete="future"]').click();
  await expect.poll(() => state.tasks().find(item => item.id === "future").status).toBe("completed");
  expect(state.tasks().find(item => item.id === "future").completedAt).toBe(today);
  await expectStatus(page, "2026-10-30", null);
  await expectStatus(page, today, "done", 1);
  await cell(page, today).locator(".calendar-date-button").click();
  await expect(page.locator('.day-panel-completed [data-task-id="future"]')).toBeVisible();
  await page.screenshot({ path: info.outputPath("calendar-day-status-completed-day.png"), fullPage: true });
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "future").status).toBe("not_started");
  await expectStatus(page, today, null);
  await expectStatus(page, "2026-10-30", "pending", 1);
  expect(await page.evaluate(() => window.__calendarActivityUnsafe)).toBeUndefined();
  expect(state.writes).toHaveLength(4);
  noErrors(state);
});
