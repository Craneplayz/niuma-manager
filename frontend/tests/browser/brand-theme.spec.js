import { test, expect } from "@playwright/test";

test.use({ timezoneId: "Asia/Shanghai" });

test("approved logo and white theme stay consistent across the workspace and admin login", async ({ page }, info) => {
  const errors = [], unexpected = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.clock.setFixedTime(new Date("2026-10-26T04:00:00Z"));
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/version") return route.fulfill({ json: { environment: "test", revision: "brand-synthetic" } });
    if (path === "/api/usage/guest-visit") return route.fulfill({ json: { saved: true } });
    if (path === "/api/admin/usage") return route.fulfill({ status: 401, json: { error: "请登录" } });
    unexpected.push(path);
    return route.fulfill({ status: 500, json: { error: "Unexpected synthetic API" } });
  });
  const fits = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.goto("/?demo=1");
  await expect(page.locator(".task-row").first()).toBeVisible();
  const logo = page.locator(".brand-logo");
  await expect.poll(() => logo.evaluate(image => image.complete && image.naturalWidth > 0)).toBe(true);
  const src = await logo.getAttribute("src");
  await expect(page.locator("html")).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await fits();
  await page.screenshot({ path: info.outputPath("brand-list.png"), fullPage: true });
  await page.locator('[data-view="calendar"]').click();
  await expect(page.locator(".calendar-month-grid")).toBeVisible();
  await fits();
  await page.screenshot({ path: info.outputPath("brand-calendar.png"), fullPage: true });
  if (await page.locator("#sidebar-show").isVisible()) await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "导入内容", exact: true }).click();
  await expect(page.getByRole("heading", { name: "导入", exact: true })).toBeVisible();
  await fits();
  await page.screenshot({ path: info.outputPath("brand-import.png"), fullPage: true });
  await page.goto("/admin.html");
  await expect(page.getByRole("heading", { name: "管理后台登录" })).toBeVisible();
  await expect(page.locator(".admin-brand img")).toHaveAttribute("src", src);
  await expect(page.locator("html")).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await fits();
  expect(unexpected).toEqual([]);
  expect(errors).toEqual([]);
});
