import { test, expect } from "@playwright/test";

async function openApp(page, { avatar = null, failSaveOnce = false, failSaveAt = failSaveOnce ? 1 : 0, holdSave = false, holdProfile = false } = {}) {
  let user = { id: "avatar-synthetic", name: "测试账号", email: "avatar-preview@example.test", emailVerified: true, avatar };
  const writes = [];
  let releaseSave, markSaveRequested, releaseProfile, markProfileRequested;
  const saveGate = new Promise(resolve => { releaseSave = resolve; });
  const saveRequested = new Promise(resolve => { markSaveRequested = resolve; });
  const profileGate = new Promise(resolve => { releaseProfile = resolve; });
  const profileRequested = new Promise(resolve => { markProfileRequested = resolve; });
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value, status = 200;
    if (path === "/api/auth/me") value = { user };
    else if (path === "/api/auth/avatar") {
      expect(request.method()).toBe("POST");
      const body = request.postDataJSON();
      writes.push(body);
      markSaveRequested();
      if (holdSave) await saveGate;
      if (writes.length === failSaveAt) {
        status = 503;
        value = { error: "Synthetic avatar service unavailable" };
      } else {
        user = { ...user, avatar: body.avatar };
        value = { user };
      }
    } else if (path === "/api/auth/profile") {
      const name = request.postDataJSON().name;
      value = { user: { ...user, name } };
      markProfileRequested();
      if (holdProfile) await profileGate;
      user = { ...user, name };
    } else if (path === "/api/workspace") value = { tasks: { version: 1, value: [] } };
    else if (path === "/api/version") value = { environment: "test", revision: "avatar-synthetic", managed: true };
    else if (path === "/api/usage/guest-visit") value = {};
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/recognition/batches") value = [];
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else if (path === "/api/sources/credentials") value = { configured: false };
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else throw new Error("Unmocked API: " + path);
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
  });
  await page.goto("/");
  await expect(page.locator(".loading-state")).toHaveCount(0);
  return { writes, saveRequested, releaseSave, profileRequested, releaseProfile };
}

async function openAccount(page) {
  if (await page.locator("#sidebar-show").isVisible() && !await page.locator(".account-entry").isVisible())
    await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "账号与数据", exact: true }).click();
  await expect(page.locator("#dialog .account-summary")).toBeVisible();
}

async function openAvatar(page) {
  await page.locator('#dialog [data-account-action="avatar"]').click();
  const form = page.locator('[data-account-form="avatar"]');
  await expect(form).toBeVisible();
  return form;
}

async function syntheticImage(page, { width = 900, height = 500, type = "image/png" } = {}) {
  const data = await page.evaluate(({ width, height, type }) => {
    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext("2d");
    const gradient = context.createLinearGradient(0, 0, width, height);
    gradient.addColorStop(0, "#e87364");
    gradient.addColorStop(0.5, "#91bd9b");
    gradient.addColorStop(1, "#6388cd");
    context.fillStyle = gradient;
    context.fillRect(0, 0, width, height);
    context.fillStyle = "#fff2d8";
    context.fillRect(width / 4, height / 4, width / 4, height / 4);
    return canvas.toDataURL(type);
  }, { width, height, type });
  return { name: "synthetic-avatar." + ({ "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" })[type], mimeType: type, buffer: Buffer.from(data.split(",")[1], "base64") };
}

async function chooseImage(page, form, image) {
  const chosen = page.waitForEvent("filechooser");
  await form.getByRole("button", { name: "选择图片", exact: true }).click();
  await (await chosen).setFiles(image);
  await expect(form.getByRole("button", { name: "保存头像", exact: true })).toBeEnabled();
  await expect(form.locator("canvas[data-avatar-preview]")).toBeVisible();
}

