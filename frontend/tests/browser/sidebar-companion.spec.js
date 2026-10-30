import { test, expect } from "@playwright/test";

test.use({ timezoneId: "Asia/Shanghai" });

const today = "2026-10-29";
const preferenceKey = "niuma.completion-companion.v1";
const mascot = page => page.locator("#sidebar-companion");
const button = page => page.locator("#sidebar-companion-button");
const speech = page => page.locator("#sidebar-companion-speech");
const task = (id, fields = {}) => ({
  id, title: `合成任务 ${id}`, content_type: "task", status: "not_started",
  due: { date: today, precision: "date" }, course: "数据结构", source: "教学网", plans: [], subtasks: [], ...fields,
});
const overdueTasks = () => ["one", "two", "three"].map(id => task(id, { due: { date: "2026-10-27", precision: "date" } }));
const completedTasks = () => ["done-one", "done-two", "done-three"].map(id => task(id, { status: "completed", completedAt: today }));

function pendingReview() {
  return [{
    request: { request_id: "sidebar-review", contract_version: "0.2", records: [{ input_id: "source", platform: "teaching_web", text: "合成待核对任务", source_label: "教学网" }] },
    result: { status: "success", errors: [] },
    items: [{ id: "sidebar-pending", state: "pending", version: 1, candidate: {
      input_id: "source", title: "未保存的待核对任务", description: "确认后才入库", content_type: "task", notice_type: "create", course_or_project: "线性代数",
      deadline: { kind: "deadline", status: "explicit", precision: "date", value: "2026-10-20" },
      sources: [{ input_id: "source", platform: "teaching_web", label: "教学网", url: null }],
      evidence: [{ input_id: "source", quote: "合成待核对任务" }], needs_review: true, review_reasons: ["请确认"],
    } }],
  }];
}

async function workspace(page, { tasks: initialTasks = overdueTasks(), reviews = [], onWrite,
  time = "2026-10-29T04:00:00Z", runningClock = false } = {}) {
  if (runningClock) await page.clock.install({ time: new Date(time) });
  else await page.clock.setFixedTime(new Date(time));
  let tasks = structuredClone(initialTasks), version = 1;
  let user = { id: "sidebar-synthetic", name: "合成账号", email: "sidebar@example.test", emailVerified: true };
  const writes = [], unknown = [], errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value, status = 200;
    if (path === "/api/auth/me") { status = user ? 200 : 401; value = user ? { user } : { error: "请先登录" }; }
    else if (path === "/api/workspace") value = { tasks: { version, value: tasks }, trash: { version: 1, value: [] } };
    else if (path === "/api/data/tasks") {
      const body = structuredClone(request.postDataJSON()); writes.push(body);
      const response = await onWrite?.({ index: writes.length, body });
      if (response) return route.fulfill(response);
      expect(body.version).toBe(version);
      tasks = structuredClone(body.value); value = { version: ++version };
    } else if (path === "/api/auth/logout") { user = null; value = {}; }
    else if (path === "/api/version") value = { environment: "test", revision: "sidebar-synthetic" };
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
    await route.fulfill({ status, json: value });
  });
  await page.goto("/");
  await expect(page.locator(".loading-state")).toHaveCount(0);
  return { writes, unknown, errors, tasks: () => structuredClone(tasks) };
}

async function showSidebar(page) {
  await expect(button(page)).toBeVisible();
}
async function showMain(page) {
  if (page.viewportSize().width <= 700 && await page.locator("#sidebar-toggle").getAttribute("aria-expanded") === "true") await page.locator("#sidebar-toggle").click();
}
async function talk(page) {
  await showSidebar(page);
  await button(page).focus();
  await expect(speech(page)).toBeVisible();
}
function clean(state) { expect(state.unknown).toEqual([]); expect(state.errors).toEqual([]); }

