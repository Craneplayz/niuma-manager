import {
  api,
  session,
  authenticate,
  logout,
  accountVersion,
} from "./team-store.js";
import { describeUserError, userErrorMarkup } from "./user-errors.js";
import { avatarContent, readAvatarFile, drawAvatar } from "./account-avatar.js";

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
  let avatarImage = null, selection = 0, selecting = false,
    avatarSaves = Promise.resolve();
  function clearAvatarDraft() {
    selection++;
    avatarImage = null;
    selecting = false;
  }
  const accountPanelOpen = () =>
    !host.isDialogOpen || host.isDialogOpen("[data-account-settings-panel]");
  const focusEmailSection = () => host.focusAccountSection?.("pku-email");
  function reset() {
    clearAvatarDraft();
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
  function teachingAccount() {
    return `<section class="account-connection" aria-label="教学网账号管理"><div><h3>教学网账号</h3><p class="status-note">管理用于获取课程作业与通知的账号</p></div><button type="button" class="btn quiet" data-import-action="credentials" ${busy ? "disabled" : ""}>管理连接</button></section>`;
  }
  function render() {
    if (mode === "summary" && session.user)
      return `<div data-account-settings-panel>${header("账号")}<div class="account-summary"><button type="button" class="avatar account-avatar" data-account-action="avatar" aria-label="更改头像" ${busy ? "disabled" : ""}>${avatarContent(session.user, esc)}<span class="account-avatar-overlay" aria-hidden="true">更改头像</span></button><div class="account-summary-copy"><h3>${esc(session.user.name || "我的账号")}</h3><p class="status-note">${esc(session.user.email)}</p><p class="status-note">${session.user.emailVerified ? "邮箱已验证" : '邮箱未验证，现有功能可正常使用。<button type="button" class="btn quiet" data-account-action="verify-email">验证邮箱</button>'}</p></div></div><div class="account-actions"><button type="button" class="btn" data-account-action="profile" ${busy ? "disabled" : ""}>修改昵称</button><button type="button" class="btn" data-account-action="password" ${busy ? "disabled" : ""}>修改密码</button></div>${teachingAccount()}${host.renderEmailSettings?.() || ""}${host.accountTools?.render(busy) || ""}${host.feedbackBox?.() || ""}${errorMarkup()}<div class="dialog-actions"><button type="button" class="btn quiet" data-account-action="logout" ${busy ? "disabled" : ""}>${busy ? "正在退出…" : "退出登录"}</button></div></div>`;
    if (mode === "avatar")
      return `<form data-account-form="avatar">${header("更改头像")}<div class="avatar-editor"><div class="avatar-editor-preview"><div class="avatar avatar-editor-current">${avatarContent(session.user, esc)}</div><canvas data-avatar-preview width="256" height="256" role="img" aria-label="头像裁切预览" hidden></canvas></div><button type="button" class="btn" data-account-action="avatar-choose">选择图片</button><input type="file" data-avatar-file accept="image/png,image/jpeg,image/webp" aria-label="选择头像图片" hidden><p class="status-note">支持 JPG、PNG、WebP，最大 5 MB</p><p class="status-note" data-avatar-selection-status role="status" hidden></p></div><div class="avatar-crop-controls" data-avatar-controls hidden><label>缩放<input type="range" name="zoom" min="1" max="3" step="0.01" value="1"></label><label>水平位置<input type="range" name="x" min="-100" max="100" value="0"></label><label>垂直位置<input type="range" name="y" min="-100" max="100" value="0"></label></div>${errorMarkup()}<div class="avatar-editor-actions">${session.user?.avatar ? '<button type="button" class="btn quiet" data-account-action="avatar-reset">恢复默认头像</button>' : ""}<div class="dialog-actions"><button type="button" class="btn quiet" data-account-action="avatar-cancel">取消</button><button type="submit" class="btn primary" disabled>保存头像</button></div></div></form>`;
    if (mode === "verify-email")
      return `<form data-account-form="verify-email">${header("验证邮箱")}<p class="status-note">验证当前账号邮箱：${esc(session.user?.email)}</p>${codeMarkup()}${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">确认验证</button><button type="button" class="btn quiet" data-account-action="summary">返回账号</button></div></form>`;
    if (mode === "profile")
      return `<form data-account-form="profile">${header("修改昵称")}<label class="field">昵称<input name="name" required maxlength="40" autocomplete="nickname" value="${esc(session.user?.name || "")}"></label>${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">保存昵称</button></div></form>`;
    if (mode === "password")
      return `<form data-account-form="password">${header("修改密码")}<label class="field">当前密码<input name="currentPassword" type="password" required maxlength="128" autocomplete="current-password"></label><label class="field">新密码<input name="newPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="至少 8 位"></label><label class="field">确认新密码<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">更新密码</button></div></form>`;
    if (mode === "reset-password")
      return `<form data-account-form="reset-password">${header("找回密码")}<p class="status-note">请使用已验证的账号邮箱接收验证码，设置新密码后重新登录。</p><label class="field">邮箱<input name="email" type="email" required maxlength="254" autocomplete="username" value="${esc(email)}"></label>${codeMarkup()}<label class="field">新密码<input name="newPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="至少 8 位"></label><label class="field">确认新密码<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">重置密码</button><button type="button" class="btn quiet" data-account-action="login">返回登录</button></div></form>`;
    const register = mode === "register";
    return `<form data-account-form="${register ? "register" : "login"}">${header(register ? "注册账号" : "登录")}<label class="field">邮箱<input name="email" type="email" required maxlength="254" autocomplete="username" value="${esc(email)}"></label>${register ? '<label class="field">昵称<input name="displayName" maxlength="40" autocomplete="nickname" placeholder="可稍后修改"></label>' : ""}<label class="field">密码<input name="password" type="password" required minlength="8" maxlength="128" autocomplete="${register ? "new-password" : "current-password"}" placeholder="至少 8 位"></label>${register ? '<label class="field">确认密码<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>' + codeMarkup() : ""}${errorMarkup()}<div class="dialog-actions">${!register && !session.user ? '<button class="btn quiet account-recovery-link" type="button" data-account-action="reset-password">忘记密码</button>' : ""}<button class="btn primary" type="submit">${register ? "注册并登录" : "登录"}</button><button class="btn quiet" type="button" data-account-action="${register ? "login" : "register"}">${register ? "已有账号，去登录" : "注册账号"}</button></div></form>`;
  }
  function show(nextMode) {
    clearAvatarDraft();
    clearVerification();
    operation++;
    busy = false;
    mode = nextMode;
    error = "";
    host.openDialog(render());
  }
  async function open(section) {
    mode = session.user ? "summary" : "login";
    error = "";
    const attempt = ++operation;
    const epoch = accountVersion();
    const pending = mode === "summary" ? Promise.resolve(host.loadEmailSettings?.()) : null;
    host.openDialog(render());
    if (section === "pku-email" && mode === "summary") focusEmailSection();
    if (pending) await pending;
    if (
      attempt === operation && epoch === accountVersion() &&
      mode === "summary" && session.user && accountPanelOpen()
    ) {
      host.openDialog(render());
      if (section === "pku-email") focusEmailSection();
    }
  }
  function refresh(section) {
    if (mode !== "summary" || !session.user || !accountPanelOpen()) return false;
    host.openDialog(render());
    if (section === "pku-email") focusEmailSection();
    return true;
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
      status.textContent = challenge ? form.dataset.accountForm === "reset-password" ? "如果该邮箱有可找回的账号，验证码会发送到收件箱。验证码 10 分钟内有效，也请检查垃圾邮件。" : "验证码已发送，10 分钟内有效，请查看收件箱或垃圾邮件。" : "";
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
    if (!form || !["register", "verify-email", "reset-password"].includes(kind) || busy || sending || retryAt > Date.now()) return;
    if (kind === "reset-password" && session.user) return;
    const emailField = form.querySelector('[name="email"]');
    if (kind !== "verify-email" && !emailField?.checkValidity()) {
      emailField?.reportValidity();
      return;
    }
    const targetEmail = normalizedEmail(kind !== "verify-email" ? emailField.value : session.user?.email);
    const attempt = ++verificationOperation, epoch = accountVersion(), userId = session.user?.id;
    const current = () => attempt === verificationOperation && epoch === accountVersion() && userId === session.user?.id && formIsOpen(form);
    const controller = new AbortController();
    sendController = controller;
    sending = true;
    formError(form, "");
    updateCodeControls(form);
    try {
      const purpose = { register: "register", "verify-email": "verify_email", "reset-password": "reset_password" }[kind];
      const result = await api("/api/auth/email-code", kind !== "verify-email" ? { purpose, email: targetEmail } : { purpose }, "POST", { signal: controller.signal });
      if (!current()) return;
      challenge = { id: result.challengeId, kind, email: targetEmail, expiresAt: Date.now() + (Number(result.expiresIn) || 600) * 1000 };
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
    // Escape may dismiss the anonymous reset form while its request is pending.
    // Reopening starts a new operation so that the dismissed request stays stale.
    if (action === "open" && mode === "reset-password") {
      show(session.user ? "summary" : "login");
      return true;
    }
    if (busy) return true;
    if (host.accountTools?.handles(action)) {
      busy = true;
      try { await host.accountTools.click(button); }
      finally { busy = false; }
      return true;
    }
    if (action === "close") {
      clearAvatarDraft();
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
      await open();
      return true;
    }
    if (action === "avatar-choose") {
      button.closest("form")?.querySelector("[data-avatar-file]")?.click();
      return true;
    }
    if (action === "avatar-cancel") {
      show("summary");
      return true;
    }
    if (action === "avatar-reset") {
      await saveAvatar(button.closest("form"), null);
      return true;
    }
    if (["login", "register", "profile", "password", "avatar", "verify-email", "reset-password", "summary"].includes(action)) {
      if (["profile", "password", "avatar", "verify-email", "summary"].includes(action) && !session.user) show("login");
      else if (action === "reset-password" && session.user) show("summary");
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
    if (kind === "reset-password" && session.user) return true;
    if (kind === "avatar") {
      if (avatarImage && !selecting)
        await saveAvatar(form, form.querySelector("[data-avatar-preview]").toDataURL("image/png"));
      return true;
    }
    const values = Object.fromEntries(new FormData(form));
    if (kind === "register" && values.password !== values.confirmPassword) {
      formError(form, "两次输入的密码不一致");
      return true;
    }
    if (kind === "reset-password" && values.newPassword !== values.confirmPassword) {
      formError(form, "两次输入的新密码不一致");
      return true;
    }
    if (["register", "verify-email", "reset-password"].includes(kind)) {
      const targetEmail = normalizedEmail(kind !== "verify-email" ? values.email : session.user?.email);
      if (!challenge || challenge.kind !== kind || challenge.email !== targetEmail) {
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
    const current = () => attempt === operation && (accountChange || epoch === accountVersion()) && (!["verify-email", "reset-password"].includes(kind) || formIsOpen(form));
    busy = true;
    formError(form, "");
    lock(form, true);
    const submitButton = form.querySelector('[type="submit"]'), label = submitButton?.textContent;
    if (submitButton) submitButton.textContent = accountChange ? "正在登录…" : kind === "verify-email" ? "正在验证…" : kind === "reset-password" ? "正在重置…" : "正在保存…";
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
      } else if (kind === "reset-password") {
        const result = await api("/api/auth/reset-password", { email: normalizedEmail(values.email), challengeId: values.challengeId, code: values.code, newPassword: values.newPassword });
        if (!current()) return true;
        if (result.reset !== true) throw new Error("密码重置结果未确认");
        email = String(values.email || "").trim();
        show("login");
        host.toast("密码已重置，请使用新密码登录");
      } else if (kind === "verify-email") {
        const result = await api("/api/auth/verify-email", { challengeId: values.challengeId, code: values.code });
        if (!current() || result.user?.id !== session.user?.id) return true;
        session.user = { ...session.user, emailVerified: result.user.emailVerified };
        clearVerification();
        host.closeDialog();
        host.render();
        host.toast("邮箱已验证");
      } else if (kind === "profile") {
        const result = await api("/api/auth/profile", { name: String(values.name).trim() });
        if (!current()) return true;
        session.user = { ...session.user, name: result.user.name };
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
      if (attempt === operation) {
        registering = false;
        busy = false;
        lock(form, false);
        if (submitButton) submitButton.textContent = label;
        if (["register", "verify-email", "reset-password"].includes(kind)) updateCodeControls(form);
      }
    }
    return true;
  }
  function input(event) {
    const avatarForm = event.target.closest?.('[data-account-form="avatar"]');
    if (avatarForm && ["zoom", "x", "y"].includes(event.target.name)) {
      if (avatarImage && !busy) updateAvatarPreview(avatarForm);
      return true;
    }
    if (event.target.name === "email" && event.target.closest?.("[data-account-form]")) {
      email = event.target.value;
      const form = event.target.closest("[data-account-form]");
      if (["register", "reset-password"].includes(form.dataset.accountForm)) {
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
  function updateAvatarPreview(form) {
    drawAvatar(form.querySelector("[data-avatar-preview]"), avatarImage, {
      zoom: Number(form.elements.zoom.value),
      x: Number(form.elements.x.value),
      y: Number(form.elements.y.value),
    });
  }
  async function selectAvatar(input) {
    const file = input.files?.[0], form = input.closest("form");
    if (!file || busy) return;
    const attempt = ++selection, epoch = accountVersion();
    const current = () => attempt === selection && epoch === accountVersion() && formIsOpen(form);
    selecting = true;
    const save = form.querySelector('[type="submit"]'), status = form.querySelector("[data-avatar-selection-status]");
    save.disabled = true;
    status.textContent = "正在读取图片…";
    status.hidden = false;
    formError(form, "");
    try {
      const image = await readAvatarFile(file);
      if (!current()) return;
      avatarImage = image;
      for (const name of ["zoom", "x", "y"]) form.elements[name].value = name === "zoom" ? "1" : "0";
      updateAvatarPreview(form);
      form.querySelector("[data-avatar-preview]").hidden = false;
      form.querySelector(".avatar-editor-current").hidden = true;
      form.querySelector("[data-avatar-controls]").hidden = false;
    } catch (error) {
      if (current()) formError(form, error);
    } finally {
      if (attempt === selection) {
        selecting = false;
        save.disabled = !avatarImage;
        status.hidden = true;
        input.value = "";
      }
    }
  }
  async function saveAvatar(form, avatar) {
    if (busy || !session.user || !formIsOpen(form)) return;
    const attempt = ++operation, epoch = accountVersion(), userId = session.user.id;
    const current = () => attempt === operation && epoch === accountVersion();
    const sameAccount = () => epoch === accountVersion() && session.user?.id === userId;
    busy = true;
    // A pending file decode cannot replace the preview being saved or restored.
    selection++;
    selecting = false;
    formError(form, "");
    lock(form, true);
    const save = form.querySelector('[type="submit"]');
    save.textContent = "正在保存…";
    try {
      // Keep later avatar choices in order even when Escape dismisses a save.
      const pending = avatarSaves.catch(() => {}).then(() => {
        if (!sameAccount()) throw new Error("请先登录后重新操作");
        return api("/api/auth/avatar", { avatar });
      });
      avatarSaves = pending;
      const result = await pending;
      if (!sameAccount()) return;
      if (result.user?.id !== userId || !(result.user.avatar === null ||
          typeof result.user.avatar === "string" && /^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(result.user.avatar)))
        throw new Error("头像保存结果无法确认，请重试");
      session.user = { ...session.user, avatar: result.user.avatar };
      if (current()) {
        busy = false;
        if (formIsOpen(form)) show("summary");
      }
      host.render();
      host.toast(avatar === null ? "已恢复默认头像" : "头像已更新");
    } catch (error) {
      if (current() && formIsOpen(form)) formError(form, error);
    } finally {
      if (current()) {
        busy = false;
        lock(form, false);
        save.textContent = "保存头像";
        save.disabled = !avatarImage;
        form.querySelector("[data-avatar-selection-status]").hidden = true;
      }
    }
  }
  function change(event) {
    if (!event.target.matches?.("[data-avatar-file]")) return false;
    void selectAvatar(event.target);
    return true;
  }
  function closed() {
    if (mode === "avatar") {
      clearAvatarDraft();
      // Escape may dismiss an in-flight save; its response may update the user,
      // but must not prevent opening a different account form.
      busy = false;
    }
  }
  return { click, submit, input, change, closed, open, refresh };
}
