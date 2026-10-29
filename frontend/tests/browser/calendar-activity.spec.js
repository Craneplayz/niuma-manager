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
const square = (page, date, kind) => cell(page, date).locator(`.calendar-activity-region[data-calendar-activity="${kind}"]`);

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
  [["2026-10-29", 1], ["2026-10-30", 2], ["2026-10-31", 4], ["2026-11-01", 7]].forEach(([date, count]) => add("pending", date, count));
  [["2026-10-26", 1], ["2026-10-27", 2], ["2026-10-28", 4], ["2026-10-25", 7]].forEach(([date, count]) => {
    add("overdue", date, count);
    add("done", date, count);
  });
  tasks.push(
    task("unread-notice", "2026-10-30", { content_type: "notice" }),
    task("read-notice", "2026-10-26", { content_type: "notice", status: "completed", completedAt: "2026-10-26" }),
    task("undated", null),
    task("cancelled", "2026-10-30", { status: "cancelled" }),
    task("trashed", "2026-10-30", { status: "cancelled", recycleBin: { deletedAt: "2026-10-28T10:00:00Z", status: "not_started" } }),
  );
  return tasks;
}

async function workspace(page, { tasks: initialTasks = activityTasks(), reviews = pendingReview(), url = "/" } = {}) {
  await page.clock.setFixedTime(new Date("2026-10-29T04:00:00Z"));
  let tasks = structuredClone(initialTasks), version = 1;
  const unknown = [], errors = [], writes = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value;
    if (path === "/api/auth/me") value = { user: { id: "activity-browser-user", name: "合成账号", email: "activity@example.test", emailVerified: true } };
    else if (path === "/api/workspace") value = { tasks: { version, value: tasks }, trash: { version: 1, value: [] } };
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
  return { unknown, errors, writes, tasks: () => structuredClone(tasks) };
}

function noErrors(state) {
  expect(state.unknown).toEqual([]);
  expect(state.errors).toEqual([]);
}

async function expectActivity(page, date, expected) {
  await expect(cell(page, date).locator(".calendar-activity-region")).toHaveCount(Object.values(expected).filter(count => count > 0).length);
  for (const [kind, count] of Object.entries(expected)) {
    const level = count === 0 ? 0 : count === 1 ? 1 : count <= 3 ? 2 : count <= 6 ? 3 : 4;
    if (!count) { await expect(square(page, date, kind)).toHaveCount(0); continue; }
    await expect(square(page, date, kind)).toHaveAttribute("data-count", String(count));
    await expect(square(page, date, kind)).toHaveAttribute("data-level", String(level));
  }
}

test("month activity uses due dates for blue/orange, actual completion dates for green, and four stable intensity levels", async ({ page }, info) => {
  const state = await workspace(page);
  await expect(page.locator('[data-review-id="activity-pending"]')).toBeVisible();
  await page.locator('[data-view="calendar"]').click();
  for (const [date, count] of [["2026-10-29", 1], ["2026-10-30", 2], ["2026-10-31", 4], ["2026-11-01", 7]]) {
    await expectActivity(page, date, { pending: count, done: 0, overdue: 0 });
  }
  for (const [date, count] of [["2026-10-26", 1], ["2026-10-27", 2], ["2026-10-28", 4], ["2026-10-25", 7]]) {
    await expectActivity(page, date, { pending: 0, done: count, overdue: count });
  }
  await expectActivity(page, "2026-10-24", { pending: 0, done: 0, overdue: 0 });
  // A notice, cancelled task, trashed task and unconfirmed draft all share this
  // deadline, but only the two real unfinished tasks contribute to activity.
  await expect(square(page, "2026-10-30", "pending")).toHaveAttribute("data-count", "2");
  const samples = [
    ["pending", ["2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"]],
    ["done", ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-25"]],
    ["overdue", ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-25"]],
  ];
  const strongest = [];
  for (const [kind, dates] of samples) {
    const colors = [];
    for (const date of dates) colors.push(await cell(page, date).locator(`.calendar-surface-region[data-activity="${kind}"]`).evaluate(element => getComputedStyle(element).fill));
    expect(new Set(colors).size).toBe(4);
    strongest.push(colors[3]);
  }
  expect(new Set(strongest).size).toBe(3);
  await expect(cell(page, "2026-10-30").locator(".calendar-date-button")).toHaveAttribute("title", /2/);
  // Dates lead keyboard navigation; the large day-panel controls provide equivalent priority selection.
  await expect(page.locator('.calendar-activity-region[tabindex="0"]')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("calendar-activity-month.png"), fullPage: true });
  noErrors(state);
});

test("calendar activity ignores list filters, matches across month/week, and preserves mobile course markers and the full month", async ({ page }, info) => {
  const state = await workspace(page);
  await page.locator("#task-search").fill("不存在的合成搜索词");
  await page.locator("#course-filter").selectOption("线性代数");
  await expect(page.locator(".task-row")).toHaveCount(0);
  await page.locator('[data-view="calendar"]').click();
  await expect(page.locator("#task-search")).toHaveCount(0);
  await expectActivity(page, "2026-10-30", { pending: 2, done: 0, overdue: 0 });
  await expect(cell(page, "2026-10-30").locator(".calendar-day-course-dots > .course-dot")).toHaveCount(2);
  if (page.viewportSize().width <= 700) await expect(cell(page, "2026-10-30").locator(".calendar-day-course-dots")).toBeVisible();
  const grid = await page.locator(".calendar-month-grid").boundingBox();
  expect(grid.y + grid.height).toBeLessThanOrEqual(page.viewportSize().height + 1);
  const weekDates = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31", "2026-11-01"];
  const snapshot = async () => Promise.all(weekDates.map(async date => ({
    date,
    counts: await cell(page, date).locator(".calendar-activity-region").evaluateAll(elements => elements.map(element => ({ kind: element.dataset.calendarActivity, count: element.dataset.count, level: element.dataset.level }))),
  })));
  const month = await snapshot();
  await page.screenshot({ path: info.outputPath("calendar-activity-month-filter-independent.png"), fullPage: true });
  await page.locator('[data-calendar-mode="week"]').click();
  expect(await snapshot()).toEqual(month);
  await expect(page.locator(".calendar-week .calendar-day")).toHaveCount(7);
  const surfaceBox = await cell(page, today).locator(".calendar-surface").boundingBox();
  const dayBox = await cell(page, today).boundingBox();
  expect(Math.abs(surfaceBox.width - dayBox.width)).toBeLessThanOrEqual(2);
  expect(Math.abs(surfaceBox.height - dayBox.height)).toBeLessThanOrEqual(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("calendar-activity-week.png"), fullPage: true });
  await page.locator('[data-view="list"]').click();
  await expect(page.locator("#task-search")).toHaveValue("不存在的合成搜索词");
  await expect(page.locator("#course-filter")).toHaveValue("线性代数");
  await expect(page.locator(".task-row")).toHaveCount(0);
  noErrors(state);
});

