import { test, expect } from "@playwright/test";

test.use({ timezoneId: "Asia/Shanghai" });

const today = "2026-10-29";
const noon = "2026-10-29T04:00:00Z";
const task = (id, date = today, fields = {}) => ({
  id, title: `合成任务 ${id}`, content_type: "task", status: "not_started",
  due: date ? { date, precision: "date" } : null,
  course: "数据结构", source: "教学网", plans: [], subtasks: [], ...fields,
});
const timed = (id, time, date = today) => task(id, date, { due: { date, time, precision: "datetime" } });
const done = (id, date = today) => task(id, "2026-11-12", { status: "completed", completedAt: date });
const cell = (page, date) => page.locator(`.calendar-day[data-calendar-drop-date="${date}"]`);
const region = (page, date, kind) => cell(page, date).locator(`.calendar-activity-region[data-calendar-activity="${kind}"][data-calendar-day="${date}"]`);
const groups = page => page.locator(".day-panel .day-panel-group[data-activity-group]");
const chip = (page, kind) => page.locator(`.day-panel [data-calendar-priority="${kind}"]`);

function waveTasks() {
  return [
    done("today-done"), timed("today-expired", "09:00"), timed("today-pending", "15:00"), task("today-date-only"),
    done("ratio-done-1", "2026-10-28"), done("ratio-done-2", "2026-10-28"), done("ratio-done-3", "2026-10-28"), task("ratio-overdue", "2026-10-28"),
    done("same-count-1", "2026-10-27"), done("same-count-2", "2026-10-27"), done("same-count-3", "2026-10-27"),
    task("future-one", "2026-10-30"), task("future-two", "2026-10-30", { course: "线性代数" }),
    task("same-future-one", "2026-10-31"), task("same-future-two", "2026-10-31"),
  ];
}

async function workspace(page, { tasks: initialTasks = waveTasks(), timers = false } = {}) {
  if (timers) await page.clock.install({ time: new Date(noon) });
  else await page.clock.setFixedTime(new Date(noon));
  let tasks = structuredClone(initialTasks), version = 1, reads = 0;
  const unknown = [], errors = [], writes = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value;
    if (path === "/api/auth/me") value = { user: { id: "waves-browser-user", name: "合成账号", email: "waves@example.test", emailVerified: true } };
    else if (path === "/api/workspace") { reads++; value = { tasks: { version, value: tasks }, trash: { version: 1, value: [] } }; }
    else if (path === "/api/data/tasks") {
      const body = structuredClone(request.postDataJSON());
      expect(body.version).toBe(version);
      writes.push(body);
      tasks = structuredClone(body.value);
      value = { version: ++version };
    } else if (path === "/api/version") value = { environment: "test", revision: "calendar-waves-synthetic" };
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/usage/guest-visit") value = { saved: true };
    else if (path === "/api/recognition/batches") value = [];
    else if (path === "/api/recognition/text-jobs") value = { contract_version: "text-import-jobs/0.1", jobs: [] };
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else if (path === "/api/sources/credentials") value = { configured: false };
    else if (path === "/api/sources/sync-runs") value = { contract_version: "source-sync/0.1", runs: [] };
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else if (path === "/api/materials/imports") value = { jobs: [] };
    else { unknown.push(path); return route.fulfill({ status: 404, json: { error: "Unknown synthetic API" } }); }
    await route.fulfill({ json: value });
  });
  await page.goto("/?view=calendar&mode=month&anchor=2026-10-29&date=2026-10-29");
  await expect(page.locator(".loading-state")).toHaveCount(0);
  await expect(page.locator(".calendar-month-grid")).toBeVisible();
  return { unknown, errors, writes, reads: () => reads, tasks: () => structuredClone(tasks) };
}

function noErrors(state) {
  expect(state.unknown).toEqual([]);
  expect(state.errors).toEqual([]);
}

