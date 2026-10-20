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
  let mode = "login",
    busy = false,
    error = "",
    email = "",
    operation = 0;
  let avatarImage = null, selection = 0, selecting = false;
  function clearAvatarDraft() {
    selection++;
    avatarImage = null;
    selecting = false;
  }
  function reset() {
    clearAvatarDraft();
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
  function teachingAccount() {
    return `<section class="account-connection" aria-label="教学网账号管理"><div><h3>教学网账号</h3><p class="status-note">管理用于获取课程作业与通知的账号</p></div><button type="button" class="btn quiet" data-import-action="credentials" ${busy ? "disabled" : ""}>管理连接</button></section>`;
  }
  function render() {
    if (mode === "summary" && session.user)
      return `${header("账号")}<div class="account-summary"><button type="button" class="avatar account-avatar" data-account-action="avatar" aria-label="更改头像" ${busy ? "disabled" : ""}>${avatarContent(session.user, esc)}<span class="account-avatar-overlay" aria-hidden="true">更改头像</span></button><div class="account-summary-copy"><h3>${esc(session.user.name || "我的账号")}</h3><p class="status-note">${esc(session.user.email)}</p></div></div><div class="account-actions"><button type="button" class="btn" data-account-action="profile" ${busy ? "disabled" : ""}>修改昵称</button><button type="button" class="btn" data-account-action="password" ${busy ? "disabled" : ""}>修改密码</button></div>${teachingAccount()}${host.feedbackBox?.() || ""}${errorMarkup()}<div class="dialog-actions"><button type="button" class="btn quiet" data-account-action="logout" ${busy ? "disabled" : ""}>${busy ? "正在退出…" : "退出登录"}</button></div>`;
    if (mode === "avatar")
      return `<form data-account-form="avatar">${header("更改头像")}<div class="avatar-editor"><div class="avatar-editor-preview"><div class="avatar avatar-editor-current">${avatarContent(session.user, esc)}</div><canvas data-avatar-preview width="256" height="256" role="img" aria-label="头像裁切预览" hidden></canvas></div><button type="button" class="btn" data-account-action="avatar-choose">选择图片</button><input type="file" data-avatar-file accept="image/png,image/jpeg,image/webp" aria-label="选择头像图片" hidden><p class="status-note">支持 JPG、PNG、WebP，最大 5 MB</p><p class="status-note" data-avatar-selection-status role="status" hidden></p></div><div class="avatar-crop-controls" data-avatar-controls hidden><label>缩放<input type="range" name="zoom" min="1" max="3" step="0.01" value="1"></label><label>水平位置<input type="range" name="x" min="-100" max="100" value="0"></label><label>垂直位置<input type="range" name="y" min="-100" max="100" value="0"></label></div>${errorMarkup()}<div class="avatar-editor-actions">${session.user?.avatar ? '<button type="button" class="btn quiet" data-account-action="avatar-reset">恢复默认头像</button>' : ""}<div class="dialog-actions"><button type="button" class="btn quiet" data-account-action="avatar-cancel">取消</button><button type="submit" class="btn primary" disabled>保存头像</button></div></div></form>`;
    if (mode === "profile")
      return `<form data-account-form="profile">${header("修改昵称")}<label class="field">昵称<input name="name" required maxlength="40" autocomplete="nickname" value="${esc(session.user?.name || "")}"></label>${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">保存昵称</button></div></form>`;
    if (mode === "password")
      return `<form data-account-form="password">${header("修改密码")}<label class="field">当前密码<input name="currentPassword" type="password" required maxlength="128" autocomplete="current-password"></label><label class="field">新密码<input name="newPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password" placeholder="至少 8 位"></label><label class="field">确认新密码<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>${errorMarkup()}<div class="dialog-actions"><button type="submit" class="btn primary">更新密码</button></div></form>`;
    const register = mode === "register";
    return `<form data-account-form="${register ? "register" : "login"}">${header(register ? "注册账号" : "登录")}<label class="field">邮箱<input name="email" type="email" required maxlength="254" autocomplete="username" value="${esc(email)}"></label>${register ? '<label class="field">昵称<input name="displayName" maxlength="40" autocomplete="nickname" placeholder="可稍后修改"></label>' : ""}<label class="field">密码<input name="password" type="password" required minlength="8" maxlength="128" autocomplete="${register ? "new-password" : "current-password"}" placeholder="至少 8 位"></label>${register ? '<label class="field">确认密码<input name="confirmPassword" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label>' : ""}${errorMarkup()}<div class="dialog-actions"><button class="btn primary" type="submit">${register ? "注册并登录" : "登录"}</button><button class="btn quiet" type="button" data-account-action="${register ? "login" : "register"}">${register ? "已有账号，去登录" : "注册账号"}</button></div></form>`;
  }
  function show(nextMode) {
    clearAvatarDraft();
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
      clearAvatarDraft();
      error = "";
      host.closeDialog();
      return true;
    }
    if (action === "open") {
      show(session.user ? "summary" : "login");
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
    if (
      ["login", "register", "profile", "password", "avatar"].includes(action)
    ) {
      if (["profile", "password", "avatar"].includes(action) && !session.user)
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
        session.user = result.user;
        if (formIsOpen(form)) host.closeDialog();
        host.render();
        host.toast("昵称已更新");
      } else if (kind === "password") {
        await api("/api/auth/password", {
          currentPassword: values.currentPassword,
          newPassword: values.newPassword,
        });
        if (!current()) return true;
        if (formIsOpen(form)) host.closeDialog();
        host.toast("密码已更新");
      }
    } catch (e) {
      if (current())
        formError(
          form,
          e,
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
    const avatarForm = event.target.closest?.('[data-account-form="avatar"]');
    if (avatarForm && ["zoom", "x", "y"].includes(event.target.name)) {
      if (avatarImage && !busy) updateAvatarPreview(avatarForm);
      return true;
    }
    if (
      event.target.name === "email" &&
      event.target.closest?.("[data-account-form]")
    ) {
      email = event.target.value;
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
    const attempt = ++operation, epoch = accountVersion();
    const current = () => attempt === operation && epoch === accountVersion();
    busy = true;
    // A pending file decode cannot replace the preview being saved or restored.
    selection++;
    selecting = false;
    formError(form, "");
    lock(form, true);
    const save = form.querySelector('[type="submit"]');
    save.textContent = "正在保存…";
    try {
      const result = await api("/api/auth/avatar", { avatar });
      if (!current()) return;
      session.user = result.user;
      busy = false;
      if (formIsOpen(form)) show("summary");
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
  return { click, submit, input, change, closed };
}
