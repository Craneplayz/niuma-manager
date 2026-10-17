import { test, expect } from "@playwright/test";

const initialUser = { id: "recovery-user", name: "合成账号", email: "synthetic@example.test", emailVerified: true };
const safeSendMessage = "如果该邮箱有可找回的账号，验证码会发送到收件箱";
async function openApp(page, { loggedIn = false, onRequest } = {}) {
  let user = loggedIn ? { ...initialUser } : null, issued = 0;
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
    else if (path === "/api/auth/reset-password") value = { reset: true };
    else if (path === "/api/auth/login") { user = { ...initialUser, email: body.email }; value = { user }; }
    else if (path === "/api/workspace") value = { tasks: { version: 1, value: [{ id: "task", title: "合成任务", status: "not_started", due: null, plans: [], subtasks: [] }] } };
    else if (path === "/api/data/tasks") value = { version: 2 };
    else if (path === "/api/version") value = { environment: "test", revision: "4bb33a1", managed: true };
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/recognition/batches") value = [];
    else if (path === "/api/sources/sync-runs") value = { contract_version: "source-sync/0.1", runs: [] };
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else if (path === "/api/sources/credentials") value = { configured: false };
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
async function recoveryForm(page) {
  const dialog = await openAccount(page);
  await dialog.getByRole("button", { name: "忘记密码", exact: true }).click();
  await dialog.getByLabel("邮箱", { exact: true }).fill("synthetic@example.test");
  await dialog.getByLabel("新密码", { exact: true }).fill("Updated-2026!");
  await dialog.getByLabel("确认新密码", { exact: true }).fill("Updated-2026!");
  return dialog;
}
async function sendCode(dialog) {
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByText(safeSendMessage, { exact: false })).toBeVisible();
}

test("password recovery verifies a code, returns to login and requires the new password", async ({ page }) => {
  const calls = await openApp(page), dialog = await recoveryForm(page);
  await sendCode(dialog);
  await expect(dialog.getByRole("button", { name: /秒后重发/ })).toBeDisabled();
  await expect(dialog.getByLabel("新密码", { exact: true })).toHaveValue("Updated-2026!");
  expect(calls.find(call => call.path === "/api/auth/email-code").body).toEqual({ purpose: "reset_password", email: "synthetic@example.test" });
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await page.screenshot({ path: `../local-data/verification/password-recovery-${test.info().project.name}.png`, fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await dialog.getByRole("button", { name: "重置密码", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "登录", exact: true })).toBeVisible();
  await expect(page.locator("#toast")).toContainText("密码已重置，请使用新密码登录");
  await expect(dialog.getByLabel("邮箱", { exact: true })).toHaveValue("synthetic@example.test");
  await expect(dialog.getByLabel("密码", { exact: true })).toHaveValue("");
  expect(calls.find(call => call.path === "/api/auth/reset-password").body).toEqual({ email: "synthetic@example.test", challengeId: "challenge-1", code: "123456", newPassword: "Updated-2026!" });
  expect(calls.filter(call => call.path === "/api/auth/login")).toHaveLength(0);
  expect(calls.filter(call => call.path === "/api/workspace")).toHaveLength(0);
  await dialog.getByLabel("密码", { exact: true }).fill("Updated-2026!");
  await dialog.getByRole("button", { name: "登录", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByText("合成任务", { exact: true })).toBeVisible();
  expect(calls.find(call => call.path === "/api/auth/login").body.password).toEqual("Updated-2026!");
});

test("recovery validates matching passwords and requires a sent code", async ({ page }) => {
  const calls = await openApp(page), dialog = await recoveryForm(page);
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByLabel("确认新密码", { exact: true }).fill("Different-2026!");
  await dialog.getByRole("button", { name: "重置密码", exact: true }).click();
  await expect(dialog.getByText("两次输入的新密码不一致")).toBeVisible();
  await dialog.getByLabel("确认新密码", { exact: true }).fill("Updated-2026!");
  await dialog.getByRole("button", { name: "重置密码", exact: true }).click();
  await expect(dialog.getByText("请先发送邮箱验证码")).toBeVisible();
  expect(calls.filter(call => call.path === "/api/auth/reset-password")).toHaveLength(0);
});

test("changing recovery email discards its challenge and preserves both password fields", async ({ page }) => {
  const calls = await openApp(page), dialog = await recoveryForm(page);
  await sendCode(dialog);
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByLabel("邮箱", { exact: true }).fill("another@example.test");
  await expect(dialog.getByLabel("邮箱验证码")).toHaveValue("");
  await expect(dialog.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
  await expect(dialog.getByLabel("新密码", { exact: true })).toHaveValue("Updated-2026!");
  await expect(dialog.getByLabel("确认新密码", { exact: true })).toHaveValue("Updated-2026!");
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "重置密码", exact: true }).click();
  await expect(dialog.getByText("请先发送邮箱验证码")).toBeVisible();
  expect(calls.filter(call => call.path === "/api/auth/reset-password")).toHaveLength(0);
});

test("incorrect recovery codes stay retryable without leaking server details", async ({ page }) => {
  let resets = 0;
  const calls = await openApp(page, { async onRequest({ path, route }) {
    if (path === "/api/auth/reset-password" && ++resets === 1) {
      await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ code: "email_code_invalid", error: "PRIVATE password=PRIVATE" }) }); return true;
    }
  } }), dialog = await recoveryForm(page);
  await sendCode(dialog);
  await dialog.getByLabel("邮箱验证码").fill("000000");
  await dialog.getByRole("button", { name: "重置密码", exact: true }).click();
  await expect(dialog.getByText("验证码不正确或已失效，请核对或重新发送。")).toBeVisible();
  await expect(dialog).not.toContainText("PRIVATE");
  await expect(dialog.getByLabel("邮箱验证码")).toHaveValue("000000");
  await expect(dialog.getByLabel("新密码", { exact: true })).toHaveValue("Updated-2026!");
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "重置密码", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "登录", exact: true })).toBeVisible();
  expect(calls.filter(call => call.path === "/api/auth/reset-password").map(call => call.body.challengeId)).toEqual(["challenge-1", "challenge-1"]);
});

