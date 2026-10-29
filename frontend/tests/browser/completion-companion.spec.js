import { test, expect } from "@playwright/test";

test.use({ timezoneId: "Asia/Shanghai" });

const preferenceKey = "niuma.completion-companion.v1";
const task = (id, fields = {}) => ({
  id, title: `合成任务 ${id}`, content_type: "task", status: "not_started",
  due: { date: "2026-10-29", precision: "date" }, course: "数据结构", plans: [], subtasks: [], ...fields,
});
const companion = page => page.locator('#sidebar-companion[data-completion="true"]');
const complete = (page, id) => page.locator(`[data-task-id="${id}"] [data-task-complete="${id}"]`).click();
const gate = () => {
  let enter, release;
  return {
    entered: new Promise(resolve => { enter = resolve; }),
    waiting: new Promise(resolve => { release = resolve; }),
    enter: () => enter(),
    release: () => release(),
  };
};

function pendingReview() {
  return [{
    request: { request_id: "companion-review", contract_version: "0.2", records: [{ input_id: "source", platform: "teaching_web", text: "合成待核对任务", source_label: "教学网" }] },
    result: { status: "success", errors: [] },
    items: [{ id: "review-pending", state: "pending", version: 1, candidate: {
      input_id: "source", title: "合成待核对任务", description: "请确认内容", content_type: "task", notice_type: "create", course_or_project: "数据结构",
      deadline: { kind: "deadline", status: "explicit", precision: "date", value: "2026-10-29" },
      sources: [{ input_id: "source", platform: "teaching_web", label: "教学网", url: null }],
      evidence: [{ input_id: "source", quote: "合成待核对任务" }], needs_review: true, review_reasons: ["请确认内容"],
    } }],
  }];
}

async function openApp(page, { initialTasks = [task("one"), task("two"), task("three")], reviews = [], offerRating = false, onWrite } = {}) {
  await page.clock.setFixedTime(new Date("2026-10-29T04:00:00Z"));
  let tasks = structuredClone(initialTasks), version = 1;
  const batches = structuredClone(reviews), writes = [], resolutions = [], unknown = [], errors = [];
  let user = { id: "companion-synthetic", name: "合成账号", email: "companion@example.test", emailVerified: true };
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value, status = 200;
    if (path === "/api/auth/me") {
      status = user ? 200 : 401;
      value = user ? { user } : { error: "请先登录" };
    } else if (path === "/api/workspace") value = { tasks: { version, value: tasks }, trash: { version: 1, value: [] } };
    else if (path === "/api/data/tasks") {
      const body = structuredClone(request.postDataJSON());
      writes.push(body);
      const response = await onWrite?.({ index: writes.length, body, route });
      if (response) return route.fulfill(response);
      expect(body.version).toBe(version);
      tasks = structuredClone(body.value);
      value = { version: ++version };
    } else if (path.includes("/candidates/") && path.endsWith("/resolve")) {
      const id = decodeURIComponent(path.split("/")[4]), body = request.postDataJSON();
      resolutions.push({ id, ...body });
      const item = batches.flatMap(batch => batch.items).find(candidate => candidate.id === id);
      expect(body.version).toBe(item.version);
      item.version++;
      item.state = body.action === "reopen" ? "pending" : "applied";
      if (body.action === "create") {
        tasks.push(task("confirmed-" + id, { ...body.draft, status: body.complete ? "completed" : "not_started", completedAt: body.complete ? "2026-10-29" : "", recognitionHistory: [{ storageId: id, action: "create" }] }));
        version++;
      }
      value = { batches };
    } else if (path === "/api/auth/logout") { user = null; value = {}; }
    else if (path === "/api/version") value = { environment: "test", revision: "companion-synthetic", managed: true };
    else if (path === "/api/usage/visit") value = { offerRating };
    else if (path === "/api/usage/guest-visit") value = { saved: true };
    else if (path === "/api/usage/feedback") value = { saved: true };
    else if (path === "/api/recognition/batches") value = batches;
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else if (path === "/api/recognition/text-jobs") value = { contract_version: "text-import-jobs/0.1", jobs: [] };
    else if (path === "/api/sources/credentials") value = { configured: false };
    else if (path === "/api/sources/sync-runs") value = { contract_version: "source-sync/0.1", runs: [] };
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else if (path === "/api/materials/imports") value = { jobs: [] };
    else { unknown.push(path); return route.fulfill({ status: 404, json: { error: "Unknown synthetic API" } }); }
    await route.fulfill({ status, json: value });
  });
  await page.goto("/");
  await expect(page.locator(".loading-state")).toHaveCount(0);
  return { writes, resolutions, unknown, errors, tasks: () => structuredClone(tasks) };
}

