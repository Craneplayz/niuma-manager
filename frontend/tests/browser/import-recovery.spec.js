import { test, expect } from "@playwright/test";
import { syncRun } from "../sync-fixtures.js";

function failedRun(id, count, recovered) {
  const run = syncRun({ id, state: recovered === count ? "succeeded" : "failed", states: Array(count).fill("failed") });
  run.records.forEach((record, index) => {
    record.request_id = `${id}:${index}`;
    record.label = `课程附件 ${index + 1}`;
    if (index < recovered) record.recovered = true;
  });
  return run;
}

async function openHistory(page, runs, linked = null) {
  const writes = [];
  await page.route("**/api/**", async route => {
    const request = route.request(), path = decodeURIComponent(new URL(request.url()).pathname);
    if (request.method() === "POST" && path.startsWith("/api/sources/sync-runs")) writes.push(path);
    let value;
    if (path === "/api/auth/me") value = { user: { id: "synthetic", name: "合成账号" } };
    else if (path === "/api/workspace") value = { tasks: { version: 1, value: [] } };
    else if (path === "/api/version") value = { environment: "test", revision: "pr171", managed: true };
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/recognition/batches") value = [];
    else if (path === "/api/sources/sync-runs") value = { contract_version: "source-sync/0.1", runs };
    else if (linked && path === `/api/sources/sync-runs/${linked.run_id}`) value = linked;
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else if (path === "/api/sources/credentials") value = { configured: true, username: "synthetic" };
    else throw new Error("Unmocked API: " + path);
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
  });
  await page.goto("/");
  if (await page.locator("#sidebar-show").isVisible()) await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "导入内容", exact: true }).click();
  await page.getByRole("button", { name: "教学网", exact: true }).click();
  await page.locator("[data-source-sync-history] > summary").click();
  return writes;
}

test("recovery summaries and individual errors keep distinct colors on desktop and mobile", async ({ page }, info) => {
  const all = failedRun("all:recovered", 8, 8), partial = failedRun("part:recovered", 2, 1), unresolved = failedRun("not:recovered", 1, 0);
  const writes = await openHistory(page, [all, partial, unresolved]);
  const allRow = page.locator(`[id="sync-run-${all.run_id}"]`);
  await allRow.getByRole("button", { name: "错误 8 条 · 全部已恢复", exact: true }).click();
  await expect(allRow.locator(".sync-error-count-muted")).toHaveCSS("color", "rgb(119, 124, 130)");
  await expect(allRow.locator(".sync-error-summary-success")).toHaveCSS("color", "rgb(36, 112, 68)");
  await expect(allRow.locator(".sync-item-status-muted")).toHaveCount(8);
  await expect(allRow.locator(".sync-item-status-muted").first()).toHaveCSS("color", "rgb(119, 124, 130)");
  await expect(allRow.getByRole("button", { name: /处理未完成/ })).toHaveCount(0);

  const partialRow = page.locator(`[id="sync-run-${partial.run_id}"]`);
  await partialRow.getByRole("button", { name: "错误 2 条 · 部分已恢复 1 / 2", exact: true }).click();
  await expect(partialRow.locator(".sync-error-summary-review")).toHaveCSS("color", "rgb(148, 96, 0)");
  await expect(partialRow.locator(".sync-item-status-error")).toHaveCSS("color", "rgb(182, 57, 57)");
  await expect(partialRow.locator(".sync-item-status-muted")).toHaveCount(1);
  await expect(partialRow.getByRole("button", { name: "处理未完成 · 1", exact: true })).toBeVisible();
  const unresolvedRow = page.locator(`[id="sync-run-${unresolved.run_id}"]`);
  await expect(unresolvedRow.locator(".sync-error-count-error")).toHaveCSS("color", "rgb(182, 57, 57)");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(writes).toHaveLength(0);
  await page.screenshot({ path: info.outputPath("import-recovery-states.png"), fullPage: true });
});

test("a resolved whole collection exposes recovery navigation without obsolete password guidance or retry", async ({ page }) => {
  const run = syncRun({ id: "collection:failed", state: "succeeded", states: [] });
  const later = syncRun({ id: "collection:quiet", states: [] });
  run.error = { code: "collection_failed", message: "核对密码并重新获取", retryable: true };
  run.recovered_by = { run_id: later.run_id };
  const writes = await openHistory(page, [run], later);
  const row = page.locator(`[id="sync-run-${run.run_id}"]`);
  await row.getByRole("button", { name: "错误 1 条 · 全部已恢复", exact: true }).click();
  await row.getByRole("button", { name: "本轮导入", exact: true }).click();
  await expect(row.getByText("后续已恢复。", { exact: true })).toBeVisible();
  await expect(row).not.toContainText("核对密码");
  await expect(row.getByRole("button", { name: /处理未完成|处理本条/ })).toHaveCount(0);
  const link = row.getByRole("button", { name: "本轮导入：已恢复，查看后续采集批次", exact: true });
  await expect(link).toHaveCSS("color", "rgb(119, 124, 130)");
  await link.click();
  const target = page.locator(`[id="sync-run-${later.run_id}"]`);
  await expect(target).toBeVisible();
  await expect(target).toContainText("这次处理已完成，没有新增任务或待核对内容。");
  expect(writes).toHaveLength(0);
});