test("registration challenges cannot carry over to password recovery", async ({ page }) => {
  const calls = await openApp(page), dialog = await openAccount(page);
  await dialog.getByRole("button", { name: "注册账号", exact: true }).click();
  await dialog.getByLabel("邮箱", { exact: true }).fill("synthetic@example.test");
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByText(/验证码已发送/)).toBeVisible();
  await dialog.getByRole("button", { name: "已有账号，去登录", exact: true }).click();
  await dialog.getByRole("button", { name: "忘记密码", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
  await dialog.getByLabel("新密码", { exact: true }).fill("Updated-2026!");
  await dialog.getByLabel("确认新密码", { exact: true }).fill("Updated-2026!");
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "重置密码", exact: true }).click();
  await expect(dialog.getByText("请先发送邮箱验证码")).toBeVisible();
  expect(calls.filter(call => call.path === "/api/auth/reset-password")).toHaveLength(0);
});

test("cooldown, server rate limits and expiry apply to password recovery", async ({ page }) => {
  await page.clock.install();
  let sends = 0;
  const calls = await openApp(page, { async onRequest({ path, route }) {
    if (path === "/api/auth/email-code" && ++sends === 2) {
      await route.fulfill({ status: 429, contentType: "application/json", body: JSON.stringify({ code: "email_code_rate_limited", error: "PRIVATE", retryAfter: 120 }) }); return true;
    }
  } }), dialog = await recoveryForm(page);
  await sendCode(dialog);
  await page.clock.fastForward(61000);
  await dialog.getByRole("button", { name: "重新发送验证码", exact: true }).click();
  await expect(dialog.getByText("验证码请求较多，请稍后再试。")).toBeVisible();
  await expect(dialog.getByRole("button", { name: /秒后重发/ })).toBeDisabled();
  await page.clock.fastForward(121000);
  await expect(dialog.getByRole("button", { name: "重新发送验证码", exact: true })).toBeEnabled();
  await page.clock.fastForward(420000);
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "重置密码", exact: true }).click();
  await expect(dialog.getByText("验证码已过期，请重新发送。")).toBeVisible();
  expect(calls.filter(call => call.path === "/api/auth/reset-password")).toHaveLength(0);
});