test("the stern checklist horse counts only saved tasks and ignores list filters", async ({ page }, info) => {
  const state = await workspace(page, { tasks: [
    ...overdueTasks(), ...completedTasks().slice(0, 2),
    task("unread-notice", { content_type: "notice", due: { date: "2026-10-20", precision: "date" } }),
    task("read-notice", { content_type: "notice", status: "completed", completedAt: today }),
    task("cancelled", { status: "cancelled", due: { date: "2026-10-20", precision: "date" } }),
    task("trashed", { status: "cancelled", due: { date: "2026-10-20", precision: "date" }, recycleBin: { deletedAt: "2026-10-28T10:00:00Z", status: "not_started" } }),
  ], reviews: pendingReview() });
  await expect(page.locator('[data-review-id="sidebar-pending"]')).toBeVisible();
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-high");
  await expect(mascot(page)).toHaveAttribute("data-character", "horse");
  await expect(mascot(page)).toHaveAttribute("data-sprite-ready", "true");
  await talk(page);
  await expect(speech(page)).toContainText("今天做完 2 项");
  await expect(speech(page)).toContainText("3 项逾期");
  await page.keyboard.press("Escape");
  await showMain(page);
  await page.locator("#task-search").fill("不会匹配任何任务的合成查询");
  await page.locator("#course-filter").selectOption("线性代数");
  await expect(page.locator(".task-row")).toHaveCount(0);
  await page.locator('[data-view="calendar"]').click();
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-high");
  await talk(page);
  await expect(speech(page)).toContainText("3 项逾期");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("sidebar-horse-checklist.png"), fullPage: true });
  clean(state);
});

test("three completions today produce the dancing cow and ordinary rerenders do not replay its finite dance", async ({ page }, info) => {
  const state = await workspace(page, { tasks: [
    ...completedTasks(), task("future", { due: { date: "2026-11-02", precision: "date" } }),
    task("yesterday", { status: "completed", completedAt: "2026-10-28" }),
    task("read-notice", { content_type: "notice", status: "completed", completedAt: today }),
  ] });
  await expect(mascot(page)).toHaveAttribute("data-state", "progress");
  await expect(mascot(page)).toHaveAttribute("data-character", "cow");
  await expect(mascot(page)).toHaveAttribute("data-sprite-ready", "true");
  await talk(page);
  await expect(speech(page)).toContainText("今天做完 3 项");
  const caption = await speech(page).boundingBox(), figure = await button(page).boundingBox();
  expect(caption.y).toBeGreaterThanOrEqual(figure.y + figure.height);
  await expect(mascot(page)).toHaveAttribute("data-playing", "false", { timeout: 4000 });
  await page.keyboard.press("Escape");
  await showMain(page);
  await page.locator('[data-view="calendar"]').click();
  await showSidebar(page);
  await expect(mascot(page)).toHaveAttribute("data-playing", "false");
  expect(await mascot(page).evaluate(element => element.getAnimations({ subtree: true }).filter(animation => animation.playState === "running").length)).toBe(0);
  await page.screenshot({ path: info.outputPath("sidebar-cow-dance.png"), fullPage: true });
  clean(state);
});

test("speech works by keyboard, pointer and touch, stays open across the hover gap, and closes with Escape or outside action", async ({ page }, info) => {
  const state = await workspace(page);
  await talk(page);
  await expect(button(page)).toHaveAttribute("aria-describedby", "sidebar-companion-speech");
  await expect(button(page)).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Escape");
  await expect(speech(page)).not.toBeVisible();
  if (info.project.name === "desktop") {
    await page.locator("#page-title").click();
    await button(page).hover();
    await expect(speech(page)).toBeVisible();
    await speech(page).hover();
    await page.waitForTimeout(350);
    await expect(speech(page)).toBeVisible();
    await page.locator("#page-title").click();
  } else {
    await button(page).tap();
    await expect(speech(page)).toBeVisible();
    await page.locator("#page-title").tap();
  }
  await expect(speech(page)).not.toBeVisible();
  clean(state);
});

test("an optimistic or failed completion never changes the sidebar's confirmed counts", async ({ page }) => {
  let enter, release;
  const entered = new Promise(resolve => { enter = resolve; });
  const waiting = new Promise(resolve => { release = resolve; });
  const state = await workspace(page, { onWrite: async () => { enter(); await waiting; return { status: 503, json: { error: "Synthetic save unavailable" } }; } });
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-high");
  await page.locator('[data-task-complete="one"]').click();
  await entered;
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-high");
  expect(state.tasks().find(item => item.id === "one").status).toBe("not_started");
  release();
  await expect(page.locator("#sync-alert")).toContainText("无法确认修改是否已保存");
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-high");
  await talk(page);
  await expect(speech(page)).toContainText("3 项逾期");
  expect(state.tasks().find(item => item.id === "one").status).toBe("not_started");
  clean(state);
});

