// Served only from source in development/test; never bundled into release images.
export function createAccountTools(host) {
  let busy = false, target = null;
  const handles = selected => ["test-reset-open", "test-reset-cancel", "test-reset-confirm", "test-reset-reload"].includes(selected);
  const action = name => `data-account-action="${name}"`;
  function render(disabled = false) {
    if (!host.session.user) return "";
    return `<section class="account-connection" aria-label="测试工具"><div><h3>测试工具</h3><p class="status-note">清空当前账号的数据，保留账号和教学网连接</p></div><button type="button" class="btn quiet" ${action("test-reset-open")} ${disabled || busy ? "disabled" : ""}>清空测试数据</button></section>`;
  }
  function confirmation(message = "") {
    return `<header class="dialog-header"><h2>清空测试数据</h2></header><p>将删除当前账号的任务、通知、导入历史、识别结果、附件、去重记录及测试反馈，并关闭定时同步。</p><p class="status-note">账号、密码、头像和教学网连接会保留。清空后可以重新导入同一批内容；其他窗口需刷新。此操作无法撤销。</p><p class="error" role="alert">${host.escapeHtml(message)}</p><div class="dialog-actions"><button type="button" class="btn quiet" ${action("test-reset-cancel")} ${busy ? "disabled" : ""}>取消</button><button type="button" class="btn primary" ${action("test-reset-confirm")} ${busy ? "disabled" : ""}>${busy ? "正在清空…" : "确认清空"}</button></div>`;
  }
  async function click(button) {
    const selected = button.dataset.accountAction;
    if (!handles(selected)) return false;
    if (busy || !host.session.user) return true;
    if (selected === "test-reset-reload") { host.reload(); return true; }
    if (selected === "test-reset-cancel") { host.closeDialog(); return true; }
    if (selected === "test-reset-open") {
      target = { uid: host.session.user.id, epoch: host.accountVersion() };
      host.openDialog(confirmation()); return true;
    }
    if (!target) return true;
    const { uid, epoch } = target;
    const current = () => host.session.user?.id === uid && host.accountVersion() === epoch;
    if (!current()) { host.closeDialog(); return true; }
    busy = true;
    host.openDialog(confirmation());
    try {
      // Wait for existing writes; confirmation also discards failed local edits.
      try { await host.flushStore(); } catch {}
      if (!current()) return true;
      const result = await host.api("/api/environment-tools/reset-account", { confirm: true, accountId: uid }, "POST", { timeoutMs: 360000 });
      if (!current()) return true;
      if (result.reset !== true) throw new Error("清空结果尚未确认，请刷新后核对");
      // Page reload drops pending UI state/polling; no old workspace is saved back.
      if (result.pendingFiles) {
        busy = false;
        host.openDialog(`<header class="dialog-header"><h2>测试数据已清空</h2></header><p>部分附件文件等待后台清理，记录已清空，可以继续测试。</p><div class="dialog-actions"><button type="button" class="btn primary" ${action("test-reset-reload")}>刷新并继续</button></div>`);
      } else host.reload();
    } catch (error) {
      if (current()) {
        busy = false;
        host.openDialog(confirmation(error.message || "清空失败，请重试"));
      }
    } finally { busy = false; }
    return true;
  }
  return { render, click, handles };
}
