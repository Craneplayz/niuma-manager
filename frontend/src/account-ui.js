import {
  api,
  session,
  authenticate,
  logout,
  accountVersion,
} from "./team-store.js";
import { describeUserError, userErrorMarkup } from "./user-errors.js";

export function createAccountUI(host) {
  const esc = host.escapeHtml;
  const formIsOpen = (form) => form?.isConnected !== false && form?.closest?.("dialog")?.open !== false;
  const normalizedEmail = value => String(value || "").trim().toLowerCase();
  let mode = "login",
    busy = false,
    registering = false,
    error = "",
    email = "",
    operation = 0,
    verificationOperation = 0,
    challenge = null,
    retryAt = 0,
    sending = false,
    sendController = null,
    countdownTimer;
  function clearVerification() {
    verificationOperation++;
    sendController?.abort();
    sendController = null;
    clearTimeout(countdownTimer);
    sending = false;
    challenge = null;
    retryAt = 0;
  }
  function reset() {
    if (!registering) clearVerification();
    email = "";
    error = "";
    mode = session.user ? "summary" : "login";
    // authenticate/logout themselves emit account-reset while their request is active.
    if (!session.changing) {
      operation++;
      busy = false;
    }
  }
  globalThis.window?.addEventListener("niuma:account-reset", reset);
  function header(title) {
    return `<header class="dialog-header"><h2>${esc(title)}</h2><button class="icon-button" type="button" data-account-action="close" aria-label="关闭" ${busy ? "disabled" : ""}>${host.icon("close")}</button></header>`;
  }
  function errorMarkup() {
    return `<div class="error" data-account-error ${error ? "" : "hidden"}>${userErrorMarkup(error, "account")}</div>`;
  }
  function codeMarkup() {
    return `<div class="email-code-field"><label class="field">邮箱验证码<input name="code" type="text" required pattern="[0-9]{6}" minlength="6" maxlength="6" inputmode="numeric" autocomplete="one-time-code" placeholder="6 位数字"></label><button type="button" class="btn" data-account-action="send-code">发送验证码</button></div><p class="status-note email-code-status" data-email-code-status role="status" hidden></p>`;
  }
  function render() {
    if (mode === "summary" && session.user)
      return `${header("账号")}<div class="account-summary"><h3>${esc(session.user.name || "我的账号")}</h3><p class="status-note">${esc(session.user.email)}</p><p class="status-note">${session.user.emailVerified ? "邮箱已验证" : '邮箱未验证，现有功能可正常使用。<button type="button" class="btn quiet" data-account-action="verify-email">验证邮箱</button>'}</p></div><div class="account-actions"><button type="button" class="btn" data-account-action="profile" ${busy ? "disabled" : ""}>修改昵称</button><button type="button" class="btn" data-account-action="password" ${busy ? "disabled" : ""}>修改密码</button></div>${host.feedbackBox?.() || ""}${errorMarkup()}<div class="dialog-actions"><button type="button" class="btn quiet" data-account-action="logout" ${busy ? "disabled" : ""}>${busy ? "正在退出…" : "退出登录"}</button></div>`;
    if (mode === "verify-email")
      return `<form data-account-form="verify-email">${header("验证邮箱")}<p class="status-note">验证当前账号邮箱：${esc(session.user?.email)}</p>${codeMarkup()}${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">确认验证</button><button type="button" class="btn quiet" data-account-action="summary">返回账号</button></div></form>`;
    if (mode === "profile")
      return `<form data-account-form="profile">${header("修改昵称")}<label class="field">昵称<input name="name" required maxlength="40" autocomplete="nickname" value="${esc(session.user?.name || "")}"></label>${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">保存昵称</button></div></form>`;
    if (mode === "password")
      return `<form data-account-form="password">${header("修改密码")}<label class="field">当前密码<input name="currentPassword" type="password" required maxlength="128" autocomplete="current-password"></label><label class="field">新密码<input name="newPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="至少 8 位"></label><label class="field">确认新密码<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">更新密码</button></div></form>`;
    const register = mode === "register";
    return `<form data-account-form="${register ? "register" : "login"}">${header(register ? "注册账号" : "登录")}<label class="field">邮箱<input name="email" type="email" required maxlength="254" autocomplete="username" value="${esc(email)}"></label>${register ? '<label class="field">昵称<input name="displayName" maxlength="40" autocomplete="nickname" placeholder="可稍后修改"></label>' : ""}<label class="field">密码<input name="password" type="password" required minlength="8" maxlength="128" autocomplete="${register ? "new-password" : "current-password"}" placeholder="至少 8 位"></label>${register ? '<label class="field">确认密码<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>' + codeMarkup() : ""}${errorMarkup()}<div class="dialog-actions"><button class="btn primary" type="submit">${register ? "注册并登录" : "登录"}</button><button class="btn quiet" type="button" data-account-action="${register ? "login" : "register"}">${register ? "已有账号，去登录" : "注册账号"}</button></div></form>`;
  }
  function show(nextMode) {
    clearVerification();
    operation++;
    busy = false;
    mode = nextMode;
    error = "";
    host.openDialog(render());
  }
  function formError(form, message) {
    error = message;
    const node = form.querySelector("[data-account-error]");
    if (node) {
      node.textContent = message ? describeUserError(message, "account").message : "";
      node.innerHTML = userErrorMarkup(message, "account");
      node.hidden = !message;
    }
  }
  function lock(form, disabled) {
    form.querySelectorAll("input,button").forEach((field) => {
      if (field.dataset.errorFeedback) return;
      if (disabled) {
        field.dataset.wasDisabled = String(field.disabled);
        field.disabled = true;
      } else {
        field.disabled = field.dataset.wasDisabled === "true";
        delete field.dataset.wasDisabled;
      }
    });
  }
  function updateCodeControls(form) {
    clearTimeout(countdownTimer);
    if (!formIsOpen(form)) return;
    const button = form.querySelector('[data-account-action="send-code"]');
    if (!button) return;
    const remaining = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
    button.disabled = busy || sending || remaining > 0;
    const codeField = form.querySelector('[name="code"]');
    if (codeField) codeField.disabled = busy || sending;
    button.textContent = sending ? "正在发送…" : remaining ? `${remaining} 秒后重发` : challenge ? "重新发送验证码" : "发送验证码";
    const status = form.querySelector("[data-email-code-status]");
    if (status) {
      status.textContent = challenge ? "验证码已发送，10 分钟内有效，请查看收件箱或垃圾邮件。" : "";
      status.hidden = !challenge;
    }
    if (remaining > 0) {
      const token = verificationOperation;
      countdownTimer = setTimeout(() => {
        if (token === verificationOperation) updateCodeControls(form);
      }, 1000);
      countdownTimer.unref?.();
    }
  }
  async function sendCode(button) {
    const form = button.closest?.("[data-account-form]");
    const kind = form?.dataset.accountForm;
    if (!form || !["register", "verify-email"].includes(kind) || busy || sending || retryAt > Date.now()) return;
    const emailField = form.querySelector('[name="email"]');
    if (kind === "register" && !emailField?.checkValidity()) {
      emailField?.reportValidity();
      return;
    }
    const targetEmail = normalizedEmail(kind === "register" ? emailField.value : session.user?.email);
    const attempt = ++verificationOperation, epoch = accountVersion(), userId = session.user?.id;
    const current = () => attempt === verificationOperation && epoch === accountVersion() && userId === session.user?.id && formIsOpen(form);
    const controller = new AbortController();
    sendController = controller;
    sending = true;
    formError(form, "");
    updateCodeControls(form);
    try {
      const result = await api("/api/auth/email-code", kind === "register" ? { purpose: "register", email: targetEmail } : { purpose: "verify_email" }, "POST", { signal: controller.signal });
      if (!current()) return;
      challenge = { id: result.challengeId, email: targetEmail, expiresAt: Date.now() + (Number(result.expiresIn) || 600) * 1000 };
      retryAt = Date.now() + Math.min(86400, Math.max(1, Number(result.retryAfter) || 60)) * 1000;
      const codeField = form.querySelector('[name="code"]');
      if (codeField) {
        codeField.value = "";
      }
    } catch (e) {
      if (current()) {
        if (e.status === 429 && e.retryAfter > 0) retryAt = Date.now() + Math.min(86400, e.retryAfter) * 1000;
        formError(form, e);
      }
    } finally {
      if (attempt === verificationOperation) {
        sending = false;
        sendController = null;
        updateCodeControls(form);
        if (current() && challenge) form.querySelector('[name="code"]')?.focus();
      }
    }
  }
  async function signOut() {
    if (busy) return;
    clearVerification();
    const attempt = ++operation;
    busy = true;
    error = "";
    host.openDialog(render());
    try {
      const data = await logout();
      if (attempt !== operation) return;
      host.setState(data);
      host.closeDialog();
      host.render();
      host.toast("已退出登录");
    } catch (e) {
      if (attempt === operation) {
        error = e;
        busy = false;
        mode = session.user ? "summary" : "login";
        host.openDialog(render());
      }
    } finally {
      if (attempt === operation) busy = false;
    }
  }
  async function click(button) {
    const action = button.dataset.accountAction;
    if (!action) return false;
    if (busy) return true;
    if (action === "close") {
      clearVerification();
      operation++;
      error = "";
      host.closeDialog();
      return true;
    }
    if (action === "send-code") {
      await sendCode(button);
      return true;
    }
    if (action === "open") {
      show(session.user ? "summary" : "login");
      return true;
    }
    if (["login", "register", "profile", "password", "verify-email", "summary"].includes(action)) {
      if (["profile", "password", "verify-email", "summary"].includes(action) && !session.user) show("login");
      else show(action);
      return true;
    }
    if (action === "logout") {
      await signOut();
      return true;
    }
    return false;
  }
  async function submit(form) {
    const kind = form.dataset.accountForm;
    if (!kind) return false;
    if (busy || sending) return true;
    const values = Object.fromEntries(new FormData(form));
    if (kind === "register" && values.password !== values.confirmPassword) {
      formError(form, "两次输入的密码不一致");
      return true;
    }
    if (["register", "verify-email"].includes(kind)) {
      const targetEmail = normalizedEmail(kind === "register" ? values.email : session.user?.email);
      if (!challenge || challenge.email !== targetEmail) {
        formError(form, "请先发送邮箱验证码");
        return true;
      }
      if (challenge.expiresAt <= Date.now()) {
        formError(form, { code: "email_code_expired" });
        return true;
      }
      if (!/^[0-9]{6}$/.test(values.code || "")) {
        formError(form, "请输入 6 位邮箱验证码");
        return true;
      }
      values.challengeId = challenge.id;
    }
    if (kind === "password") {
      if (values.newPassword !== values.confirmPassword) {
        formError(form, "两次输入的新密码不一致");
        return true;
      }
      if (values.currentPassword === values.newPassword) {
        formError(form, "新密码不能与当前密码相同");
        return true;
      }
    }
    if (kind === "profile" && !String(values.name || "").trim()) {
      formError(form, "请填写昵称");
      return true;
    }
    const attempt = ++operation,
      epoch = accountVersion(),
      accountChange = ["login", "register"].includes(kind);
    const current = () => attempt === operation && (accountChange || epoch === accountVersion()) && (kind !== "verify-email" || formIsOpen(form));
    busy = true;
    formError(form, "");
    lock(form, true);
    const submitButton = form.querySelector('[type="submit"]'), label = submitButton?.textContent;
    if (submitButton) submitButton.textContent = accountChange ? "正在登录…" : kind === "verify-email" ? "正在验证…" : "正在保存…";
    try {
      if (accountChange) {
        email = String(values.email || "").trim();
        registering = kind === "register";
        const data = await authenticate(kind, { ...values, email });
        if (!current()) return true;
        clearVerification();
        host.setState(data);
        host.closeDialog();
        host.render();
        host.toast("已登录");
        email = "";
      } else if (kind === "verify-email") {
        const result = await api("/api/auth/verify-email", { challengeId: values.challengeId, code: values.code });
        if (!current() || result.user?.id !== session.user?.id) return true;
        session.user = result.user;
        clearVerification();
        host.closeDialog();
        host.render();
        host.toast("邮箱已验证");
      } else if (kind === "profile") {
        const result = await api("/api/auth/profile", { name: String(values.name).trim() });
        if (!current()) return true;
        session.user = result.user;
        if (formIsOpen(form)) host.closeDialog();
        host.render();
        host.toast("昵称已更新");
      } else if (kind === "password") {
        await api("/api/auth/password", { currentPassword: values.currentPassword, newPassword: values.newPassword });
        if (!current()) return true;
        if (formIsOpen(form)) host.closeDialog();
        host.toast("密码已更新");
      }
    } catch (e) {
      if (current()) formError(form, e);
    } finally {
      registering = false;
      if (attempt === operation) {
        busy = false;
        lock(form, false);
        if (submitButton) submitButton.textContent = label;
        if (["register", "verify-email"].includes(kind)) updateCodeControls(form);
      }
    }
    return true;
  }
  function input(event) {
    if (event.target.name === "email" && event.target.closest?.("[data-account-form]")) {
      email = event.target.value;
      const form = event.target.closest("[data-account-form]");
      if (form.dataset.accountForm === "register") {
        clearVerification();
        const code = form.querySelector('[name="code"]');
        if (code) code.value = "";
        formError(form, "");
        updateCodeControls(form);
      }
      return true;
    }
    return false;
  }
  return { click, submit, input };
}
