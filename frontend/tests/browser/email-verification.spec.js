import { test, expect } from "@playwright/test";

const initialUser = { id: "synthetic", name: "合成账号", email: "synthetic@example.test" };

async function openApp(page, { anonymous = false, onRequest } = {}) {
  let user = anonymous ? null : { ...initialUser }, issued = 0;
  const calls = [];
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const body = request.postData() ? request.postDataJSON() : undefined;
    calls.push({ path, body });
    if (await onRequest?.({ path, body, route }) === true) return;
    let value = {}, status = 200;
    if (path === "/api/auth/me") {
      value = user ? { user } : { error: "请先登录" };
      if (!user) status = 401;
    } else if (path === "/api/auth/email-code") value = { challengeId: "challenge-" + ++issued, retryAfter: 60, expiresIn: 600 };
    else if (path === "/api/auth/register") { user = { ...initialUser, name: body.name, email: body.email, emailVerified: true }; value = { user }; }
    else if (path === "/api/auth/verify-email") { user = { ...user, emailVerified: true }; value = { user }; }
    else if (path === "/api/auth/logout") user = null;
    else if (path === "/api/workspace") value = { tasks: { version: 1, value: [{ id: "task", title: "合成任务", status: "not_started", due: null, plans: [], subtasks: [] }] } };
    else if (path === "/api/data/tasks") value = { version: 2 };
    else if (path === "/api/version") value = { environment: "test", revision: "6107891", managed: true };
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/usage/guest-visit") value = { saved: true };
    else if (path === "/api/recognition/batches") value = [];
    else if (path === "/api/sources/sync-runs") value = { contract_version: "source-sync/0.1", runs: [] };
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else if (path === "/api/sources/credentials") value = { configured: false };
    else if (path === "/api/error-reports") value = { contract_version: "error-report/0.1", saved: true, report_id: "abc123".repeat(4) };
    else throw new Error("Unmocked API: " + path);
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
  });
  await page.goto("/");
  return calls;
}
async function openAccount(page) {
  if (await page.locator("#sidebar-show").isVisible() && await page.locator("#sidebar-show").getAttribute("aria-expanded") !== "true") await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "账号与数据", exact: true }).click();
  return page.locator("#dialog");
}
async function registerForm(page) {
  const dialog = await openAccount(page);
  await dialog.getByRole("button", { name: "注册账号", exact: true }).click();
  await dialog.getByLabel("邮箱", { exact: true }).fill("synthetic@example.test");
  await dialog.getByLabel("昵称", { exact: true }).fill("合成昵称");
  await dialog.getByLabel("密码", { exact: true }).fill("Synthetic-2026!");
  await dialog.getByLabel("确认密码", { exact: true }).fill("Synthetic-2026!");
  return dialog;
}
async function screenshot(page, name) {
  await page.screenshot({ path: `../local-data/verification/${name}-${test.info().project.name}.png`, fullPage: true });
}

test("registration requires a sent code, preserves its draft and logs in after verification", async ({ page }) => {
  const calls = await openApp(page, { anonymous: true }), dialog = await registerForm(page);
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "注册并登录", exact: true }).click();
  await expect(dialog.getByText("请先发送邮箱验证码")).toBeVisible();
  expect(calls.filter(call => call.path === "/api/auth/register")).toHaveLength(0);
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByText(/验证码已发送/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: /秒后重发/ })).toBeDisabled();
  await expect(dialog.getByLabel("昵称", { exact: true })).toHaveValue("合成昵称");
  await expect(dialog.getByLabel("密码", { exact: true })).toHaveValue("Synthetic-2026!");
  await expect(dialog.getByLabel("确认密码", { exact: true })).toHaveValue("Synthetic-2026!");
  expect(calls.find(call => call.path === "/api/auth/email-code").body).toEqual({ purpose: "register", email: "synthetic@example.test" });
  await screenshot(page, "email-registration");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "注册并登录", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(calls.find(call => call.path === "/api/auth/register").body).toEqual({ name: "合成昵称", email: "synthetic@example.test", password: "Synthetic-2026!", challengeId: "challenge-1", code: "123456" });
  await expect(page.getByText("合成任务", { exact: true })).toBeVisible();
  await openAccount(page);
  await expect(dialog.getByText("邮箱已验证", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("region", { name: "教学网账号管理" }).getByRole("button", { name: "管理连接" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "验证邮箱", exact: true })).toHaveCount(0);
});

