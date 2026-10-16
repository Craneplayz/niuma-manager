import test from "node:test";
import assert from "node:assert/strict";

globalThis.window = new EventTarget();
globalThis.CustomEvent ||= class extends Event {};
const { createAccountUI } = await import("../src/account-ui.js");
const { session, accountVersion } = await import("../src/team-store.js");

const reply = (value, status = 200) => ({ ok: status < 400, status, json: async () => value });
const challenge = { challengeId: "reset-challenge", retryAfter: 60, expiresIn: 600 };
function fixture(t, responder = () => reply(challenge)) {
  session.user = null;
  session.changing = false;
  const calls = [], dialogs = [], toasts = [];
  t.mock.method(globalThis, "fetch", async (path, options) => {
    const call = { path, body: JSON.parse(options.body) };
    calls.push(call);
    return responder(call);
  });
  const originalFormData = globalThis.FormData;
  globalThis.FormData = class {
    constructor(form) { this.values = Object.entries(form.fields).filter(([, node]) => !node.disabled).map(([name, node]) => [name, node.value]); }
    [Symbol.iterator]() { return this.values[Symbol.iterator](); }
  };
  t.after(() => { globalThis.FormData = originalFormData; });
  const ui = createAccountUI({
    escapeHtml: value => String(value || ""), icon: () => "", openDialog: html => dialogs.push(html),
    closeDialog: () => assert.fail("recovery must stay in the account dialog"),
    setState: () => assert.fail("recovery must not replace the workspace"), render: () => assert.fail("recovery must not redraw the workspace"),
    toast: message => toasts.push(message),
  });
  const dialog = { open: true }, fields = Object.fromEntries(Object.entries({
    email: " Person@Example.test ", code: "123456", newPassword: "Updated-2026!", confirmPassword: "Updated-2026!",
  }).map(([name, value]) => [name, { name, value, dataset: {}, disabled: false, checkValidity: () => true, focus() {} }]));
  const error = { hidden: true }, status = {}, send = { dataset: { accountAction: "send-code" }, disabled: false }, submit = { dataset: {}, textContent: "重置密码", disabled: false };
  const form = {
    dataset: { accountForm: "reset-password" }, fields, isConnected: true,
    closest: () => dialog,
    querySelector(selector) {
      const name = selector.match(/^\[name="(.+)"\]$/)?.[1];
      return name ? fields[name] : ({ "[data-account-error]": error, "[data-email-code-status]": status, '[data-account-action="send-code"]': send, '[type="submit"]': submit })[selector];
    },
    querySelectorAll: () => [...Object.values(fields), send, submit],
  };
  send.closest = () => form;
  for (const node of Object.values(fields)) node.closest = () => form;
  const open = () => ui.click({ dataset: { accountAction: "reset-password" } });
  const sendCode = () => ui.click(send);
  const close = () => ui.click({ dataset: { accountAction: "close" } });
  // Remove each instance's timers and invalidate pending operations at test completion.
  t.after(() => { session.user = null; window.dispatchEvent(new Event("niuma:account-reset")); });
  return { ui, calls, dialogs, toasts, form, fields, error, status, send, dialog, open, sendCode, close };
}

test("recovery sends a purpose-bound challenge and returns to login without authenticating", async t => {
  const f = fixture(t, call => reply(call.path.endsWith("email-code") ? challenge : { reset: true })), epoch = accountVersion();
  await f.open();
  assert.match(f.dialogs.at(-1), /找回密码/);
  await f.sendCode();
  assert.deepEqual(f.calls[0], { path: "/api/auth/email-code", body: { purpose: "reset_password", email: "person@example.test" } });
  assert.match(f.status.textContent, /如果该邮箱有可找回的账号，验证码会发送到收件箱/);
  f.fields.code.value = "123456";
  await f.ui.submit(f.form);
  assert.deepEqual(f.calls[1], { path: "/api/auth/reset-password", body: { email: "person@example.test", challengeId: "reset-challenge", code: "123456", newPassword: "Updated-2026!" } });
  assert.match(f.dialogs.at(-1), /data-account-form="login"/);
  assert.doesNotMatch(f.dialogs.at(-1), /Updated-2026!/);
  assert.deepEqual(f.toasts, ["密码已重置，请使用新密码登录"]);
  assert.equal(session.user, null);
  assert.equal(accountVersion(), epoch);
});

test("recovery requires matching new passwords and its own issued challenge", async t => {
  const f = fixture(t);
  await f.open();
  f.fields.confirmPassword.value = "Different-2026!";
  await f.ui.submit(f.form);
  assert.match(f.error.innerHTML, /两次输入的新密码不一致/);
  f.fields.confirmPassword.value = f.fields.newPassword.value;
  await f.ui.submit(f.form);
  assert.match(f.error.innerHTML, /请先发送邮箱验证码/);
  assert.equal(f.calls.length, 0);
  f.form.dataset.accountForm = "register";
  await f.sendCode();
  f.form.dataset.accountForm = "reset-password";
  await f.ui.submit(f.form);
  assert.match(f.error.innerHTML, /请先发送邮箱验证码/);
  assert.equal(f.calls.length, 1);
});