async function changeRange(locator, fraction) {
  const bounds = await locator.evaluate(input => ({ min: Number(input.min || 0), max: Number(input.max || 100), step: Number(input.step || 1) }));
  const value = bounds.min + Math.round((bounds.max - bounds.min) * fraction / bounds.step) * bounds.step;
  await locator.fill(String(Number(value.toFixed(5))));
}

test("avatar entry stays quiet until hover or keyboard focus and remains directly usable on touch", async ({ page }, info) => {
  await openApp(page);
  await openAccount(page);
  const avatar = page.locator("#dialog .account-avatar");
  const overlay = avatar.locator(".account-avatar-overlay");
  await expect(avatar).toHaveAttribute("aria-label", "更改头像");
  await expect(avatar).toContainText("测");
  await expect(avatar.locator("img, svg")).toHaveCount(0);
  await expect(overlay).toHaveText("更改头像");
  await page.mouse.move(0, 0);
  await page.locator('#dialog [data-account-action="close"]').focus();
  await expect(overlay).toHaveCSS("opacity", "0");
  const bounds = await avatar.boundingBox();
  expect(bounds.width).toBeGreaterThanOrEqual(44);
  expect(bounds.height).toBeGreaterThanOrEqual(44);
  expect(Math.abs(bounds.width - bounds.height)).toBeLessThan(1);
  await page.screenshot({ path: info.outputPath("account-avatar-default.png"), fullPage: true });
  if (info.project.name === "desktop") {
    await avatar.hover();
    await expect(overlay).toHaveCSS("opacity", "1");
    await page.screenshot({ path: info.outputPath("account-avatar-hover.png"), fullPage: true });
    await page.mouse.move(0, 0);
    await page.locator('#dialog [data-account-action="close"]').focus();
    await page.keyboard.press("Tab");
    await expect(avatar).toBeFocused();
    await expect(overlay).toHaveCSS("opacity", "1");
    await page.keyboard.press("Enter");
  } else await avatar.tap();
  await expect(page.locator('[data-account-form="avatar"]')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("selecting and adjusting an image saves a square PNG and updates the account and sidebar after refresh", async ({ page }, info) => {
  const f = await openApp(page);
  const image = await syntheticImage(page);
  await openAccount(page);
  const form = await openAvatar(page);
  await expect(form.getByRole("button", { name: "保存头像", exact: true })).toBeDisabled();
  await chooseImage(page, form, image);
  const preview = form.locator("canvas[data-avatar-preview]");
  const initial = await preview.evaluate(canvas => canvas.toDataURL("image/png"));
  await changeRange(form.getByLabel("缩放", { exact: true }), 0.65);
  await changeRange(form.getByLabel("水平位置", { exact: true }), 0.8);
  await changeRange(form.getByLabel("垂直位置", { exact: true }), 0.3);
  await expect.poll(() => preview.evaluate(canvas => canvas.toDataURL("image/png"))).not.toBe(initial);
  const adjusted = await preview.evaluate(canvas => ({ data: canvas.toDataURL("image/png"), width: canvas.width, height: canvas.height }));
  expect(adjusted.width).toBe(256);
  expect(adjusted.height).toBe(256);
  await page.screenshot({ path: info.outputPath("account-avatar-crop.png"), fullPage: true });
  await form.getByRole("button", { name: "保存头像", exact: true }).click();
  await expect(page.locator("#dialog .account-summary")).toBeVisible();
  expect(f.writes).toEqual([{ avatar: adjusted.data }]);
  await expect(page.locator("#dialog .account-avatar img")).toHaveAttribute("src", adjusted.data);
  await expect(page.locator(".account-entry .avatar img")).toHaveAttribute("src", adjusted.data);
  await page.screenshot({ path: info.outputPath("account-avatar-saved.png"), fullPage: true });
  await page.reload();
  await openAccount(page);
  await expect(page.locator("#dialog .account-avatar img")).toHaveAttribute("src", adjusted.data);
  await expect(page.locator(".account-entry .avatar img")).toHaveAttribute("src", adjusted.data);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("cancelling a selected image writes nothing and restoring the default persists the initial", async ({ page }) => {
  const existing = await syntheticImage(page, { width: 256, height: 256 });
  const avatar = "data:image/png;base64," + existing.buffer.toString("base64");
  const f = await openApp(page, { avatar });
  const next = await syntheticImage(page, { type: "image/jpeg" });
  await openAccount(page);
  let form = await openAvatar(page);
  await chooseImage(page, form, next);
  await form.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator("#dialog .account-summary")).toBeVisible();
  await expect(page.locator("#dialog .account-avatar img")).toHaveAttribute("src", avatar);
  expect(f.writes).toEqual([]);
  form = await openAvatar(page);
  await form.getByRole("button", { name: "恢复默认头像", exact: true }).click();
  await expect(page.locator("#dialog .account-summary")).toBeVisible();
  expect(f.writes).toEqual([{ avatar: null }]);
  await expect(page.locator("#dialog .account-avatar img, .account-entry .avatar img")).toHaveCount(0);
  await expect(page.locator("#dialog .account-avatar")).toContainText("测");
  await page.reload();
  await openAccount(page);
  await expect(page.locator("#dialog .account-avatar img, .account-entry .avatar img")).toHaveCount(0);
});

test("failed saves preserve the adjusted preview and can retry without choosing the file again", async ({ page }) => {
  const f = await openApp(page, { failSaveOnce: true });
  const image = await syntheticImage(page, { type: "image/webp" });
  await openAccount(page);
  const form = await openAvatar(page);
  await chooseImage(page, form, image);
  await changeRange(form.getByLabel("缩放", { exact: true }), 0.4);
  const preview = await form.locator("canvas[data-avatar-preview]").evaluate(canvas => canvas.toDataURL("image/png"));
  await form.getByRole("button", { name: "保存头像", exact: true }).click();
  await expect(form.locator("[data-account-error]")).toBeVisible();
  await expect(form.getByRole("button", { name: "保存头像", exact: true })).toBeEnabled();
  expect(await form.locator("canvas[data-avatar-preview]").evaluate(canvas => canvas.toDataURL("image/png"))).toBe(preview);
  await expect(page.locator(".account-entry .avatar img")).toHaveCount(0);
  await form.getByRole("button", { name: "保存头像", exact: true }).click();
  await expect(page.locator("#dialog .account-summary")).toBeVisible();
  expect(f.writes).toEqual([{ avatar: preview }, { avatar: preview }]);
  await expect(page.locator("#dialog .account-avatar img")).toHaveAttribute("src", preview);
});

test("unsupported, oversized and damaged images never enable a save or send an avatar request", async ({ page }) => {
  const f = await openApp(page);
  await openAccount(page);
  const form = await openAvatar(page);
  const input = form.locator("input[data-avatar-file]");
  const save = form.getByRole("button", { name: "保存头像", exact: true });
  for (const file of [
    { name: "unsupported.svg", mimeType: "image/svg+xml", buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"></svg>') },
    { name: "oversized.png", mimeType: "image/png", buffer: Buffer.alloc(5 * 1024 * 1024 + 1) },
    { name: "damaged.png", mimeType: "image/png", buffer: Buffer.from("This is not a PNG image.") },
  ]) {
    await input.setInputFiles(file);
    await expect(form.locator("[data-account-error]")).toBeVisible();
    await expect(save).toBeDisabled();
    expect(f.writes).toEqual([]);
  }
  await chooseImage(page, form, await syntheticImage(page));
  await expect(form.locator("[data-account-error]")).not.toBeVisible();
  await expect(save).toBeEnabled();
  expect(f.writes).toEqual([]);
});

test("a save that completes after Escape updates the avatar without closing or being overwritten by a later nickname form", async ({ page }) => {
  const f = await openApp(page, { holdSave: true, holdProfile: true });
  await openAccount(page);
  const form = await openAvatar(page);
  await chooseImage(page, form, await syntheticImage(page));
  await form.getByRole("button", { name: "保存头像", exact: true }).click();
  await f.saveRequested;
  await page.keyboard.press("Escape");
  await expect(page.locator("#dialog")).not.toBeVisible();
  try {
    await openAccount(page);
    await page.locator("#dialog").getByRole("button", { name: "修改昵称", exact: true }).click();
    const nickname = page.locator('[data-account-form="profile"]');
    await nickname.getByLabel("昵称", { exact: true }).fill("仍在编辑的昵称");
    await nickname.getByRole("button", { name: "保存昵称", exact: true }).click();
    await f.profileRequested;
    const response = page.waitForResponse("**/api/auth/avatar");
    f.releaseSave();
    await response;
    await expect(page.locator(".account-entry .avatar img")).toHaveAttribute("src", f.writes[0].avatar);
    await expect(nickname).toBeVisible();
    await expect(nickname.getByLabel("昵称", { exact: true })).toHaveValue("仍在编辑的昵称");
    const profileResponse = page.waitForResponse("**/api/auth/profile");
    f.releaseProfile();
    await profileResponse;
    await expect(nickname).not.toBeVisible();
    await expect(page.locator(".account-entry .avatar img")).toHaveAttribute("src", f.writes[0].avatar);
    await expect(page.locator(".account-name")).toContainText("仍在编辑的昵称");
  } finally { f.releaseSave(); f.releaseProfile(); }
});

test("closing during image decoding discards the selection and leaves a later form untouched", async ({ page }) => {
  const f = await openApp(page);
  const image = await syntheticImage(page);
  await page.evaluate(() => {
    const decode = Image.prototype.decode;
    Image.prototype.decode = async function () {
      await decode.call(this);
      window.avatarDecodePending = true;
      await new Promise(resolve => { window.releaseAvatarDecode = resolve; });
    };
  });
  await openAccount(page);
  const form = await openAvatar(page);
  await form.locator("[data-avatar-file]").setInputFiles(image);
  await expect.poll(() => page.evaluate(() => window.avatarDecodePending)).toBe(true);
  await page.keyboard.press("Escape");
  await openAccount(page);
  await page.locator("#dialog").getByRole("button", { name: "修改昵称", exact: true }).click();
  const nickname = page.locator('[data-account-form="profile"]');
  await nickname.getByLabel("昵称", { exact: true }).fill("保留我的输入");
  await page.evaluate(() => window.releaseAvatarDecode());
  await expect(nickname).toBeVisible();
  await expect(nickname.getByLabel("昵称", { exact: true })).toHaveValue("保留我的输入");
  expect(f.writes).toEqual([]);
});

test("a later failed avatar choice keeps an earlier successful save visible", async ({ page }) => {
  const f = await openApp(page, { holdSave: true, failSaveAt: 2 });
  await openAccount(page);
  let form = await openAvatar(page);
  await chooseImage(page, form, await syntheticImage(page));
  await form.getByRole("button", { name: "保存头像", exact: true }).click();
  await f.saveRequested;
  await page.keyboard.press("Escape");
  await openAccount(page);
  form = await openAvatar(page);
  await chooseImage(page, form, await syntheticImage(page, { width: 400, height: 900 }));
  await form.getByRole("button", { name: "保存头像", exact: true }).click();
  // The second upload waits for the first response before it is sent.
  expect(f.writes).toHaveLength(1);
  f.releaseSave();
  await expect(form.locator("[data-account-error]")).toBeVisible();
  expect(f.writes).toHaveLength(2);
  expect(f.writes[0].avatar).not.toBe(f.writes[1].avatar);
  await expect(page.locator(".account-entry .avatar img")).toHaveAttribute("src", f.writes[0].avatar);
  await form.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator("#dialog .account-avatar img")).toHaveAttribute("src", f.writes[0].avatar);
});