test("a completed-only historical day opens real tasks; reopening and completing recompute green on the actual day and support undo", async ({ page }, info) => {
  const state = await workspace(page, {
    tasks: [
      task("historical", "2026-11-12", { status: "completed", completedAt: "2026-10-20", title: unsafeTitle }),
      task("future", "2026-10-30"),
    ], reviews: [], url: "/?view=calendar&mode=month&anchor=2026-10-29&date=2026-10-29",
  });
  await expectActivity(page, "2026-10-20", { pending: 0, done: 1, overdue: 0 });
  await cell(page, "2026-10-20").locator(".calendar-date-button").click();
  const completed = page.locator(".day-panel-completed");
  await expect(completed.locator('[data-task-id="historical"]')).toBeVisible();
  await expect(page.locator('[data-calendar-pane="day"] > span')).toHaveText("1");
  await expect(page.locator('.day-panel-due [data-task-id="historical"]')).toHaveCount(0);
  await expect(completed.locator('[data-task-open="historical"] .task-title')).toHaveText(unsafeTitle);
  await expect(completed.locator("img")).toHaveCount(0);
  await completed.locator('[data-task-open="historical"]').click();
  const drawer = page.locator("#task-drawer");
  await expect(drawer.locator("#drawer-heading")).toHaveText(unsafeTitle);
  await expect(drawer.locator("#drawer-heading img")).toHaveCount(0);
  await drawer.getByRole("button", { name: "重新打开", exact: true }).click();
  await expect.poll(() => state.tasks().find(item => item.id === "historical").status).toBe("not_started");
  expect(state.tasks().find(item => item.id === "historical").completedAt).toBe("");
  await expectActivity(page, "2026-10-20", { pending: 0, done: 0, overdue: 0 });
  await page.keyboard.press("Escape");
  await expect(page.locator('.day-panel-completed [data-task-id="historical"]')).toHaveCount(0);
  await cell(page, "2026-10-30").locator(".calendar-date-button").click();
  await page.locator('.day-panel-due [data-task-complete="future"]').click();
  await expect.poll(() => state.tasks().find(item => item.id === "future").status).toBe("completed");
  expect(state.tasks().find(item => item.id === "future").completedAt).toBe(today);
  await expectActivity(page, "2026-10-30", { pending: 0, done: 0, overdue: 0 });
  await expectActivity(page, today, { pending: 0, done: 1, overdue: 0 });
  await cell(page, today).locator(".calendar-date-button").click();
  await expect(page.locator('.day-panel-completed [data-task-id="future"]')).toBeVisible();
  await page.screenshot({ path: info.outputPath("calendar-activity-completed-day.png"), fullPage: true });
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "future").status).toBe("not_started");
  await expectActivity(page, today, { pending: 0, done: 0, overdue: 0 });
  await expectActivity(page, "2026-10-30", { pending: 1, done: 0, overdue: 0 });
  expect(await page.evaluate(() => window.__calendarActivityUnsafe)).toBeUndefined();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(state.writes).toHaveLength(3);
  noErrors(state);
});