async function navigate(page, label) {
  if (await page.locator("#sidebar-show").isVisible() && await page.locator("#sidebar-show").getAttribute("aria-expanded") !== "true") await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: label, exact: true }).click();
}

async function openAccount(page) {
  await navigate(page, "账号与数据");
  await expect(page.locator("#dialog")).toBeVisible();
  return page.locator("#dialog");
}

async function closeAccount(page) {
  await page.keyboard.press("Escape");
  await expect(page.locator("#dialog")).not.toBeVisible();
  // Opening account settings from the mobile navigation leaves that menu open.
  // Close it through its real toggle before interacting with the main content.
  if (await page.locator("#sidebar-show").isVisible() && await page.locator("#sidebar-toggle").getAttribute("aria-expanded") === "true") {
    await page.locator("#sidebar-toggle").click();
    await expect(page.locator("#main")).not.toHaveAttribute("inert", "");
  }
}

function noUnexpectedErrors(state) {
  expect(state.unknown).toEqual([]);
  expect(state.errors).toEqual([]);
}

test("a saved task gets one cow acknowledgement and undo restores its real status", async ({ page }, info) => {
  const save = gate();
  const state = await openApp(page, { onWrite: async ({ index }) => { if (index === 1) { save.enter(); await save.waiting; } } });
  await complete(page, "one");
  await save.entered;
  await expect(companion(page)).toHaveCount(0);
  save.release();
  await expect(companion(page)).toBeVisible();
  await expect(page.locator("#toast")).toContainText("任务已完成");
  await expect(companion(page)).toHaveAttribute("data-character", "cow");
  await expect(companion(page)).toHaveAttribute("data-count", "1");
  await expect(companion(page).locator("[data-companion-message]")).not.toBeEmpty();
  await expect(companion(page).locator(".companion-pet-sprite")).toBeVisible();
  await expect(companion(page)).toHaveAttribute("data-sprite-ready", "true");
  expect(state.tasks().find(item => item.id === "one").status).toBe("completed");
  await page.screenshot({ path: info.outputPath("completion-cow.png"), fullPage: true });
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "one").status).toBe("not_started");
  await expect(companion(page)).toHaveCount(0);
  await expect(page.locator('[data-task-id="one"]')).not.toHaveClass(/is-completed/);
  expect(state.writes).toHaveLength(2);
  noUnexpectedErrors(state);
});

test("rapid completions merge, a later appearance alternates, and undo affects only the latest task", async ({ page }, info) => {
  const state = await openApp(page, { initialTasks: ["one", "two", "three", "four", "five"].map(id => task(id)) });
  await complete(page, "one");
  await expect(companion(page)).toHaveAttribute("data-count", "1");
  await complete(page, "two");
  await expect(companion(page)).toHaveAttribute("data-count", "2");
  await expect(companion(page)).toHaveAttribute("data-character", "cow");
  await expect(companion(page)).toHaveAttribute("data-mode", "batch");
  // Date.now drives the merge window; advance the fixed clock beyond it.
  await page.clock.setFixedTime(new Date("2026-10-29T04:00:03.001Z"));
  await complete(page, "three");
  await expect(companion(page)).toHaveAttribute("data-character", "horse");
  await expect(companion(page)).toHaveAttribute("data-count", "1");
  await complete(page, "four");
  await expect(companion(page)).toHaveAttribute("data-count", "2");
  await expect(companion(page)).toHaveAttribute("data-character", "horse");
  await expect(page.locator('#sidebar-companion[data-completion="true"]')).toHaveCount(1);
  await page.screenshot({ path: info.outputPath("completion-horse-merged.png"), fullPage: true });
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "four").status).toBe("not_started");
  for (const id of ["one", "two", "three"]) expect(state.tasks().find(item => item.id === id).status).toBe("completed");
  await expect(companion(page)).toHaveCount(0);
  noUnexpectedErrors(state);
});

