import {
  api,
  session,
  authenticate,
  logout,
  accountVersion,
} from "./team-store.js";

export function createAccountUI(host) {
  const esc = host.escapeHtml;
  let mode = "login",
    busy = false,
    error = "",
    email = "",
    operation = 0;
  function reset() {
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
    return `<p class="error" role="alert" data-account-error ${error ? "" : "hidden"}>${esc(error)}</p>`;
  }
  function render() {
    if (mode === "summary" && session.user)
      return `${header("账号")}<div class="account-summary"><h3>${esc(session.user.display_name || session.user.name || "我的账号")}</h3><p class="status-note">${esc(session.user.email)}</p></div><div class="account-actions"><button type="button" class="btn" data-account-action="profile" ${busy ? "disabled" : ""}>修改昵称</button><button type="button" class="btn" data-account-action="password" ${busy ? "disabled" : ""}>修改密码</button></div>${errorMarkup()}<div class="dialog-actions"><button type="button" class="btn quiet" data-account-action="logout" ${busy ? "disabled" : ""}>${busy ? "正在退出…" : "退出登录"}</button></div>`;
    if (mode === "profile")
      return `<form data-account-form="profile">${header("修改昵称")}<label class="field">昵称<input name="name" required maxlength="40" autocomplete="nickname" value="${esc(session.user?.display_name || session.user?.name || "")}"></label>${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">保存昵称</button></div></form>`;
    if (mode === "password")
      return `<form data-account-form="password">${header("修改密码")}<label class="field">当前密码<input name="currentPassword" type="password" required maxlength="128" autocomplete="current-password"></label><label class="field">新密码<input name="newPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="至少 8 位"></label><label class="field">确认新密码<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">更新密码</button></div></form>`;
    const register = mode === "register";
    return `<form data-account-form="${register ? "register" : "login"}">${header(register ? "注册账号" : "登录")}<label class="field">邮箱<input name="email" type="email" required maxlength="254" autocomplete="username" value="${esc(email)}"></label>${register ? '<label class="field">昵称<input name="displayName" maxlength="40" autocomplete="nickname" placeholder="可稍后修改"></label>' : ""}<label class="field">密码<input name="password" type="password" required minlength="8" maxlength="128" autocomplete="${register ? "new-password" : "current-password"}" placeholder="至少 8 位"></label>${register ? '<label class="field">确认密码<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>' : ""}${errorMarkup()}<div class="dialog-actions"><button class="btn primary" type="submit">${register ? "注册并登录" : "登录"}</button><button class="btn quiet" type="button" data-account-action="${register ? "login" : "register"}">${register ? "已有账号，去登录" : "注册账号"}</button></div></form>`;
  }
  function show(nextMode) {
    mode = nextMode;
    error = "";
    host.openDialog(render());
  }
  function formError(form, message) {
    error = message;
    const node = form.querySelector("[data-account-error]");
    if (node) {
      node.textContent = message;
      node.hidden = !message;
    }
  }
  function lock(form, disabled) {
    form.querySelectorAll("input,button").forEach((field) => {
      if (disabled) {
        field.dataset.wasDisabled = String(field.disabled);
        field.disabled = true;
      } else {
        field.disabled = field.dataset.wasDisabled === "true";
        delete field.dataset.wasDisabled;
      }
    });
  }
  async function signOut() {
    if (busy) return;
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
        error = e.message;
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
      error = "";
      host.closeDialog();
      return true;
    }
    if (action === "open") {
      show(session.user ? "summary" : "login");
      return true;
    }
    if (
      ["login", "register", "summary", "profile", "password"].includes(action)
    ) {
      if (["summary", "profile", "password"].includes(action) && !session.user)
        show("login");
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
    if (busy) return true;
    const values = Object.fromEntries(new FormData(form));
    if (kind === "register" && values.password !== values.confirmPassword) {
      formError(form, "两次输入的密码不一致");
      return true;
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
    const current = () =>
      attempt === operation && (accountChange || epoch === accountVersion());
    busy = true;
    formError(form, "");
    lock(form, true);
    const submitButton = form.querySelector('[type="submit"]'),
      label = submitButton?.textContent;
    if (submitButton)
      submitButton.textContent = accountChange ? "正在登录…" : "正在保存…";
    try {
      if (accountChange) {
        email = String(values.email || "").trim();
        const data = await authenticate(kind, { ...values, email });
        if (!current()) return true;
        host.setState(data);
        host.closeDialog();
        host.render();
        host.toast("已登录");
        email = "";
      } else if (kind === "profile") {
        const result = await api("/api/auth/profile", {
          name: String(values.name).trim(),
        });
        if (!current()) return true;
        session.user = { ...result.user, display_name: result.user.name };
        host.closeDialog();
        host.render();
        host.toast("昵称已更新");
      } else if (kind === "password") {
        await api("/api/auth/password", {
          currentPassword: values.currentPassword,
          newPassword: values.newPassword,
        });
        if (!current()) return true;
        host.closeDialog();
        host.toast("密码已更新");
      }
    } catch (e) {
      if (current())
        formError(
          form,
          String(e.message || "操作未完成，请重试").replaceAll("后端", "服务"),
        );
    } finally {
      if (current()) {
        busy = false;
        lock(form, false);
        if (submitButton) submitButton.textContent = label;
      }
    }
    return true;
  }
  function input(event) {
    if (
      event.target.name === "email" &&
      event.target.closest?.("[data-account-form]")
    ) {
      email = event.target.value;
      return true;
    }
    return false;
  }
  return { render, click, submit, input, reset };
}
