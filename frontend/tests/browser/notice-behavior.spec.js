import { test, expect } from "@playwright/test";

const due = (date, time) => ({ date, precision: time ? "datetime" : "date", ...(time ? { time } : {}) });
const notice = (id, title, deadline, status = "not_started") => ({
  id, title, content_type: "notice", status, due: deadline, course: "合成课程", plans: [], subtasks: [],
});

async function openApp(page, initialTasks) {
  let tasks = structuredClone(initialTasks), version = 1;
  const writes = [];
  await page.clock.setFixedTime(new Date("2026-10-18T04:00:00Z"));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value;
    if (path === "/api/auth/me") value = { user: { id: "synthetic", name: "合成账号", email: "notice@example.test" } };
    else if (path === "/api/workspace") value = { tasks: { version, value: tasks } };
    else if (path === "/api/data/tasks") {
      const body = request.postDataJSON();
      writes.push(body); tasks = body.value; value = { version: ++version, operationId: body.operation?.id };
    } else if (path === "/api/version") value = { environment: "test", revision: "issue179", managed: true };
    else if (path === "/api/usage/guest-visit") value = {};
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/recognition/batches") value = [];
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else throw new Error("Unmocked API: " + path);
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
  });
  await page.goto("/");
  await expect(page.locator(".loading-state")).toHaveCount(0);
  return { writes };
}

async function openNotices(page) {
  if (await page.locator("#sidebar-show").isVisible()) await page.locator("#sidebar-show").click();
  await page.locator('[data-scope="notices"]').click();
  await expect(page.getByRole("heading", { name: /^通知 \d+$/ })).toBeVisible();
}

const listTasks = () => [
  notice("notice-undated", "未注明时间的通知", null),
  notice("read-later", "已阅读的后续通知", due("2026-10-20", "18:00"), "completed"),
  notice("notice-later", "同日较晚的通知", due("2026-10-20", "18:00")),
  notice("read-earlier", "已阅读的较早通知", due("2026-10-10"), "completed"),
  notice("notice-earlier", "同日较早的通知", due("2026-10-20", "09:00")),
  notice("notice-overdue", "已经过期的未读通知", due("2026-10-12", "13:00")),
  notice("read-undated", "已阅读的无日期通知", null, "completed"),
  { id: "task-overdue", title: "已经过期的任务", status: "not_started", due: due("2026-10-12", "13:00"), plans: [], subtasks: [] },
];

test("notices sort unread before read, share overdue task color, and undo reading restores order", async ({ page }, info) => {
  const f = await openApp(page, listTasks());
  const taskRow = page.locator('[data-task-id="task-overdue"]');
  await expect(taskRow).toHaveClass(/is-overdue/);
  const overdueTaskColor = await taskRow.locator(".task-deadline").evaluate(element => getComputedStyle(element).color);
  await openNotices(page);
  const rows = page.locator(".task-list > .task-row");
  const order = () => rows.evaluateAll(elements => elements.map(element => element.dataset.taskId));
  const initialOrder = ["notice-overdue", "notice-earlier", "notice-later", "notice-undated", "read-earlier", "read-later", "read-undated"];
  await expect.poll(order).toEqual(initialOrder);
  const overdue = page.locator('[data-task-id="notice-overdue"]');
  await expect(overdue).toHaveClass(/is-overdue/);
  await expect(overdue.locator(".task-deadline")).toHaveCSS("color", overdueTaskColor);
  expect(overdueTaskColor).toBe("rgb(173, 84, 85)");
  await expect(page.locator('[data-task-id="read-earlier"]')).not.toHaveClass(/is-overdue/);
  await page.screenshot({ path: info.outputPath("notice-unread-overdue.png"), fullPage: true });
  await overdue.getByRole("checkbox", { name: "标记已阅读：已经过期的未读通知", exact: true }).click();
  await expect(page.locator("#toast")).toContainText("通知已标记为已阅读");
  await expect.poll(order).toEqual(["notice-earlier", "notice-later", "notice-undated", "read-earlier", "notice-overdue", "read-later", "read-undated"]);
  await expect(overdue).toHaveClass(/is-read/);
  await expect(overdue).not.toHaveClass(/is-overdue/);
  await expect(overdue.getByRole("checkbox")).toHaveAttribute("aria-checked", "true");
  expect(f.writes[0].value.find(task => task.id === "notice-overdue").status).toBe("completed");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("notice-read-order-and-overdue.png"), fullPage: true });
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect.poll(order).toEqual(initialOrder);
  await expect(overdue).toHaveClass(/is-overdue/);
  await expect(overdue.getByRole("checkbox")).toHaveAttribute("aria-checked", "false");
  expect(f.writes[1].value.find(task => task.id === "notice-overdue").status).toBe("not_started");
});