test("editing the email discards the old challenge without losing passwords or nickname", async ({ page }) => {
  const calls = await openApp(page, { anonymous: true }), dialog = await registerForm(page);
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByText(/验证码已发送/)).toBeVisible();
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByLabel("邮箱", { exact: true }).fill("another@example.test");
  await expect(dialog.getByLabel("邮箱验证码")).toHaveValue("");
  await expect(dialog.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
  await expect(dialog.getByLabel("昵称", { exact: true })).toHaveValue("合成昵称");
  await expect(dialog.getByLabel("密码", { exact: true })).toHaveValue("Synthetic-2026!");
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "注册并登录", exact: true }).click();
  await expect(dialog.getByText("请先发送邮箱验证码")).toBeVisible();
  expect(calls.filter(call => call.path === "/api/auth/register")).toHaveLength(0);
});

test("wrong registration codes remain retryable with the original challenge and draft", async ({ page }) => {
  let attempts = 0;
  const calls = await openApp(page, { anonymous: true, async onRequest({ path, route }) {
    if (path === "/api/auth/register" && ++attempts === 1) {
      await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ error: "PRIVATE", code: "email_code_invalid" }) }); return true;
    }
  } }), dialog = await registerForm(page);
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByText(/验证码已发送/)).toBeVisible();
  await dialog.getByLabel("邮箱验证码").fill("000000");
  await dialog.getByRole("button", { name: "注册并登录", exact: true }).click();
  await expect(dialog.getByText("验证码不正确或已失效，请核对或重新发送。")).toBeVisible();
  await expect(dialog).not.toContainText("PRIVATE");
  await expect(dialog.getByLabel("昵称", { exact: true })).toHaveValue("合成昵称");
  await expect(dialog.getByLabel("密码", { exact: true })).toHaveValue("Synthetic-2026!");
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "注册并登录", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  const requests = calls.filter(call => call.path === "/api/auth/register");
  expect(requests).toHaveLength(2);
  expect(requests.map(call => call.body.challengeId)).toEqual(["challenge-1", "challenge-1"]);
});

test("resend respects elapsed cooldown and the server rate limit", async ({ page }) => {
  await page.clock.install();
  let sent = 0;
  const calls = await openApp(page, { anonymous: true, async onRequest({ path, route }) {
    if (path === "/api/auth/email-code" && ++sent === 2) {
      await route.fulfill({ status: 429, contentType: "application/json", body: JSON.stringify({ error: "PRIVATE", code: "email_code_rate_limited", retryAfter: 120 }) }); return true;
    }
  } }), dialog = await registerForm(page);
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByRole("button", { name: /秒后重发/ })).toBeDisabled();
  await page.clock.fastForward(61000);
  await dialog.getByRole("button", { name: "重新发送验证码", exact: true }).click();
  await expect(dialog.getByText("验证码请求较多，请稍后再试。")).toBeVisible();
  await expect(dialog.getByRole("button", { name: /秒后重发/ })).toBeDisabled();
  await page.clock.fastForward(121000);
  await expect(dialog.getByRole("button", { name: "重新发送验证码", exact: true })).toBeEnabled();
  expect(calls.filter(call => call.path === "/api/auth/email-code")).toHaveLength(2);
});

test("unconfigured mail displays safe guidance and keeps the entered draft", async ({ page }) => {
  await openApp(page, { anonymous: true, async onRequest({ path, route }) {
    if (path === "/api/auth/email-code") {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "PRIVATE password=PRIVATE", code: "email_not_configured" }) }); return true;
    }
  } });
  const dialog = await registerForm(page);
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByText("邮件服务暂未启用，请稍后再试。")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "反馈此问题", exact: true })).toBeVisible();
  await expect(dialog).not.toContainText("PRIVATE");
  await expect(dialog.getByLabel("昵称", { exact: true })).toHaveValue("合成昵称");
  await expect(dialog.getByLabel("密码", { exact: true })).toHaveValue("Synthetic-2026!");
  await expect(dialog.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
});

