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

async function openHistory(page, runs, linked = null, { batches = [], tasks = [] } = {}) {
  const writes = [];
  let version = 1;
  await page.route("**/api/**", async route => {
    const request = route.request(), path = decodeURIComponent(new URL(request.url()).pathname);
    if (request.method() === "POST" && path.startsWith("/api/sources/sync-runs")) writes.push(path);
    let value;
    if (path === "/api/auth/me") value = { user: { id: "synthetic", name: "合成账号" } };
    else if (path === "/api/workspace") value = { tasks: { version, value: tasks } };
    else if (path === "/api/version") value = { environment: "test", revision: "pr171", managed: true };
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/recognition/batches") value = batches;
    else if (path.endsWith("/resolve")) {
      const id = path.split("/").at(-2), item = batches.flatMap(batch => batch.items).find(item => item.id === id);
      expect(request.postDataJSON().reviewed).toBe(true);
      item.state = "applied"; item.version++; version++;
      tasks.push({ id: `task-${id}`, title: item.candidate.title, status: "not_started", due: null, plans: [], subtasks: [],
        recognitionHistory: [{ storageId: id, action: "create" }] });
      value = { batches };
    }
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
  const allCategory = allRow.getByRole("button", { name: "错误 8 条", exact: true });
  await allCategory.click();
  await expect(allCategory).toHaveCSS("color", "rgb(36, 112, 68)");
  await expect(allCategory).toHaveText("错误 8 条");
  await expect(allCategory).toHaveAttribute("title", "已恢复 8 / 8 条；仍需处理 0 条。");
  await expect(allRow.locator(".sync-item-status-muted")).toHaveCount(8);
  await expect(allRow.locator(".sync-item-status-muted").first()).toHaveCSS("color", "rgb(119, 124, 130)");
  await expect(allRow.getByRole("button", { name: /处理未完成/ })).toHaveCount(0);

  const partialRow = page.locator(`[id="sync-run-${partial.run_id}"]`);
  const partialCategory = partialRow.getByRole("button", { name: "错误 2 条", exact: true });
  await partialCategory.click();
  await expect(partialCategory).toHaveCSS("color", "rgb(148, 96, 0)");
  await expect(partialCategory).toHaveText("错误 2 条");
  await expect(partialRow.locator(".sync-item-status-error")).toHaveCSS("color", "rgb(182, 57, 57)");
  await expect(partialRow.locator(".sync-item-status-muted")).toHaveCount(1);
  await expect(partialRow.getByRole("button", { name: "处理未完成 · 1", exact: true })).toBeVisible();
  const unresolvedRow = page.locator(`[id="sync-run-${unresolved.run_id}"]`);
  await expect(unresolvedRow.getByRole("button", { name: "错误 1 条", exact: true })).toHaveCSS("color", "rgb(182, 57, 57)");
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
  await row.getByRole("button", { name: "错误 1 条", exact: true }).click();
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
  await expect(target.locator(".sync-run-heading")).toHaveAttribute("data-source-sync-highlight", "");
  await expect.poll(async () => Math.abs((await target.boundingBox()).y - 24)).toBeLessThan(2);
  expect(writes).toHaveLength(0);
});

test("category priority follows recovered errors and confirmed reviews without changing the receipt totals", async ({ page }, info) => {
  const run = syncRun({ id: "priority:batch", state: "partial_success", states: ["succeeded", "failed"], added: 1, review: 1,
    updates: [{ task_id: "updated:task", label: "修改过的任务", course: null }] });
  run.records[0].added_items = [{ candidate_id: "auto", task_id: "created:task", label: "新增任务", course: null }];
  run.records[0].review_items = [{ candidate_id: "manual:review", label: "待核对任务", course: null }];
  const batches = [{ sourceSyncManaged: true, request: { contract_version: "0.2", request_id: "request:review", records: [] },
    result: { status: "success" }, items: [{ id: "manual:review", state: "pending", version: 1, candidate: {
      title: "待核对任务", description: "合成材料", course_or_project: "合成课程", notice_type: "create", needs_review: true,
      deadline: { kind: "deadline", status: "explicit", precision: "date", value: "2026-10-20" }, evidence: [], sources: [], review_reasons: [],
    } }] }];
  const writes = await openHistory(page, [run], null, { batches });
  const row = page.locator(`[id="sync-run-${run.run_id}"]`), categories = row.locator(".sync-category");
  await expect(categories).toHaveText(["错误 1 条", "待核对 1 项", "新增 1 项", "修改 1 项"]);
  await expect(row.getByRole("button", { name: "待核对 1 项", exact: true })).toHaveCSS("color", "rgb(148, 96, 0)");
  await page.screenshot({ path: info.outputPath("import-priority-pending.png"), fullPage: true });
  run.records[1].recovered = true; run.state = "succeeded"; run.revision++;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(categories).toHaveText(["待核对 1 项", "新增 1 项", "修改 1 项", "错误 1 条"]);
  await row.getByRole("button", { name: "待核对 1 项", exact: true }).click();
  await row.locator(".sync-result-name").click();
  const form = page.locator('[data-import-form="review"]');
  await expect(form).toBeVisible();
  await form.getByRole("button", { name: "确认加入任务", exact: true }).click();
  await expect(form).not.toBeVisible();
  await expect(categories).toHaveText(["新增 1 项", "修改 1 项", "待核对 1 项", "错误 1 条"]);
  await expect(row.getByRole("button", { name: "待核对 1 项", exact: true })).toHaveCSS("color", "rgb(36, 112, 68)");
  await expect(row.getByRole("button", { name: "待核对 1 项", exact: true })).toHaveAttribute("aria-expanded", "true");
  await expect(row.locator(".sync-item-status-muted")).toHaveText("未完成");
  expect(writes).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator("#toast")).not.toHaveClass(/visible/);
  await page.screenshot({ path: info.outputPath("import-priority-reviewed.png"), fullPage: true });
});