async function clickPaintedRegion(page, date, kind) {
  const target = region(page, date, kind);
  await expect(target).toBeVisible();
  await target.scrollIntoViewIfNeeded();
  // clip-path regions share bounding boxes. Sample actual hit targets rather
  // than assuming the bounding-box centre belongs to the intended colour.
  const point = await target.evaluate(element => {
    const box = element.getBoundingClientRect();
    for (let row = 0; row < 23; row++) for (let column = 0; column < 19; column++) {
      const x = box.left + (column + .5) * box.width / 19;
      const y = box.top + (row + .5) * box.height / 23;
      if (document.elementFromPoint(x, y)?.closest(".calendar-activity-region") === element) return { x, y };
    }
    return null;
  });
  expect(point, `${date} ${kind} has a visible, clickable part of its painted surface`).not.toBeNull();
  await page.mouse.click(point.x, point.y);
}

async function expectGroupCount(page, total) {
  await expect(page.locator(".day-panel .day-panel-group .task-row")).toHaveCount(total);
}

test("today has three selectable wave regions and priority changes order while keeping every task", async ({ page }, info) => {
  const state = await workspace(page);
  for (const [kind, count] of [["done", 1], ["pending", 2], ["overdue", 1]]) {
    await expect(region(page, today, kind)).toHaveAttribute("data-count", String(count));
    await clickPaintedRegion(page, today, kind);
    await expect(chip(page, kind)).toHaveAttribute("aria-pressed", "true");
    await expect(groups(page).first()).toHaveAttribute("data-activity-group", kind);
    await expectGroupCount(page, 4);
    for (const id of ["today-done", "today-expired", "today-pending", "today-date-only"]) {
      await expect(page.locator(`.day-panel [data-task-id="${id}"]`)).toHaveCount(1);
    }
  }
  await page.goBack();
  await expect(chip(page, "pending")).toHaveAttribute("aria-pressed", "true");
  await expect(groups(page).first()).toHaveAttribute("data-activity-group", "pending");
  await page.goForward();
  await expect(chip(page, "overdue")).toHaveAttribute("aria-pressed", "true");
  await expectGroupCount(page, 4);
  await cell(page, today).locator(".calendar-date-button").click();
  await expect(chip(page, "all")).toHaveAttribute("aria-pressed", "true");
  await expectGroupCount(page, 4);
  await page.screenshot({ path: info.outputPath("calendar-three-waves-priority.png"), fullPage: true });
  expect(state.writes).toEqual([]);
  noErrors(state);
});

test("wave shares follow real counts, equal counts keep equal shades, and empty days remain plain", async ({ page }, info) => {
  const state = await workspace(page);
  const doneShare = Number(await region(page, "2026-10-28", "done").getAttribute("data-share"));
  const overdueShare = Number(await region(page, "2026-10-28", "overdue").getAttribute("data-share"));
  expect(doneShare / overdueShare).toBeCloseTo(3, 4);
  await expect(region(page, "2026-10-28", "done")).toHaveAttribute("data-count", "3");
  await expect(region(page, "2026-10-28", "overdue")).toHaveAttribute("data-count", "1");
  const color = (date, kind) => cell(page, date).locator(`.calendar-surface-region[data-activity="${kind}"]`).evaluate(element => getComputedStyle(element).fill);
  expect(await color("2026-10-28", "done")).toBe(await color("2026-10-27", "done"));
  expect(await color("2026-10-30", "pending")).toBe(await color("2026-10-31", "pending"));
  expect(await color(today, "pending")).toBe(await color("2026-10-30", "pending"));
  await expect(cell(page, "2026-10-30").locator(".calendar-activity-region")).toHaveCount(1);
  await expect(cell(page, "2026-10-24").locator(".calendar-activity-region")).toHaveCount(0);
  expect(["rgb(255, 255, 255)", "rgba(0, 0, 0, 0)"]).toContain(await cell(page, "2026-10-24").evaluate(element => getComputedStyle(element).backgroundColor));
  await page.screenshot({ path: info.outputPath("calendar-waves-count-shares.png"), fullPage: true });
  noErrors(state);
});