test("pending review tasks count as remaining work and the last completed task gets all-done copy", async ({ page }) => {
  const state = await openApp(page, { initialTasks: [task("one"), task("notice", { content_type: "notice" })], reviews: pendingReview() });
  await expect(page.locator('[data-review-id="review-pending"]')).toBeVisible();
  await complete(page, "one");
  await expect(companion(page)).toHaveAttribute("data-mode", "ordinary");
  await page.locator('[data-review-id="review-pending"]').getByRole("button", { name: "完成任务：合成待核对任务", exact: true }).click();
  await expect(companion(page)).toHaveAttribute("data-mode", "all-done");
  await expect(companion(page).locator("[data-companion-message]")).not.toContainText(/下一项|下一步|下一关|还有谁|剩下/);
  expect(state.resolutions).toHaveLength(1);
  expect(state.resolutions[0].complete).toBe(true);
  expect(state.tasks().find(item => item.id === "confirmed-review-pending").status).toBe("completed");
  expect(state.tasks().find(item => item.id === "notice").status).toBe("not_started");
  noUnexpectedErrors(state);
});

test("notice reading and reopening a task keep ordinary status feedback without a mascot", async ({ page }) => {
  const state = await openApp(page, { initialTasks: [task("one"), task("done", { status: "completed" }), task("notice", { content_type: "notice" })] });
  await navigate(page, "通知");
  await page.getByRole("checkbox", { name: "标记已阅读：合成任务 notice", exact: true }).click();
  await expect(page.locator("#toast")).toContainText("通知已标记为已阅读");
  await expect(companion(page)).toHaveCount(0);
  await navigate(page, "已完成");
  await complete(page, "done");
  await expect(page.locator("#toast")).toContainText("任务已重新打开");
  await expect(companion(page)).toHaveCount(0);
  expect(state.tasks().find(item => item.id === "done").status).toBe("not_started");
  noUnexpectedErrors(state);
});

test("a failed save shows recovery guidance and never celebrates an unconfirmed completion", async ({ page }) => {
  const state = await openApp(page, { onWrite: async () => ({ status: 503, json: { error: "Synthetic save unavailable" } }) });
  await complete(page, "one");
  await expect(page.locator("#sync-alert")).toContainText("无法确认修改是否已保存");
  await expect(companion(page)).toHaveCount(0);
  await expect(page.locator("#toast")).not.toContainText("任务已完成");
  expect(state.tasks().find(item => item.id === "one").status).toBe("not_started");
  expect(state.writes).toHaveLength(1);
  noUnexpectedErrors(state);
});

test("completion and undo stay reachable inside an open task-details modal", async ({ page }, info) => {
  const state = await openApp(page);
  await page.locator('[data-task-open="one"]').click();
  const drawer = page.locator("#task-drawer");
  await drawer.getByRole("button", { name: "完成任务", exact: true }).click();
  await expect(drawer).toBeVisible();
  await expect(companion(page)).toBeHidden();
  await expect(drawer.locator("#toast-undo")).toBeEnabled();
  await page.screenshot({ path: info.outputPath("completion-details.png"), fullPage: true });
  await drawer.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "one").status).toBe("not_started");
  await expect(drawer.getByRole("button", { name: "完成任务", exact: true })).toBeVisible();
  await expect(companion(page)).toHaveCount(0);
  noUnexpectedErrors(state);
});

test("completion preferences persist through reload and disabling preserves standard undo", async ({ page }) => {
  const state = await openApp(page);
  let account = await openAccount(page);
  await expect(account.locator('[data-companion-preference="enabled"]')).toBeChecked();
  const animated = account.locator('[data-companion-preference="animated"]');
  await expect(animated).toBeChecked();
  await animated.uncheck();
  await closeAccount(page);
  await complete(page, "one");
  await expect(companion(page)).toHaveAttribute("data-animated", "false");
  await expect(companion(page)).toHaveAttribute("data-playing", "false");
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "one").status).toBe("not_started");
  account = await openAccount(page);
  await account.locator('[data-companion-preference="enabled"]').uncheck();
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key)).enabled, preferenceKey)).toBe(false);
  await page.reload();
  await expect(page.locator(".loading-state")).toHaveCount(0);
  account = await openAccount(page);
  await expect(account.locator('[data-companion-preference="enabled"]')).not.toBeChecked();
  await expect(account.locator('[data-companion-preference="animated"]')).not.toBeChecked();
  await closeAccount(page);
  await complete(page, "one");
  await expect(page.locator("#toast")).toContainText("任务已完成");
  await expect(companion(page)).toBeHidden();
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "one").status).toBe("not_started");
  noUnexpectedErrors(state);
});