test("email edits invalidate recovery codes while retaining the password draft", async t => {
  const f = fixture(t);
  await f.open();
  await f.sendCode();
  f.fields.email.value = "another@example.test";
  f.ui.input({ target: f.fields.email });
  assert.equal(f.fields.code.value, "");
  assert.equal(f.fields.newPassword.value, "Updated-2026!");
  assert.equal(f.send.disabled, false);
  await f.ui.submit(f.form);
  assert.match(f.error.innerHTML, /请先发送邮箱验证码/);
  assert.equal(f.calls.length, 1);
});

test("expired recovery challenges and nonnumeric codes never reach the reset endpoint", async t => {
  const f = fixture(t), now = Date.now();
  t.mock.method(Date, "now", () => now);
  await f.open();
  await f.sendCode();
  f.fields.code.value = "12x456";
  await f.ui.submit(f.form);
  assert.match(f.error.innerHTML, /请输入 6 位邮箱验证码/);
  f.fields.code.value = "123456";
  Date.now.mock.mockImplementation(() => now + 601000);
  await f.ui.submit(f.form);
  assert.match(f.error.innerHTML, /验证码已过期/);
  assert.equal(f.calls.length, 1);
});

test("failed resets preserve challenge and draft and hide raw server messages", async t => {
  const f = fixture(t, call => call.path.endsWith("email-code") ? reply(challenge) : reply({ code: "email_code_invalid", error: "PRIVATE password=PRIVATE" }, 400));
  await f.open();
  await f.sendCode();
  f.fields.code.value = "000000";
  await f.ui.submit(f.form);
  assert.match(f.error.innerHTML, /验证码不正确或已失效/);
  assert.doesNotMatch(f.error.innerHTML, /PRIVATE/);
  assert.equal(f.fields.newPassword.value, "Updated-2026!");
  assert.equal(f.fields.code.value, "000000");
  assert.equal(f.fields.newPassword.disabled, false);
  f.fields.code.value = "123456";
  await f.ui.submit(f.form);
  assert.deepEqual(f.calls.slice(1).map(call => call.body.challengeId), ["reset-challenge", "reset-challenge"]);
});

test("closing the dialog during a reset suppresses a late success", async t => {
  let release;
  const f = fixture(t, call => call.path.endsWith("email-code") ? reply(challenge) : new Promise(resolve => release = resolve));
  await f.open(); await f.sendCode(); f.fields.code.value = "123456";
  const pending = f.ui.submit(f.form);
  f.dialog.open = false;
  release(reply({ reset: true }));
  await pending;
  assert.equal(f.dialogs.length, 1);
  assert.deepEqual(f.toasts, []);
  assert.equal(f.fields.newPassword.disabled, false);
});

test("account resets invalidate an in-flight recovery response", async t => {
  let release;
  const f = fixture(t, call => call.path.endsWith("email-code") ? reply(challenge) : new Promise(resolve => release = resolve));
  await f.open(); await f.sendCode(); f.fields.code.value = "123456";
  const pending = f.ui.submit(f.form);
  window.dispatchEvent(new Event("niuma:account-reset"));
  release(reply({ reset: true }));
  await pending;
  assert.deepEqual(f.toasts, []);
  assert.equal(f.dialogs.length, 1);
});

test("reopening after Escape starts an independent form while the old reset is pending", async t => {
  let release;
  const f = fixture(t, call => call.path.endsWith("email-code") ? reply(challenge) : new Promise(resolve => release = resolve));
  await f.open(); await f.sendCode(); f.fields.code.value = "123456";
  const pending = f.ui.submit(f.form);
  f.dialog.open = false;
  await f.ui.click({ dataset: { accountAction: "open" } });
  assert.match(f.dialogs.at(-1), /data-account-form="login"/);
  release(reply({ reset: true }));
  await pending;
  assert.equal(f.dialogs.length, 2);
  assert.deepEqual(f.toasts, []);
});

test("a logged-in account cannot enter or submit anonymous password recovery", async t => {
  const f = fixture(t), user = { id: "existing", name: "已有账号", email: "owner@example.test" };
  session.user = user;
  const epoch = accountVersion();
  await f.open(); await f.sendCode(); await f.ui.submit(f.form);
  assert.match(f.dialogs.at(-1), /已有账号/);
  assert.doesNotMatch(f.dialogs.at(-1), /data-account-form="reset-password"/);
  assert.equal(f.calls.length, 0);
  assert.equal(session.user, user);
  assert.equal(accountVersion(), epoch);
});