test("legacy users verify without resetting tasks or pending writes", async ({ page }) => {
  let releaseSave, enterSave;
  const entered = new Promise(resolve => enterSave = resolve), waiting = new Promise(resolve => releaseSave = resolve);
  const calls = await openApp(page, { async onRequest({ path }) {
    if (path === "/api/data/tasks") { enterSave(); await waiting; }
  } });
  await page.getByRole("button", { name: "完成任务：合成任务", exact: true }).click();
  await entered;
  const initialReads = calls.filter(call => call.path === "/api/workspace").length, dialog = await openAccount(page);
  await expect(dialog.getByText(/邮箱未验证，现有功能可正常使用/)).toBeVisible();
  await dialog.getByRole("button", { name: "验证邮箱", exact: true }).click();
  await expect(dialog.getByText("验证当前账号邮箱：synthetic@example.test")).toBeVisible();
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await screenshot(page, "email-legacy-verification");
  expect(calls.find(call => call.path === "/api/auth/email-code").body).toEqual({ purpose: "verify_email" });
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "确认验证", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(calls.filter(call => call.path === "/api/workspace")).toHaveLength(initialReads);
  expect(calls.filter(call => call.path === "/api/auth/me")).toHaveLength(1);
  releaseSave();
  if (await page.locator("#sidebar-show").isVisible() && await page.locator("#sidebar-show").getAttribute("aria-expanded") !== "true") await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "已完成", exact: true }).click();
  await expect(page.getByText("合成任务", { exact: true })).toBeVisible();
  await openAccount(page);
  await expect(dialog.getByText("邮箱已验证", { exact: true })).toBeVisible();
});

test("closing during a send ignores its late result in a reopened form", async ({ page }) => {
  let release, enter;
  const entered = new Promise(resolve => enter = resolve), waiting = new Promise(resolve => release = resolve);
  await openApp(page, { anonymous: true, async onRequest({ path }) {
    if (path === "/api/auth/email-code") { enter(); await waiting; }
  } });
  const dialog = await registerForm(page);
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await entered;
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await openAccount(page);
  await dialog.getByRole("button", { name: "注册账号", exact: true }).click();
  release();
  await expect(dialog.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
  await expect(dialog.getByText(/验证码已发送/)).toHaveCount(0);
  await expect(dialog.getByLabel("邮箱验证码")).toHaveValue("");
});

test("closing during verification ignores the late result and permits reopening", async ({ page }) => {
  let release, enter;
  const entered = new Promise(resolve => enter = resolve), waiting = new Promise(resolve => release = resolve);
  await openApp(page, { async onRequest({ path }) {
    if (path === "/api/auth/verify-email") { enter(); await waiting; }
  } });
  const dialog = await openAccount(page);
  await dialog.getByRole("button", { name: "验证邮箱", exact: true }).click();
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByText(/验证码已发送/)).toBeVisible();
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "确认验证", exact: true }).click();
  await entered;
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  release();
  await expect(dialog.locator('[type="submit"]')).toBeEnabled();
  await openAccount(page);
  await expect(dialog.getByText(/邮箱未验证，现有功能可正常使用/)).toBeVisible();
  await expect(page.locator("#toast")).not.toContainText("邮箱已验证");
});

test("an old account send cannot populate registration after logout", async ({ page }) => {
  let release, enter;
  const entered = new Promise(resolve => enter = resolve), waiting = new Promise(resolve => release = resolve);
  await openApp(page, { async onRequest({ path }) {
    if (path === "/api/auth/email-code") { enter(); await waiting; }
  } });
  const dialog = await openAccount(page);
  await dialog.getByRole("button", { name: "验证邮箱", exact: true }).click();
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await entered;
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await openAccount(page);
  await dialog.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await registerForm(page);
  release();
  await expect(dialog.getByText(/验证码已发送/)).toHaveCount(0);
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "注册并登录", exact: true }).click();
  await expect(dialog.getByText("请先发送邮箱验证码")).toBeVisible();
});