test("saving three overdue completions changes checklist horse to gentle horse then dancing cow, and undo reverses the last change", async ({ page }) => {
  const state = await workspace(page);
  await page.locator('[data-task-complete="one"]').click();
  await expect.poll(() => state.writes.length).toBe(1);
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-low");
  await page.locator('[data-task-complete="two"]').click();
  await expect.poll(() => state.writes.length).toBe(2);
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-low");
  await page.locator('[data-task-complete="three"]').click();
  await expect(mascot(page)).toHaveAttribute("data-state", "progress");
  await expect(mascot(page)).toHaveAttribute("data-sprite-ready", "true");
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "three").status).toBe("not_started");
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-low");
  await talk(page);
  await expect(speech(page)).toContainText("今天做完 2 项");
  await expect(speech(page)).toContainText("1 项逾期");
  clean(state);
});

test("shared animation preferences and reduced motion keep static companions, with a usable compact or mobile target", async ({ page }, info) => {
  const state = await workspace(page, { tasks: completedTasks() });
  await page.evaluate(key => localStorage.setItem(key, JSON.stringify({ enabled: true, animated: false })), preferenceKey);
  await page.reload();
  await expect(page.locator(".loading-state")).toHaveCount(0);
  await showSidebar(page);
  await expect(mascot(page)).toHaveAttribute("data-playing", "false");
  expect(await mascot(page).evaluate(element => element.getAnimations({ subtree: true }).filter(animation => animation.playState === "running").length)).toBe(0);
  await page.evaluate(key => localStorage.setItem(key, JSON.stringify({ enabled: true, animated: true })), preferenceKey);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  await expect(page.locator(".loading-state")).toHaveCount(0);
  await showSidebar(page);
  if (info.project.name === "desktop") {
    await page.locator("#sidebar-toggle").click();
    await expect(mascot(page)).toHaveAttribute("data-compact", "true");
  }
  await expect(mascot(page)).toHaveAttribute("data-playing", "false");
  const bounds = await button(page).boundingBox();
  expect(bounds.width).toBeGreaterThanOrEqual(44);
  expect(bounds.height).toBeGreaterThanOrEqual(44);
  await button(page).focus();
  await expect(speech(page)).toBeVisible();
  const bubble = await speech(page).boundingBox();
  expect(bubble.x).toBeGreaterThanOrEqual(0);
  expect(bubble.x + bubble.width).toBeLessThanOrEqual(page.viewportSize().width + 1);
  expect(bubble.y).toBeGreaterThanOrEqual(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  clean(state);
});

test("midnight refresh changes the companion without replacing an unsent import draft", async ({ page }) => {
  const state = await workspace(page, { tasks: [...completedTasks(), task("tonight")], time: "2026-10-29T15:58:00Z", runningClock: true });
  await expect(mascot(page)).toHaveAttribute("data-state", "progress");
  if (page.viewportSize().width <= 700) await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "导入内容", exact: true }).click();
  await expect(page.locator("#import-text")).toBeVisible();
  const draft = "这段合成草稿尚未识别，跨过午夜也必须保留。";
  await page.locator("#import-text").fill(draft);
  await page.locator("#import-text").focus();
  await page.clock.fastForward(121000);
  await expect(page.locator(".main-date")).toHaveAttribute("datetime", "2026-10-30");
  await expect(page.locator("#import-text")).toHaveValue(draft);
  await expect(page.locator("#import-text")).toBeFocused();
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-low");
  expect(state.writes).toHaveLength(0);
  clean(state);
});

test("logout clears the previous account's mascot counts and open speech", async ({ page }) => {
  const state = await workspace(page);
  await talk(page);
  await expect(speech(page)).toContainText("3 项逾期");
  if (page.viewportSize().width <= 700) await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "账号与数据", exact: true }).click();
  await expect(speech(page)).not.toBeVisible();
  await page.locator("#dialog").getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.locator(".account-entry")).toContainText("登录账号");
  await expect(mascot(page)).not.toHaveAttribute("data-state", "overdue-high");
  expect((await speech(page).allTextContents()).join("")).not.toContain("3 项逾期");
  expect(state.writes).toHaveLength(0);
  clean(state);
});