test("reduced motion keeps the character and copy without spatial animation or narrow-screen overflow", async ({ page }, info) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const state = await openApp(page, { offerRating: true });
  await expect(page.locator("#usage-rating")).toBeVisible();
  await complete(page, "one");
  await expect(companion(page)).toBeVisible();
  await expect(companion(page).locator(".companion-pet-sprite")).toBeVisible();
  await expect(companion(page).locator("[data-companion-message]")).not.toBeEmpty();
  const spatialAnimations = await companion(page).evaluate(element => element.getAnimations({ subtree: true }).filter(animation => animation.effect?.getKeyframes().some(frame => frame.transform && frame.transform !== "none")).length);
  expect(spatialAnimations).toBe(0);
  const toast = await companion(page).boundingBox(), viewport = page.viewportSize();
  expect(toast.x).toBeGreaterThanOrEqual(0);
  expect(toast.x + toast.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(toast.y).toBeGreaterThanOrEqual(0);
  expect(toast.y + toast.height).toBeLessThanOrEqual(viewport.height + 1);
  if (await page.locator("#usage-rating").isVisible()) {
    const rating = await page.locator("#usage-rating").boundingBox();
    expect(toast.x + toast.width <= rating.x || rating.x + rating.width <= toast.x || toast.y + toast.height <= rating.y || rating.y + rating.height <= toast.y).toBe(true);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("completion-reduced-motion.png"), fullPage: true });
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "one").status).toBe("not_started");
  await complete(page, "two");
  await expect(companion(page)).toBeVisible();
  await page.locator("#usage-rating").getByRole("button", { name: "3分", exact: true }).click();
  const ratingForm = page.locator("#usage-rating [data-rating-feedback]");
  await expect(ratingForm).toBeVisible();
  await ratingForm.getByRole("textbox", { name: "使用反馈（选填）", exact: true }).fill("合成界面反馈");
  await expect(ratingForm.getByRole("textbox", { name: "使用反馈（选填）", exact: true })).toHaveValue("合成界面反馈");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("completion-rating-priority.png"), fullPage: true });
  noUnexpectedErrors(state);
});

test("logout clears a completion and a delayed save cannot bring it back after account reset", async ({ page }) => {
  const save = gate();
  const state = await openApp(page, { onWrite: async ({ index }) => { if (index === 2) { save.enter(); await save.waiting; } } });
  await complete(page, "one");
  await expect(companion(page)).toBeVisible();
  // Opening account settings dismisses the completed feedback without undoing data.
  await openAccount(page);
  await expect(companion(page)).toHaveCount(0);
  await closeAccount(page);
  await complete(page, "two");
  await save.entered;
  const account = await openAccount(page);
  await account.getByRole("button", { name: "退出登录", exact: true }).click();
  save.release();
  await expect(page.locator("#toast")).toContainText("已退出登录");
  await expect(companion(page)).toHaveCount(0);
  await expect(page.locator("#toast-undo")).toHaveCount(0);
  await expect(page.locator(".account-entry")).toContainText("登录账号");
  // Let pending render microtasks and the exit transition finish after reset.
  await page.waitForTimeout(300);
  await expect(companion(page)).toHaveCount(0);
  noUnexpectedErrors(state);
});

test('an existing undo moves into newly opened task details and stays actionable', async ({ page }) => {
  const state = await openApp(page);
  await complete(page, 'one');
  await expect(page.locator('#toast-undo')).toBeVisible();
  await page.locator('[data-task-open="two"]').click();
  const undo = page.locator('#task-drawer #toast-undo');
  await expect(undo).toBeVisible();
  await expect(undo).toBeEnabled();
  await undo.click();
  await expect.poll(() => state.tasks().find(item => item.id === 'one').status).toBe('not_started');
  noUnexpectedErrors(state);
});
