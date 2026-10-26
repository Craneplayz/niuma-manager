import { test, expect } from "@playwright/test";

test.use({ timezoneId: "Asia/Shanghai" });

const day = "2026-10-26";
const names = ["概率论", "线性代数", "数据结构", "大学英语", "大学物理", "计算机导论", "中国近代史", "离散数学"];
const longCourse = "计算机系统结构与复杂软件工程综合设计实验课程（长课程名称检查）";
const unsafeCourse = '<img src=x onerror="window.__courseUnsafe=true">';
const task = (id, course, time = "18:00") => ({
  id, title: `合成任务 ${id}`, course, source: "教学网", status: "not_started",
  due: { date: day, time, precision: "datetime" }, plans: [], subtasks: [],
});

async function workspace(page) {
  await page.clock.setFixedTime(new Date("2026-10-26T04:00:00Z"));
  const tasks = names.map((course, index) => task("a" + index, course, String(8 + index).padStart(2, "0") + ":00"));
  tasks.push(task("same", names[0]), task("long", longCourse), task("unsafe", unsafeCourse), task("none", ""));
  const batches = [{
    request: { request_id: "course-review", contract_version: "0.2", records: [{ input_id: "source", platform: "teaching_web", text: "合成课程通知", source_label: "教学网" }] },
    result: { status: "success", errors: [] },
    items: [{
      id: "pending", state: "pending", version: 1,
      candidate: { input_id: "source", title: "待核对的数据结构任务", description: "合成课程核对",
        course_or_project: "数据结构", content_type: "task", notice_type: "create",
        deadline: { kind: "deadline", status: "explicit", precision: "date", value: day },
        sources: [{ input_id: "source", platform: "teaching_web", label: "教学网", url: null }],
        evidence: [{ input_id: "source", quote: "待核对的数据结构任务" }], review_reasons: ["请确认"], needs_review: true },
    }],
  }];
  let version = 1;
  const unknown = [];
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let value;
    if (path === "/api/auth/me") value = { user: { id: "course-browser-user", name: "合成账号", email: "course@example.test", emailVerified: true } };
    else if (path === "/api/workspace") value = { tasks: { version, value: tasks }, trash: { version: 1, value: [] } };
    else if (path === "/api/version") value = { environment: "test", revision: "course-synthetic" };
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/usage/guest-visit") value = { saved: true };
    else if (path === "/api/recognition/batches") value = batches;
    else if (path === "/api/recognition/text-jobs") value = { contract_version: "text-import-jobs/0.1", jobs: [] };
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else if (path === "/api/sources/credentials") value = { configured: false };
    else if (path === "/api/sources/sync-runs") value = { contract_version: "source-sync/0.1", runs: [] };
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else if (path === "/api/materials/imports") value = { jobs: [] };
    else { unknown.push(path); return route.fulfill({ status: 404, json: { error: "Unknown synthetic API" } }); }
    await route.fulfill({ json: value });
  });
  await page.goto("/");
  await expect(page.locator(".loading-state")).toHaveCount(0);
  await expect(page.locator('[data-review-id="pending"]')).toBeVisible();
  return { add: course => { tasks.push(task("added", course)); version++; }, unknown, errors };
}

const badge = (page, id) => page.locator(`[data-task-id="${id}"] .course-badge`);
const tone = locator => locator.getAttribute("data-course-tone");
const noOverflow = page => expect.poll(() => page.evaluate(width => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 || document.documentElement.clientWidth > width + 1 || innerWidth > width + 1, page.viewportSize().width)).toBe(false);

test("course identities retain colors through filtering, reload and additional courses", async ({ page }) => {
  const state = await workspace(page);
  const before = await Promise.all(names.map((_, index) => tone(badge(page, "a" + index))));
  expect(new Set(before).size).toBe(8);
  await expect(badge(page, "same")).toHaveAttribute("data-course-tone", before[0]);
  await page.locator("#course-filter").selectOption(names[0]);
  await expect(page.locator(".course-filter > .course-dot")).toHaveAttribute("data-course-tone", before[0]);
  await expect(page.locator(".task-row")).toHaveCount(2);
  await page.locator("#course-filter").selectOption("");
  state.add("新增的实验课程");
  await page.reload();
  await expect(page.locator(".loading-state")).toHaveCount(0);
  expect(await Promise.all(names.map((_, index) => tone(badge(page, "a" + index))))).toEqual(before);
  await expect(badge(page, "none")).toHaveAttribute("data-course-tone", "neutral");
  await expect(badge(page, "unsafe")).toHaveAttribute("title", unsafeCourse);
  await expect(badge(page, "unsafe").locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => window.__courseUnsafe)).toBeUndefined();
  const preferences = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith("niuma.course-colors.")).map(([, value]) => value).join(""));
  for (const name of [...names, longCourse, unsafeCourse]) expect(preferences).not.toContain(name);
  await noOverflow(page);
  expect(state.unknown).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("calendar course markers match the list and keep the month usable on small screens", async ({ page }) => {
  const state = await workspace(page);
  const identity = await tone(badge(page, "a0"));
  await page.locator('[data-view="calendar"]').click();
  const dayCell = page.locator(`[data-calendar-drop-date="${day}"]`);
  await expect(dayCell.locator(".calendar-day-course-dots > .course-dot")).toHaveCount(3);
  await expect(dayCell.locator(".calendar-date-button")).toHaveAttribute("aria-label", new RegExp(names[0]));
  await expect(dayCell.locator('[data-task-open="a0"] .course-badge')).toHaveAttribute("data-course-tone", identity);
  await expect(page.locator('.day-panel [data-task-id="a0"] .course-badge')).toHaveAttribute("data-course-tone", identity);
  const box = await page.locator(".calendar-month-grid").boundingBox();
  expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize().height + 1);
  if (page.viewportSize().width <= 700) await expect(dayCell.locator(".calendar-day-course-dots")).toBeVisible();
  await page.locator('[data-calendar-mode="week"]').click();
  await expect(page.locator('.calendar-week [data-task-open="a0"] .course-badge')).toHaveAttribute("data-course-tone", identity);
  await expect(page.locator('.calendar-week [data-task-open="a0"]')).toHaveAttribute("aria-label", new RegExp(names[0]));
  await noOverflow(page);
  expect(state.unknown).toEqual([]);
  expect(state.errors).toEqual([]);
});

test("course badges match in details, pending review and import while long names stay readable", async ({ page }) => {
  const state = await workspace(page);
  await noOverflow(page);
  const identity = await tone(badge(page, "a2"));
  const pending = page.locator('[data-review-id="pending"]');
  await expect(pending.locator(".course-badge")).toHaveAttribute("data-course-tone", identity);
  await pending.locator(".task-open").click();
  const drawer = page.locator("#task-drawer");
  await expect(drawer.locator(".drawer-properties .course-badge")).toHaveAttribute("data-course-tone", identity);
  await page.keyboard.press("Escape");
  await page.locator('[data-task-id="long"] .task-open').click();
  await expect(drawer.locator(".drawer-properties .course-name")).toHaveText(longCourse);
  await expect(drawer.locator(".drawer-properties .course-name")).toHaveCSS("white-space", "normal");
  await noOverflow(page);
  await page.keyboard.press("Escape");
  if (await page.locator("#sidebar-show").isVisible()) await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "导入内容", exact: true }).click();
  const candidate = page.locator(".candidate-row").filter({ hasText: "待核对的数据结构任务" });
  await expect(candidate.locator(".course-badge")).toHaveAttribute("data-course-tone", identity);
  await noOverflow(page);
  expect(state.unknown).toEqual([]);
  expect(state.errors).toEqual([]);
});