test("passing pointers do not change the pet and hover only reveals its caption", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Pointer proximity is meaningful only for a mouse.");
  const state = await workspace(page, { tasks: [task("future")] });
  await expect(mascot(page)).toHaveAttribute("data-sprite-ready", "true");
  const before = await mascot(page).boundingBox(), target = await button(page).boundingBox();
  const frame = await mascot(page).getAttribute("data-frame");
  const count = await mascot(page).getAttribute("data-motion-count");
  await page.mouse.move(target.x + target.width + 70, target.y + target.height / 2);
  await page.waitForTimeout(120);
  await expect(mascot(page)).toHaveAttribute("data-frame", frame);
  await button(page).hover();
  await page.waitForTimeout(180);
  await expect(speech(page)).not.toBeVisible();
  await expect(speech(page)).toBeVisible();
  await expect(mascot(page)).toHaveAttribute("data-playing", "false");
  await expect(mascot(page)).toHaveAttribute("data-motion-count", count);
  const after = await mascot(page).boundingBox();
  expect(after.x).toBe(before.x); expect(after.y).toBe(before.y);
  await button(page).click();
  await expect(mascot(page)).toHaveAttribute("data-action", "wave");
  const cells = new Set();
  for (let i = 0; i < 16; i++) {
    cells.add(await mascot(page).getAttribute("data-frame"));
    await page.waitForTimeout(85);
  }
  expect(cells.size).toBeGreaterThanOrEqual(10);
  await expect(mascot(page)).toHaveAttribute("data-playing", "false");
  clean(state);
});

test("dragging picks up the character, docks at an edge, and keeps undo separately reachable", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Mouse dragging uses pointer capture; touch has the same runtime pointer path.");
  const state = await workspace(page, { tasks: [task("one"), task("two")] });
  await page.locator('[data-task-complete="one"]').click();
  await expect(mascot(page)).toHaveAttribute("data-completion", "true");
  const b = await button(page).boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(1350, 760, { steps: 8 });
  await expect(mascot(page)).toHaveAttribute("data-dragging", "true");
  await expect(mascot(page)).toHaveAttribute("data-frame", "13");
  await page.mouse.up();
  await expect(mascot(page)).toHaveAttribute("data-edge", "right");
  await expect(mascot(page)).toHaveAttribute("data-dragging", "false");
  const pet = await mascot(page).boundingBox(), undo = await page.locator("#toast-undo").boundingBox();
  expect(pet.y + pet.height <= undo.y || pet.x + pet.width <= undo.x || pet.x >= undo.x + undo.width).toBe(true);
  await page.locator("#toast-undo").click();
  await expect.poll(() => state.tasks().find(item => item.id === "one").status).toBe("not_started");
  await button(page).focus();
  await page.keyboard.press("Home");
  await expect(mascot(page)).toHaveAttribute("data-edge", "left");
  clean(state);
});

test("keyboard docking stays within a narrow viewport and Escape closes the completion caption", async ({ page }) => {
  const state = await workspace(page, { tasks: [task("one"), task("two")] });
  await page.locator('[data-task-complete="one"]').click();
  await expect(mascot(page)).toHaveAttribute("data-completion", "true");
  await button(page).focus();
  await page.keyboard.press("ArrowRight");
  for (let i = 0; i < 24; i++) await page.keyboard.press("ArrowDown");
  const bounds = await mascot(page).boundingBox(), view = page.viewportSize();
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(view.width);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(view.height);
  await page.keyboard.press("Escape");
  await expect(mascot(page)).toHaveAttribute("data-completion", "false");
  await expect(speech(page)).not.toBeVisible();
  clean(state);
});

test("the fixed pet hides during task details and navigation overlays, then returns with the same mood", async ({ page }, info) => {
  const state = await workspace(page);
  await expect(mascot(page)).toBeVisible();
  if (info.project.name !== "desktop") {
    await page.locator("#sidebar-show").click();
    await expect(mascot(page)).not.toBeVisible();
    await page.locator("#sidebar-toggle").click();
    await expect(mascot(page)).toBeVisible();
  }
  await page.locator('[data-task-open="one"]').click();
  await expect(page.locator("#task-drawer")).toBeVisible();
  await expect(mascot(page)).not.toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator("#task-drawer")).not.toBeVisible();
  await expect(mascot(page)).toBeVisible();
  await expect(mascot(page)).toHaveAttribute("data-state", "overdue-high");
  clean(state);
});