test("recovery highlights the matching source results and aligns its first item near the viewport top", async ({ page }, info) => {
  const old = failedRun("linked:old", 1, 1), target = syncRun({ id: "linked:success", states: ["succeeded", "succeeded"], added: 1 });
  target.records[0].request_id = "other:source";
  target.records[0].added_items = [{ candidate_id: "other", task_id: "other-task", label: "其他来源任务", course: null }];
  target.records[1].request_id = "matching:source"; target.records[1].added = 2; target.counts.added = 3;
  target.records[1].added_items = [{ candidate_id: "matching", task_id: "matching-task", label: "恢复来源对应任务", course: null },
    { candidate_id: "matching-second", task_id: "matching-task-second", label: "同一来源的第二条任务", course: null }];
  old.records[0].recovered_by = { run_id: target.run_id, request_id: "matching:source" };
  const tasks = [{ id: "matching-task", title: "恢复来源对应任务", status: "not_started" },
    { id: "matching-task-second", title: "同一来源的第二条任务", status: "completed" }];
  const writes = await openHistory(page, [old], target, { tasks }), row = page.locator(`[id="sync-run-${old.run_id}"]`);
  await expect(row.getByRole("button", { name: "错误 1 条", exact: true })).toHaveCSS("color", "rgb(36, 112, 68)");
  await row.getByRole("button", { name: "错误 1 条", exact: true }).click();
  await row.getByRole("button", { name: /已恢复，查看成功批次/ }).click();
  const result = page.locator(`[id="sync-item-${target.run_id}-added-1"]`);
  await expect(result).toBeInViewport();
  await expect(result).toContainText("恢复来源对应任务");
  await expect(result).toHaveAttribute("data-source-sync-highlight", "");
  await expect(result).toHaveCSS("background-color", "rgb(237, 243, 248)");
  await expect.poll(async () => Math.abs((await result.boundingBox()).y - 24)).toBeLessThan(2);
  await expect(page.locator("[data-source-sync-highlight]")).toHaveCount(2);
  await expect(page.locator(`[id="sync-item-${target.run_id}-added-0"]`)).not.toHaveAttribute("data-source-sync-highlight");
  await expect(page.locator("[data-source-sync-hint], .sync-recovery-hint")).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("import-recovery-highlight.png") });
  await page.mouse.wheel(0, 120);
  await page.keyboard.press("ArrowDown");
  await expect(page.locator("[data-source-sync-highlight]")).toHaveCount(2);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.locator("[data-source-sync-highlight]")).toHaveCount(2);
  const targetRow = page.locator(`[id="sync-run-${target.run_id}"]`);
  await targetRow.getByRole("button", { name: "新增 3 项", exact: true }).click();
  await expect(page.locator("[data-source-sync-highlight]")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.style.getPropertyValue("--recovery-scroll-space"))).toBe("");
  expect(writes).toHaveLength(0);
});

