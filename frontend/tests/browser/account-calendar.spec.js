import { test, expect } from "@playwright/test";

async function openApp(page, { holdCredentials = false } = {}) {
  let tasks = [
    { id: "dated", title: "课程报告", status: "not_started", due: { date: "2026-10-16", time: "18:30", precision: "datetime" }, plans: [], subtasks: [] },
    { id: "undated", title: "补充阅读", status: "not_started", due: null, plans: [], subtasks: [] },
  ];
  let credentials = { configured: true, username: "campus-user" }, version = 1;
  let releaseCredentials, markCredentialRead;
  const credentialGate = new Promise(resolve => { releaseCredentials = resolve; });
  const credentialRead = new Promise(resolve => { markCredentialRead = resolve; });
  const writes = [], credentialCalls = [];
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value;
    if (path === "/api/auth/me") value = { user: { id: "synthetic", name: "测试账号", email: "test@example.test" } };
    else if (path === "/api/workspace") value = { tasks: { version, value: tasks } };
    else if (path === "/api/data/tasks") {
      const body = request.postDataJSON();
      writes.push(body); tasks = body.value; value = { version: ++version };
    } else if (path === "/api/sources/credentials") {
      credentialCalls.push(request.method());
      if (request.method() === "GET") {
        markCredentialRead();
        if (holdCredentials) await credentialGate;
      } else credentials = { configured: true, username: request.postDataJSON().username };
      value = credentials;
    } else if (path === "/api/sources/credentials/remove") {
      credentialCalls.push("REMOVE");
      value = credentials = { configured: false };
    } else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else if (path === "/api/version") value = { environment: "test", revision: "a7fe0a8", managed: true };
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else throw new Error("Unmocked API: " + path);
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
  });
  await page.goto("/");
  await expect(page.locator(".loading-state")).toHaveCount(0);
  return { writes, credentialCalls, credentialRead, releaseCredentials };
}

async function openAccount(page) {
  if (await page.locator("#sidebar-show").isVisible() && !await page.locator(".account-entry").isVisible())
    await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "账号与数据", exact: true }).click();
}

test("account management can update, remove and reconnect teaching credentials without visiting import", async ({ page }, info) => {
  const f = await openApp(page);
  await openAccount(page);
  const dialog = page.locator("#dialog");
  const entry = dialog.getByRole("region", { name: "教学网账号管理" });
  await expect(entry.getByText("教学网账号", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "修改密码", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("account-teaching-entry.png"), fullPage: true });
  await entry.getByRole("button", { name: "管理连接" }).click();
  await expect(dialog.getByLabel("教学网账号", { exact: true })).toHaveValue("campus-user");
  await expect(dialog.getByLabel("教学网密码", { exact: true })).toHaveValue("");
  await dialog.getByLabel("教学网账号", { exact: true }).fill("updated-campus");
  await dialog.getByLabel("教学网密码", { exact: true }).fill("Synthetic-password!");
  await dialog.getByRole("button", { name: "保存连接" }).click();
  await expect(dialog).not.toBeVisible();
  await openAccount(page);
  await dialog.getByRole("button", { name: "管理连接" }).click();
  await expect(dialog.getByLabel("教学网账号", { exact: true })).toHaveValue("updated-campus");
  await dialog.getByRole("button", { name: "移除连接" }).click();
  await expect(dialog).not.toBeVisible();
  await openAccount(page);
  await dialog.getByRole("button", { name: "管理连接" }).click();
  await expect(dialog.getByRole("button", { name: "移除连接" })).toHaveCount(0);
  await expect(dialog.getByLabel("教学网账号", { exact: true })).toHaveValue("");
  await dialog.getByLabel("教学网账号", { exact: true }).fill("reconnected-campus");
  await dialog.getByLabel("教学网密码", { exact: true }).fill("Synthetic-password!");
  await dialog.getByRole("button", { name: "保存连接" }).click();
  await expect(dialog).not.toBeVisible();
  expect(f.credentialCalls).toEqual(["GET", "POST", "REMOVE", "POST"]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("closing the account dialog during a credential read keeps it closed", async ({ page }) => {
  const f = await openApp(page, { holdCredentials: true });
  await openAccount(page);
  const dialog = page.locator("#dialog");
  await dialog.getByRole("button", { name: "管理连接" }).click();
  await f.credentialRead;
  await expect(dialog.getByRole("button", { name: "正在读取…" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  const response = page.waitForResponse("**/api/sources/credentials");
  f.releaseCredentials();
  await response;
  await expect(dialog).not.toBeVisible();
  await openAccount(page);
  await dialog.getByRole("button", { name: "管理连接" }).click();
  await expect(dialog.getByLabel("教学网账号", { exact: true })).toHaveValue("campus-user");
});

async function dragToDate(page, source, date) {
  await source.scrollIntoViewIfNeeded();
  const target = page.locator(`.calendar-view [data-calendar-drop-date="${date}"]`);
  const from = await source.boundingBox(), to = await target.locator(".calendar-date-button").boundingBox();
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 10 });
  await expect(target).toHaveClass(/is-drop-target/);
  await page.mouse.up();
  await expect(page.locator(".calendar-drag-preview")).toHaveCount(0);
  await expect(page.locator("#task-drawer")).not.toBeVisible();
}

for (const mode of ["month", "week"]) test(`${mode} calendar and day panel reschedule tasks with time and undo`, async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Date dragging uses the existing mouse gesture.");
  const f = await openApp(page);
  await page.locator('[data-view="calendar"]').click();
  await page.locator("#calendar-jump").fill("2026-10-16");
  await page.locator(`[data-calendar-mode="${mode}"]`).click();
  await dragToDate(page, page.locator('.calendar-view [data-calendar-drag-task="dated"]'), "2026-10-17");
  await expect(page.locator('#toast')).toContainText("截止日期已改为 2026-10-17 18:30");
  expect(f.writes[0].value.find(task => task.id === "dated").due).toEqual({ date: "2026-10-17", time: "18:30", precision: "datetime" });
  await expect(page.locator('[data-calendar-drop-date="2026-10-17"] [data-task-open="dated"]')).toBeVisible();
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator('[data-calendar-drop-date="2026-10-16"] [data-task-open="dated"]')).toBeVisible();
  expect(f.writes[1].value.find(task => task.id === "dated").due).toEqual({ date: "2026-10-16", time: "18:30", precision: "datetime" });
  await page.locator('[data-calendar-day="2026-10-16"]').click();
  await dragToDate(page, page.locator('.day-panel [data-calendar-drag-task="dated"]'), "2026-10-18");
  await expect(page.locator('#toast')).toContainText("截止日期已改为 2026-10-18 18:30");
  expect(f.writes[2].value.find(task => task.id === "dated").due).toEqual({ date: "2026-10-18", time: "18:30", precision: "datetime" });
  await page.screenshot({ path: info.outputPath(`calendar-${mode}-rescheduled.png`), fullPage: true });
  await page.locator('.calendar-view [data-task-open="dated"]').click();
  await expect(page.locator("#task-drawer")).toBeVisible();
  await expect(page.locator("#task-drawer")).toContainText("18:30");
});