async function isolatedPet(page, character = "cow", pose = "arrive") {
  await page.route("**/pet-runtime-harness", route => route.fulfill({ contentType: "text/html", body: `<!doctype html><html><head><link rel="stylesheet" href="/src/styles.css"><link rel="stylesheet" href="/src/sidebar-companion.css"></head><body><aside id="sidebar" style="width:240px;height:100vh"><nav class="task-nav"></nav><div id="sidebar-companion-slot"></div><div class="sidebar-bottom" style="position:fixed;bottom:16px;height:60px"></div></aside></body></html>` }));
  await page.goto("/pet-runtime-harness");
  await page.evaluate(async ({ character, pose }) => {
    const { createSidebarCompanion } = await import("/src/sidebar-companion.js");
    window.petAllowed = true;
    window.pet = createSidebarCompanion({ canInteract: () => window.petAllowed });
    window.pet.update({ key: "synthetic-mood", character, pose, message: "合成的日常状态", ready: true });
    window.pet.mount(document.querySelector("#sidebar-companion-slot"));
  }, { character, pose });
  await expect(mascot(page)).toHaveAttribute("data-sprite-ready", "true");
}
async function harnessCompletion(page, line = "合成的完成回应") {
  await page.evaluate(line => window.pet.showCompletion({ character: "cow", message: { line, title: "合成完成", mode: "ordinary" }, count: 1 }), line);
  await expect(mascot(page)).toHaveAttribute("data-character", "cow");
  await expect(mascot(page)).toHaveAttribute("data-playing", "false", { timeout: 5000 });
}

test("same-character expiry settles in place and fades the old caption without replacing the actor", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "The same state machine is exercised at all widths by completion tests.");
  await isolatedPet(page);
  await harnessCompletion(page);
  const figure = await button(page).boundingBox();
  await page.evaluate(() => {
    window.returnCharacters = [];
    new MutationObserver(() => window.returnCharacters.push(document.querySelector("#sidebar-companion").dataset.character))
      .observe(document.querySelector("#sidebar-companion"), { attributes: true, attributeFilter: ["data-character"] });
    window.pet.endCompletion();
  });
  await expect(mascot(page)).toHaveAttribute("data-phase", "settling");
  await expect(mascot(page)).toHaveAttribute("data-caption", "leaving");
  await expect(mascot(page)).toHaveAttribute("data-character", "cow");
  await expect(mascot(page)).toHaveAttribute("data-phase", "steady");
  await expect(speech(page)).not.toBeVisible();
  expect(await button(page).boundingBox()).toEqual(figure);
  expect(await page.evaluate(() => window.returnCharacters.every(value => value === "cow"))).toBe(true);
  await page.screenshot({ path: info.outputPath("same-character-settled.png") });
});

test("different-character expiry performs goodbye then entrance and a new completion cancels the old return", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Focused transition regression.");
  await isolatedPet(page, "horse", "settle");
  await harnessCompletion(page);
  await page.evaluate(() => window.pet.endCompletion());
  await expect(mascot(page)).toHaveAttribute("data-phase", "goodbye");
  await expect(mascot(page)).toHaveAttribute("data-character", "cow");
  await expect(mascot(page)).toHaveAttribute("data-phase", "entering");
  await expect(mascot(page)).toHaveAttribute("data-character", "horse");
  // Interrupt after the incoming actor is visible, when old timers are most dangerous.
  await page.evaluate(() => window.pet.showCompletion({ character: "cow", message: { line: "新的合成回应", mode: "batch" }, count: 2 }));
  await expect(mascot(page)).toHaveAttribute("data-character", "cow");
  await expect(mascot(page)).toHaveAttribute("data-playing", "false", { timeout: 5000 });
  await page.waitForTimeout(1200);
  await expect(mascot(page)).toHaveAttribute("data-character", "cow");
  await expect(mascot(page)).toHaveAttribute("data-completion", "true");
  await expect(speech(page)).toHaveText("新的合成回应");
  await page.screenshot({ path: info.outputPath("interrupted-return-new-completion.png") });
});

test("reduced motion, hiding and account reset cancel the return without stale frames or captions", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Focused lifecycle cancellation regression.");
  await isolatedPet(page, "horse", "settle");
  await harnessCompletion(page);
  await page.evaluate(() => window.pet.endCompletion());
  await expect(mascot(page)).toHaveAttribute("data-phase", "goodbye");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(mascot(page)).toHaveAttribute("data-character", "horse");
  await expect(mascot(page)).toHaveAttribute("data-phase", "steady");
  await expect(mascot(page)).toHaveAttribute("data-playing", "false");
  await page.waitForTimeout(650);
  await expect(mascot(page)).toHaveAttribute("data-character", "horse");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await harnessCompletion(page);
  await page.evaluate(() => {
    window.pet.endCompletion();
    window.petAllowed = false;
    window.pet.reposition();
    window.pet.reset();
    window.pet.update({ key: "new-account", character: "cow", pose: "settle", message: "新账号的安静状态", ready: true });
    window.petAllowed = true;
    window.pet.reposition();
  });
  await page.waitForTimeout(1200);
  await expect(mascot(page)).toHaveAttribute("data-character", "cow");
  await expect(mascot(page)).toHaveAttribute("data-state", "new-account");
  await expect(mascot(page)).toHaveAttribute("data-phase", "steady");
  await expect(mascot(page)).toHaveAttribute("data-playing", "false");
  await expect(speech(page)).not.toBeVisible();
});