test("history separates notice type from completion and distinguishes reviewed cancellations from removals", async ({ page }, info) => {
  const run = syncRun({ id: "result:states", added: 4, review: 2 });
  run.records[0].added_items = [
    { candidate_id: "unread", task_id: "unread", label: "上课地点调整", course: null },
    { candidate_id: "read", task_id: "read", label: "活动安排", course: null },
    { candidate_id: "trash", task_id: "trash", label: "旧课程通知", course: null },
    { candidate_id: "removed", task_id: "removed", label: "原先新增的任务", course: null },
  ];
  run.records[0].review_items = [
    { candidate_id: "cancel", label: "取消原定报告", course: null },
    { candidate_id: "pending", label: "待确认的考试地点", course: null },
  ];
  const tasks = [
    { id: "unread", title: "上课地点调整", content_type: "notice", status: "not_started" },
    { id: "read", title: "活动安排", content_type: "notice", status: "completed" },
    { id: "trash", title: "旧课程通知", content_type: "notice", status: "cancelled", recycleBin: { status: "not_started" } },
    { id: "cancel", title: "原定报告", status: "cancelled", recognitionHistory: [{ storageId: "cancel", action: "cancel" }] },
  ];
  const batches = [{ sourceSyncManaged: true, request: { contract_version: "0.2", request_id: "states", records: [] },
    result: { status: "success" }, items: [
      { id: "removed", state: "applied", autoApplied: true, candidate: { title: "原先新增的任务" } },
      { id: "cancel", state: "applied", candidate: { title: "取消原定报告" } },
      { id: "pending", state: "pending", candidate: { title: "待确认的考试地点", content_type: "notice" } },
    ] }];
  const writes = await openHistory(page, [run], null, { tasks, batches });
  const row = page.locator(`[id="sync-run-${run.run_id}"]`);
  await row.getByRole("button", { name: "新增 4 项", exact: true }).click();
  const names = row.locator(".sync-result-name");
  await expect(names.locator(".sync-item-status")).toHaveText(["未完成", "✓ 已完成", "回收站", "已移除"]);
  await expect(names.locator(".notice-label")).toHaveCount(3);
  await expect(names.first().locator(".notice-label")).toHaveCSS("font-size", "10px");
  await expect(names.first().locator(".notice-label")).toHaveCSS("border-radius", "4px");
  expect(await names.first().evaluate(element => element.firstElementChild.classList.contains("notice-label"))).toBe(true);
  await expect(row).not.toContainText("已核对");
  await expect(row).not.toContainText("待确认的考试地点");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("import-history-result-statuses.png"), fullPage: true });
  await row.getByRole("button", { name: "待核对 2 项", exact: true }).click();
  await expect(names.locator(".sync-item-status")).toHaveText(["已取消", "待核对"]);
  await expect(names.nth(1).locator(".notice-label")).toHaveText("通知");
  await names.first().click();
  await expect(page.locator("#toast")).toContainText("这条任务或通知已取消");
  expect(writes).toHaveLength(0);
});