test("unconfigured mail keeps the recovery draft and displays safe service guidance", async ({ page }) => {
  await openApp(page, { async onRequest({ path, route }) {
    if (path === "/api/auth/email-code") {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ code: "email_not_configured", error: "PRIVATE password=PRIVATE" }) }); return true;
    }
  } });
  const dialog = await recoveryForm(page);
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByText("邮件服务暂未启用，请稍后再试。")).toBeVisible();
  await expect(dialog).not.toContainText("PRIVATE");
  await expect(dialog.getByLabel("新密码", { exact: true })).toHaveValue("Updated-2026!");
  await expect(dialog.getByLabel("确认新密码", { exact: true })).toHaveValue("Updated-2026!");
  await expect(dialog.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
});

test("a late code send cannot populate a reopened recovery form", async ({ page }) => {
  let enter, release;
  const entered = new Promise(resolve => enter = resolve), waiting = new Promise(resolve => release = resolve);
  await openApp(page, { async onRequest({ path }) { if (path === "/api/auth/email-code") { enter(); await waiting; } } });
  const dialog = await recoveryForm(page);
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await entered;
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await openAccount(page);
  await dialog.getByRole("button", { name: "忘记密码", exact: true }).click();
  release();
  await expect(dialog.getByRole("button", { name: "发送验证码", exact: true })).toBeEnabled();
  await expect(dialog.getByText(safeSendMessage, { exact: false })).toHaveCount(0);
  await expect(dialog.getByLabel("邮箱验证码")).toHaveValue("");
});

test("a late reset success cannot change a reopened login form", async ({ page }) => {
  let enter, release;
  const entered = new Promise(resolve => enter = resolve), waiting = new Promise(resolve => release = resolve);
  await openApp(page, { async onRequest({ path }) { if (path === "/api/auth/reset-password") { enter(); await waiting; } } });
  const dialog = await recoveryForm(page);
  await sendCode(dialog);
  await dialog.getByLabel("邮箱验证码").fill("123456");
  await dialog.getByRole("button", { name: "重置密码", exact: true }).click();
  await entered;
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await openAccount(page);
  await dialog.getByLabel("邮箱", { exact: true }).fill("another@example.test");
  const response = page.waitForResponse("**/api/auth/reset-password");
  release(); await response;
  await expect(dialog.getByRole("heading", { name: "登录", exact: true })).toBeVisible();
  await expect(dialog.getByLabel("邮箱", { exact: true })).toHaveValue("another@example.test");
  await expect(page.locator("#toast")).not.toContainText("密码已重置");
});

test("logged-in accounts retain workspace saves and see the existing password-change action", async ({ page }) => {
  let enter, release;
  const entered = new Promise(resolve => enter = resolve), waiting = new Promise(resolve => release = resolve);
  const calls = await openApp(page, { loggedIn: true, async onRequest({ path }) { if (path === "/api/data/tasks") { enter(); await waiting; } } });
  await page.getByRole("button", { name: "完成任务：合成任务", exact: true }).click();
  await entered;
  const reads = calls.filter(call => call.path === "/api/workspace").length, dialog = await openAccount(page);
  await expect(dialog.getByRole("button", { name: "忘记密码", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "修改密码", exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  release();
  if (await page.locator("#sidebar-show").isVisible() && await page.locator("#sidebar-show").getAttribute("aria-expanded") !== "true") await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "已完成", exact: true }).click();
  await expect(page.getByText("合成任务", { exact: true })).toBeVisible();
  expect(calls.filter(call => call.path === "/api/workspace")).toHaveLength(reads);
  expect(calls.filter(call => call.path === "/api/auth/reset-password")).toHaveLength(0);
});