test("keyboard status selection and touch-size priority chips preserve the full narrow month and course markers", async ({ page }) => {
  const state = await workspace(page);
  await region(page, today, "overdue").focus();
  await page.keyboard.press("Enter");
  await expect(chip(page, "overdue")).toHaveAttribute("aria-pressed", "true");
  await expect(groups(page).first()).toHaveAttribute("data-activity-group", "overdue");
  await expectGroupCount(page, 4);
  for (const kind of ["all", "done", "pending", "overdue"]) {
    const box = await chip(page, kind).boundingBox();
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
  }
  await chip(page, "pending").click();
  await expect(groups(page).first()).toHaveAttribute("data-activity-group", "pending");
  await expectGroupCount(page, 4);
  await expect(cell(page, "2026-10-30").locator(".calendar-day-course-dots > .course-dot")).toHaveCount(2);
  if (page.viewportSize().width <= 700) await expect(cell(page, "2026-10-30").locator(".calendar-day-course-dots")).toBeVisible();
  // Selecting the lower panel may scroll on phones; the month still fits one
  // viewport once its top is restored, rather than acquiring an internal scroll.
  await page.evaluate(() => scrollTo(0, 0));
  const grid = await page.locator(".calendar-month-grid").boundingBox();
  expect(grid.y + grid.height).toBeLessThanOrEqual(page.viewportSize().height + 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  noErrors(state);
});

test("a timed deadline changes today's blue share to orange without refreshing or writing tasks", async ({ page }) => {
  const state = await workspace(page, { tasks: [done("already-done"), timed("turns-overdue", "12:01"), task("date-only")], timers: true });
  await expect(region(page, today, "pending")).toHaveAttribute("data-count", "2");
  await expect(region(page, today, "overdue")).toHaveCount(0);
  const url = page.url();
  await page.clock.fastForward(60_100);
  await expect(region(page, today, "overdue")).toHaveAttribute("data-count", "1");
  await expect(region(page, today, "pending")).toHaveAttribute("data-count", "1");
  await expect(region(page, today, "done")).toHaveAttribute("data-count", "1");
  await expect(page.locator('.day-panel-group[data-activity-group="overdue"] [data-task-id="turns-overdue"]')).toHaveCount(1);
  await expect(page.locator('.day-panel-group[data-activity-group="pending"] [data-task-id="date-only"]')).toHaveCount(1);
  expect(page.url()).toBe(url);
  expect(state.reads()).toBe(1);
  expect(state.writes).toEqual([]);
  noErrors(state);
});

test("focus falls back to the same date when the last blue region becomes overdue", async ({ page }) => {
  const state = await workspace(page, { tasks: [timed("only-timed-task", "12:01")], timers: true });
  const pending = region(page, today, "pending");
  await expect(pending).toHaveAttribute("data-count", "1");
  await clickPaintedRegion(page, today, "pending");
  await expect(pending).toBeFocused();
  await page.clock.fastForward(60_100);
  await expect(pending).toHaveCount(0);
  await expect(region(page, today, "overdue")).toHaveAttribute("data-count", "1");
  await expect(cell(page, today).locator(".calendar-date-button")).toBeFocused();
  expect(state.reads()).toBe(1);
  expect(state.writes).toEqual([]);
  noErrors(state);
});

test("reopening and undo move activity between the recorded completion day and the real timed deadline", async ({ page }) => {
  const historical = timed("historical", "09:00");
  const state = await workspace(page, { tasks: [{ ...historical, status: "completed", completedAt: "2026-10-28" }, timed("still-pending", "15:00")] });
  await expect(region(page, "2026-10-28", "done")).toHaveAttribute("data-count", "1");
  await expect(region(page, today, "overdue")).toHaveCount(0);
  await clickPaintedRegion(page, "2026-10-28", "done");
  await page.locator('.day-panel [data-task-open="historical"]').click();
  const drawer = page.locator("#task-drawer");
  await drawer.getByRole("button", { name: "重新打开", exact: true }).click();
  await expect.poll(() => state.tasks().find(item => item.id === "historical").status).toBe("not_started");
  await expect(region(page, "2026-10-28", "done")).toHaveCount(0);
  await expect(region(page, today, "overdue")).toHaveAttribute("data-count", "1");
  await page.keyboard.press("Escape");
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "historical").status).toBe("completed");
  expect(state.tasks().find(item => item.id === "historical").completedAt).toBe("2026-10-28");
  await expect(region(page, "2026-10-28", "done")).toHaveAttribute("data-count", "1");
  await expect(region(page, today, "overdue")).toHaveCount(0);
  expect(state.writes).toHaveLength(2);
  noErrors(state);
});