const calendarNotices = () => [
  notice("dated", "有具体时间的通知", due("2026-10-16", "18:30")),
  notice("read", "已阅读的日历通知", due("2026-10-16", "20:00"), "completed"),
  notice("undated", "待安排关键日期的通知", null),
  notice("read-undated", "已阅读的无日期通知", null, "completed"),
];

async function openCalendar(page, mode = "month") {
  await page.locator('[data-view="calendar"]').click();
  await page.locator("#calendar-jump").fill("2026-10-16");
  await page.locator(`[data-calendar-mode="${mode}"]`).click();
}

async function dragToDate(page, source, date) {
  await source.scrollIntoViewIfNeeded();
  const target = page.locator(`.calendar-view [data-calendar-drop-date="${date}"]`);
  const from = await source.boundingBox(), to = await target.locator(".calendar-date-button").boundingBox();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 10 });
  await expect(target).toHaveClass(/is-drop-target/);
  await expect(page.locator(".calendar-drag-preview")).toContainText("关键");
  await page.mouse.up();
  await expect(page.locator(".calendar-drag-preview")).toHaveCount(0);
  await expect(page.locator("#task-drawer")).not.toBeVisible();
}

for (const mode of ["month", "week"]) test(`${mode} calendar and day panel reschedule notices, preserve time, and support undo`, async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Date dragging uses the existing mouse gesture.");
  const f = await openApp(page, calendarNotices());
  await openCalendar(page, mode);
  await dragToDate(page, page.locator('.calendar-view [data-calendar-drag-task="dated"]'), "2026-10-17");
  await expect(page.locator("#toast")).toContainText("关键日期已改为 2026-10-17 18:30");
  expect(f.writes[0].value.find(task => task.id === "dated").due).toEqual(due("2026-10-17", "18:30"));
  await expect(page.locator('[data-calendar-drop-date="2026-10-17"] [data-task-open="dated"]')).toBeVisible();
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator('[data-calendar-drop-date="2026-10-16"] [data-task-open="dated"]')).toBeVisible();
  expect(f.writes[1].value.find(task => task.id === "dated").due).toEqual(due("2026-10-16", "18:30"));
  await page.locator('[data-calendar-day="2026-10-16"]').click();
  await expect(page.locator('.day-panel [data-task-open="read"]')).not.toHaveAttribute("data-calendar-drag-task");
  await dragToDate(page, page.locator('.day-panel [data-calendar-drag-task="dated"]'), "2026-10-18");
  await expect(page.locator("#toast")).toContainText("关键日期已改为 2026-10-18 18:30");
  expect(f.writes[2].value.find(task => task.id === "dated").due).toEqual(due("2026-10-18", "18:30"));
  await page.screenshot({ path: info.outputPath(`notice-calendar-${mode}-rescheduled.png`), fullPage: true });
  await page.locator('.calendar-view [data-task-open="dated"]').click();
  await expect(page.locator("#task-drawer")).toBeVisible();
  await expect(page.locator("#task-drawer")).toContainText("18:30");
});

test("undated notices can acquire a date and undo it while read notices cannot drag", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Date dragging uses the existing mouse gesture.");
  const f = await openApp(page, calendarNotices());
  await openCalendar(page);
  const read = page.locator('.calendar-view [data-task-open="read"]');
  await expect(read).not.toHaveAttribute("data-calendar-drag-task");
  const from = await read.boundingBox(), to = await page.locator('[data-calendar-day="2026-10-17"]').boundingBox();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 10 });
  await expect(page.locator(".calendar-drag-preview")).toHaveCount(0);
  await page.mouse.up();
  expect(f.writes).toHaveLength(0);
  await expect(page.locator('[data-calendar-drop-date="2026-10-16"] [data-task-open="read"]')).toBeVisible();
  await page.locator('[data-calendar-pane="undated"]').click();
  await expect(page.locator('.day-panel [data-task-open="read-undated"]')).not.toHaveAttribute("data-calendar-drag-task");
  await dragToDate(page, page.locator('.day-panel [data-calendar-drag-task="undated"]'), "2026-10-18");
  await expect(page.locator("#toast")).toContainText("关键日期已设为 2026-10-18");
  expect(f.writes[0].value.find(task => task.id === "undated").due).toEqual(due("2026-10-18"));
  await expect(page.locator('[data-calendar-drop-date="2026-10-18"] [data-task-open="undated"]')).toBeVisible();
  await expect(page.locator('.day-panel [data-task-open="undated"]')).toHaveCount(0);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator('.day-panel [data-calendar-drag-task="undated"]')).toBeVisible();
  expect(f.writes[1].value.find(task => task.id === "undated").due).toBeNull();
  await expect(page.locator('.day-panel [data-task-open="read-undated"]')).not.toHaveAttribute("data-calendar-drag-task");
  await page.screenshot({ path: info.outputPath("notice-calendar-undated.png"), fullPage: true });
});
