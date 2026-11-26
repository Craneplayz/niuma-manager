import { test, expect } from "@playwright/test";

test.use({ timezoneId: "Asia/Shanghai", reducedMotion: "reduce" });

test("a day's mobile agenda stays in reach while switching days and returning to the full month", async ({ page }, info) => {
  await page.clock.setFixedTime(new Date("2026-12-31T04:00:00Z"));
  await page.route("**/api/**", route => route.fulfill({ json: { environment: "test" } }));
  await page.goto("/?demo=1&view=calendar&anchor=2026-12-31&date=2026-12-31");
  const grid = page.locator("#calendar-grid");
  const panel = page.locator(".day-panel");
  const strip = panel.getByRole("navigation", { name: "当日议程选日", includeHidden: true });
  const add = panel.locator("#agenda-add-task");

  if (info.project.name === "desktop") {
    await expect(panel).toBeVisible();
    await expect(strip).toBeHidden();
    await expect(add).toBeHidden();
    return;
  }

  await grid.locator('.calendar-date-button[data-calendar-day="2026-12-31"]').click();
  const current = strip.locator('[data-agenda-day][aria-pressed="true"]');
  await expect(current).toBeFocused();
  await expect(current).toHaveAttribute("aria-current", "date");
  await expect(strip.locator("[data-agenda-day]")).toHaveCount(7);
  const height = page.viewportSize().height;
  await expect.poll(async () => (await strip.boundingBox()).y).toBeLessThan(55);
  await expect.poll(async () => (await add.boundingBox()).y).toBeLessThan(height);

  await current.press("ArrowRight");
  await expect(current).toBeFocused();
  await expect(current).toHaveAttribute("data-agenda-day", "2027-01-01");
  await expect(add).toHaveAttribute("data-quick-date", "2027-01-01");
  await page.screenshot({ path: info.outputPath("mobile-agenda.png") });
  await expect(panel.locator(".day-panel-heading")).toContainText("1 月 1 日");

  await strip.locator('[data-agenda-day="2027-01-03"]').click();
  await current.evaluate(button => {
    const touch = x => new Touch({ identifier: 1, target: button, clientX: x, clientY: 80 });
    button.dispatchEvent(new TouchEvent("touchstart", { bubbles: true, touches: [touch(220)] }));
    button.dispatchEvent(new TouchEvent("touchend", { bubbles: true, cancelable: true, changedTouches: [touch(100)] }));
  });
  await expect(current).toHaveAttribute("data-agenda-day", "2027-01-04");
  await expect(strip.locator("[data-agenda-day]").first()).toHaveAttribute("data-agenda-day", "2027-01-04");
  await strip.getByRole("button", { name: "查看上一周的同一天" }).click();
  await expect(add).toHaveAttribute("data-quick-date", "2026-12-28");
  await strip.getByRole("button", { name: "查看下一周的同一天" }).click();
  await expect(add).toHaveAttribute("data-quick-date", "2027-01-04");
  await page.goBack();
  await expect(add).toHaveAttribute("data-quick-date", "2026-12-28");

  await strip.getByRole("button", { name: /展开.*整月日历/ }).click();
  const selected = grid.locator('.calendar-date-button[aria-pressed="true"]');
  await expect(selected).toBeFocused();
  await expect(selected).toHaveAttribute("data-calendar-day", "2026-12-28");
  await expect(selected).toBeInViewport();
  await expect(page.locator(".calendar-month-grid")).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