test("real seven-second completion expiry returns to the saved mood with an authored handoff", async ({ browser }, info) => {
  test.skip(info.project.name !== "desktop", "One desktop recording proves the real expiry; responsive tests cover placement.");
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 }, timezoneId: "Asia/Shanghai",
    baseURL: info.project.use.baseURL || "http://127.0.0.1:18763",
    recordVideo: { dir: info.outputPath("recording"), size: { width: 1440, height: 900 } },
  });
  const page = await context.newPage();
  try {
    const state = await workspace(page);
    await page.evaluate(() => {
      window.expiryPhases = [];
      new MutationObserver(() => window.expiryPhases.push(document.querySelector("#sidebar-companion").dataset.phase))
        .observe(document.querySelector("#sidebar-companion"), { attributes: true, attributeFilter: ["data-phase"] });
    });
    await page.locator('[data-task-complete="one"]').click();
    await page.mouse.move(900, 160);
    await expect(mascot(page)).toHaveAttribute("data-character", "cow");
    // Real wall-clock time: the recording must show the actual seven-second lifecycle.
    await page.waitForTimeout(7250);
    await expect(mascot(page)).toHaveAttribute("data-character", "horse");
    await expect(mascot(page)).toHaveAttribute("data-phase", "steady");
    await expect(mascot(page)).toHaveAttribute("data-completion", "false");
    const phases = await page.evaluate(() => window.expiryPhases);
    for (const phase of ["goodbye", "leaving", "entering"]) expect(phases).toContain(phase);
    await page.screenshot({ path: info.outputPath("actual-seven-second-return.png") });
    clean(state);
  } finally {
    await context.close();
    const path = await page.video()?.path();
    if (path) await info.attach("actual-seven-second-return", { path, contentType: "video/webm" });
  }
});

test("rapid completions merge while the other actor is still leaving without restarting the entrance", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Focused merge during entrance regression.");
  await isolatedPet(page, "horse", "settle");
  await page.evaluate(() => window.pet.showCompletion({ character: "cow", message: { line: "第一项的合成回应", mode: "ordinary" }, count: 1 }));
  await expect(mascot(page)).toHaveAttribute("data-phase", "goodbye");
  const count = await mascot(page).getAttribute("data-motion-count");
  await page.evaluate(() => window.pet.showCompletion({ character: "cow", message: { line: "连续两项的合成回应", mode: "batch" }, count: 2, replay: false }));
  await expect(mascot(page)).toHaveAttribute("data-motion-count", count);
  await expect(mascot(page)).toHaveAttribute("data-character", "cow");
  await expect(mascot(page)).toHaveAttribute("data-playing", "false", { timeout: 5000 });
  await expect(mascot(page)).toHaveAttribute("data-count", "2");
  await expect(speech(page)).toHaveText("连续两项的合成回应");
});

test("dragging during the handoff postpones but preserves the pending completion action", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Focused mouse interruption regression.");
  await isolatedPet(page, "horse", "settle");
  await page.evaluate(() => window.pet.showCompletion({ character: "cow", message: { line: "拖动后仍应出现的合成回应", mode: "ordinary" }, count: 1 }));
  await expect(mascot(page)).toHaveAttribute("data-phase", "goodbye");
  const b = await button(page).boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(1100, 600, { steps: 5 });
  await expect(mascot(page)).toHaveAttribute("data-dragging", "true");
  await page.mouse.up();
  await page.mouse.move(800, 100);
  await expect(mascot(page)).toHaveAttribute("data-action", "celebrate");
  await expect(mascot(page)).toHaveAttribute("data-character", "cow");
  await expect(mascot(page)).toHaveAttribute("data-playing", "false", { timeout: 5000 });
  await expect(speech(page)).toHaveText("拖动后仍应出现的合成回应");
});
