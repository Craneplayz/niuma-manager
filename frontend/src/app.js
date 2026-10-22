import {
  api,
  session,
  readStore,
  readCancelledTasks,
  initSession,
  reconnectStore,
  reloadStore,
  refreshStore,
  saveStoredTask,
  setStoredTaskStatus,
  flushStore,
  retrySave,
  trashStoredTasks,
  restoreStoredTasks,
  accountVersion,
  setStoredTaskDeadline,
} from "./team-store.js";
import { icon } from "./icons.js";
import { avatarContent } from "./account-avatar.js";
import { newId } from "./id.js";
import {
  todayKey,
  shiftDay,
  filterTasks,
  isNotice,
  taskEditSnapshot,
  assertCurrentTaskEdit,
  deadlineLabel,
} from "./task-model.js";
import { renderTaskList, renderDayPanel } from "./task-views.js";
import { projectReviewTasks } from "./review-tasks.js";
import { renderCalendar } from "./calendar.js";
import { createCalendarDrag } from "./calendar-drag.js";
import { renderTaskDrawer } from "./task-drawer.js";
import { renderPageHeading } from "./page-layout.js";
import { loadTaskEvidence } from "./task-evidence.js";
import { demoWorkspace } from "./demo.js";
import { createImportUI } from "./import-ui.js";
import { createAccountUI } from "./account-ui.js";
import { readNavigation, navigationURL } from "./navigation.js";
import { feedbackBox } from "./usage-feedback.js";
import { describeEnvironment } from "./environment-badge.js";
import { userErrorMarkup, submitErrorFeedback, resetErrorFeedback, pruneErrorFeedback } from "./user-errors.js";

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const app = document.querySelector("#app"),
  dialog = document.querySelector("#dialog"),
  drawer = document.querySelector("#task-drawer"),
  sidebarTooltip = document.querySelector("#sidebar-tooltip");
const params = new URLSearchParams(location.search);
let today = todayKey(), dayTimer;
const dateFormat = new Intl.DateTimeFormat("zh-CN", {
  month: "long", day: "numeric", weekday: "long",
});
const dateLabel = () => dateFormat.format(new Date(today + "T12:00:00"));
let demo = params.get("demo") === "1",
  state = demo ? demoWorkspace(today) : readStore(),
  loading = !demo,
  loadError = "",
  reviewTaskBusy = false,

  drawerId = null,
  drawerEditing = false,
  drawerError = null,
  lastFocus = null,
  toastTimer,
  sidebarTooltipTimer,
  sidebarTooltipTarget,
  environment = null,
  backendRevision = "unknown";
const busyTasks = new Set();
const taskBusy = id => busyTasks.has(id);
function canChangeTask(id) {
  if (taskBusy(id) || loading || session.changing) return false;
  if (reviewTaskBusy) {
    toast("正在确认待核对任务，请稍候");
    return false;
  }
  if (!demo && session.sync === "error") {
    toast("请先重试保存或重新读取任务");
    return false;
  }
  return true;
}
function syncTaskControls() {
  document.querySelectorAll("[data-task-complete]").forEach(button => {
    button.disabled = reviewTaskBusy || taskBusy(button.dataset.taskComplete) ||
      button.dataset.taskComplete.startsWith("review:") && imports.isReviewBusy() ||
      !demo && session.sync === "error";
  });
  document.querySelectorAll("[data-restore-task]").forEach(button => {
    button.disabled = reviewTaskBusy || taskBusy(button.dataset.restoreTask) || !demo && session.sync === "error";
  });
}
function finishTask(id, epoch, repaint = false) {
  if (epoch !== accountVersion()) return;
  busyTasks.delete(id);
  if (repaint) render();
  else syncTaskControls();
  // Another operation must not replace an open editor's unsent draft.
  if (drawer.open && drawerId === id && !drawerEditing) updateDrawer();
}
async function stageTaskSave(saving, id, epoch) {
  try {
    await saving.staged;
    return epoch === accountVersion();
  } catch (error) {
    if (epoch === accountVersion()) {
      toast(error.message);
      finishTask(id, epoch);
    }
    return false;
  }
}
let preferences = {};
try {
  preferences = JSON.parse(localStorage.getItem("niuma.task-ui.v2") || "{}");
} catch {}
const ui = {
  ...readNavigation(params, { today, preferences }),
  query: "",
  course: "",
  source: "",
  dateFrom: "",
  dateTo: "",
  filterOpen: false,
  sidebarCollapsed: preferences.sidebarCollapsed === true,
  mobileMenu: false,
};
const host = {
  escapeHtml: esc,
  icon,
  toast,
  reportReviewError: error => toastError(error, 'import_review'),
  render,
  renderPageHeading: workspaceHeading,
  revealImportResult(id) {
    const target = document.getElementById(id);
    if (!target) return;
    const root = document.documentElement;
    root.style.removeProperty("--recovery-scroll-space");
    // Allow even a result near the end of the page to align with the viewport top.
    const top = window.scrollY + target.getBoundingClientRect().top - 24;
    const pageBottom = window.scrollY + document.querySelector(".import-page").getBoundingClientRect().bottom;
    const space = Math.max(0, Math.ceil(top + window.innerHeight - pageBottom));
    root.style.setProperty("--recovery-scroll-space", `${space}px`);
    target?.focus({ preventScroll: true });
    target.scrollIntoView({ block: "start", inline: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  },
  clearImportResultHighlight() {
    document.querySelectorAll("[data-source-sync-highlight]").forEach(target => {
      target.removeAttribute("data-source-sync-highlight");
      target.removeAttribute("aria-description");
    });
    document.documentElement.style.removeProperty("--recovery-scroll-space");
  },
  openDialog,
  closeDialog,
  isDialogOpen: selector => dialog.open && Boolean(dialog.querySelector(selector)),
  refresh,
  openTask(id) {
    if (state.tasks.some(task => task.id === id && !task.recycleBin)) openTask(id);
    else toast("这条任务或通知已不在当前列表中");
  },
  getState: () => state,
  getCancelledTasks: () => demo ? [] : readCancelledTasks(),
  isDemo: () => demo,
  isImportVisible: () => ui.page === "import",
  getImportSource: () => ui.importSource,
  onImportSourceChange(source) {
    ui.importSource = source;
    syncNavigation();
  },
  feedbackBox: () => demo ? "" : feedbackBox(),
  setState(next) {
    state = next;
    demo = false;
    const url = new URL(location.href);
    url.searchParams.delete("demo");
    history.replaceState(null, "", url);
    loadError = "";
    resetAccountUI();
    render();
    if (ui.page === "import") void imports.load();
    else void imports.loadTaskReviews();
  },
};
const imports = createImportUI(host),
  account = createAccountUI(host);
const calendarDrag = createCalendarDrag({ root: app, getTask: draggableTask, onDrop: dropTaskOnDate });
function draggableTask(id) {
  if (reviewTaskBusy || taskBusy(id) || loading || session.changing || ui.page !== "tasks" ||
      taskView() !== "calendar" ||
      dialog.open || drawer.open || !demo && !session.user) return null;
  return state.tasks.find((task) => task.id === id &&
    task.status !== "done" && !task.recycleBin);
}
function clearUndo() {
  clearTimeout(toastTimer);
  const el = document.querySelector("#toast");
  el.innerHTML = "";
  el.classList.remove("visible");
}
function resetAccountUI() {
  clearUndo();
  busyTasks.clear();
  reviewTaskBusy = false;
  imports.reset();
  ui.query = "";
  ui.course = "";
  ui.source = "";
  ui.dateFrom = "";
  ui.dateTo = "";
  drawer.close();
  drawerId = null;
}
function syncNavigation(method = "push") {
  const url = navigationURL(location.href, ui);
  if (!demo) url.searchParams.delete("demo");
  if (url.href !== location.href) history[method === "replace" ? "replaceState" : "pushState"](null, "", url);
}
function remember() {
  try {
    localStorage.setItem(
      "niuma.task-ui.v2",
      JSON.stringify({
        view: ui.view,
        mode: ui.mode,
        sidebarCollapsed: ui.sidebarCollapsed,
      }),
    );
  } catch {}
}
function toast(message, undo) {
  const epoch = accountVersion();
  clearTimeout(toastTimer);
  const el = document.querySelector("#toast");
  el.innerHTML = `<span>${esc(message)}</span>${undo ? '<button type="button" id="toast-undo">撤销</button>' : ""}`;
  el.classList.add("visible");
  if (undo)
    el.querySelector("button").onclick = async () => {
      if (epoch !== accountVersion()) return;
      try {
        await undo();
        el.classList.remove("visible");
      } catch (e) {
        toastError(e);
      }
    };
  toastTimer = setTimeout(
    () => el.classList.remove("visible"),
    undo ? 7000 : 4000,
  );
}
function toastError(error, action = "general") {
  if (dialog.open) {
    let target = dialog.querySelector("[data-dialog-operation-error]");
    if (!target) {
      target = document.createElement("div");
      target.dataset.dialogOperationError = "";
      dialog.append(target);
    }
    target.innerHTML = userErrorMarkup(error, action);
    return;
  }
  if (drawer.open && !drawerEditing) {
    drawerError = { error, action };
    updateDrawer();
    return;
  }
  clearTimeout(toastTimer);
  const el = document.querySelector("#toast");
  el.innerHTML = `${userErrorMarkup(error, action)}<button type="button" class="btn quiet" data-action="error-dismiss">关闭提示</button>`;
  el.classList.add("visible");
}
function openDialog(html) {
  hideSidebarTooltip();
  dialog.innerHTML = html;
  if (!dialog.querySelector("#dialog-heading")) {
    const heading = dialog.querySelector("h1,h2,h3");
    if (heading) heading.id = "dialog-heading";
    else dialog.setAttribute("aria-label", "操作面板");
  }
  if (!dialog.open) dialog.showModal();
}
function closeDialog() {
  dialog.close();
}
dialog.addEventListener("close", () => {
  if (!dialog.open) account.closed?.();
});
async function refresh() {
  if (demo) return;
  state = await refreshStore();
  render();
}
const listItems = () => projectReviewTasks(state.tasks, imports.getReviewItems(), today);
const editableTask = id => state.tasks.find(task => task.id === id)
  || listItems().find(task => task.id === id && task.reviewCandidateId && !task.reviewChange);
const filtered = () => filterTasks(listItems(), { ...ui, today });
const taskView = () => (ui.scope === "all" ? ui.view : "list");
const calendarItems = () => filterTasks(state.tasks, { scope: "calendar", today });
const headingCount = () => taskView() === "calendar"
  ? filterTasks(state.tasks, { scope: "all", today }).length
  : filtered().length;
const courses = () =>
  [...new Set(listItems().map((t) => t.course).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b, "zh"),
  );
const sources = () =>
  [...new Set(listItems().map((t) => t.source || "手动添加"))].sort();
function sidebar() {
  const mobile = matchMedia("(max-width: 700px)").matches;
  const expanded = mobile ? ui.mobileMenu : !ui.sidebarCollapsed;
  const toggleLabel = mobile ? "关闭侧栏" : expanded ? "收起侧栏" : "展开侧栏";
  const counts = {
    all: filterTasks(listItems(), { scope: "all", today }).length,
    near: filterTasks(listItems(), { scope: "near", today }).length,
    done: filterTasks(state.tasks, { scope: "done", today }).length,
    notices: filterTasks(listItems(), { scope: "notices", today }).length,
  };
  return `<aside id="sidebar" class="sidebar" aria-label="主导航" ${mobile && !ui.mobileMenu ? "inert" : ""}><div class="sidebar-top"><button type="button" id="sidebar-toggle" class="icon-button sidebar-toggle" data-action="sidebar-toggle" aria-controls="sidebar" aria-expanded="${expanded}" aria-label="${toggleLabel}" data-sidebar-label="${toggleLabel}">${icon("sidebar")}</button><a href="/" class="brand" aria-label="牛马管理器">${icon("cow")}<span>牛马管理器</span></a></div><button type="button" class="btn primary import-entry" data-page="import" aria-label="导入内容" data-sidebar-label="导入内容">${icon("plus")}<span>导入内容</span></button><nav class="task-nav"><span class="nav-label">任务</span>${[
    ["all", "全部任务", "tasks"],
    ["notices", "通知", "info"],
    ["near", "临近截止", "clock"],
    ["done", "已完成", "check"],
  ]
    .map(
      ([scope, label, name]) =>
        `<button type="button" class="nav-item ${ui.page === "tasks" && ui.scope === scope ? "active" : ""}" data-scope="${scope}" aria-label="${label}" data-sidebar-label="${label}" ${ui.page === "tasks" && ui.scope === scope ? 'aria-current="page"' : ""}>${icon(name)}<span class="nav-text">${label}</span><span class="nav-count">${counts[scope]}</span></button>`,
    )
    .join(
      "",
    )}</nav><div class="sidebar-bottom">${demo ? '<div class="preview-note">示例预览 <a href="/">使用我的账号 ↗</a></div>' : ""}<button type="button" class="account-entry" data-account-action="open" aria-label="账号与数据" data-sidebar-label="账号与数据"><span class="avatar">${avatarContent(session.user, esc)}</span><span class="account-name">${esc(session.user?.name || "登录账号")}<small id="save-status">${demo ? "示例数据" : saveLabel()}</small>${!demo && session.user && !session.user.emailVerified ? '<small class="account-verification-hint">邮箱待验证</small>' : ""}</span>${icon("chevron")}</button></div></aside>`;
}
function hideSidebarTooltip() {
  clearTimeout(sidebarTooltipTimer);
  sidebarTooltipTarget?.removeAttribute("aria-describedby");
  sidebarTooltipTarget = null;
  sidebarTooltip.hidden = true;
}
function hasSidebarTooltip(button) {
  return button?.isConnected && !matchMedia("(max-width: 700px)").matches &&
    (ui.sidebarCollapsed || button.id === "sidebar-toggle") &&
    !dialog.open && !drawer.open;
}
function showSidebarTooltip(button) {
  if (!hasSidebarTooltip(button)) return;
  sidebarTooltipTarget = button;
  sidebarTooltip.textContent = button.dataset.sidebarLabel;
  sidebarTooltip.hidden = false;
  const anchor = button.getBoundingClientRect();
  const tip = sidebarTooltip.getBoundingClientRect();
  sidebarTooltip.style.left = `${Math.max(8, Math.min(anchor.right + 10, innerWidth - tip.width - 8))}px`;
  sidebarTooltip.style.top = `${Math.max(8, Math.min(anchor.top + (anchor.height - tip.height) / 2, innerHeight - tip.height - 8))}px`;
  button.setAttribute("aria-describedby", "sidebar-tooltip");
}
function updateSidebarState() {
  hideSidebarTooltip();
  const mobile = matchMedia("(max-width: 700px)").matches;
  const expanded = mobile ? ui.mobileMenu : !ui.sidebarCollapsed;
  const toggleLabel = mobile ? "关闭侧栏" : expanded ? "收起侧栏" : "展开侧栏";
  const workspace = document.querySelector(".workspace");
  workspace.classList.toggle("sidebar-collapsed", ui.sidebarCollapsed);
  workspace.classList.toggle("menu-open", ui.mobileMenu);
  document.querySelector("#sidebar").inert = mobile && !ui.mobileMenu;
  document.querySelector("#main").inert = mobile && ui.mobileMenu;
  const toggle = document.querySelector("#sidebar-toggle");
  toggle.setAttribute("aria-expanded", String(expanded));
  toggle.setAttribute("aria-label", toggleLabel);
  toggle.dataset.sidebarLabel = toggleLabel;
  document.querySelector("#sidebar-show").setAttribute("aria-expanded", String(ui.mobileMenu));
  if (environment) {
    const accountEntry = document.querySelector(".account-entry");
    if (!mobile && ui.sidebarCollapsed) accountEntry.removeAttribute("title");
    else accountEntry.title = environment.detail;
  }
}
function saveLabel() {
  if (loading) return "连接中…";
  if (!session.user) return loadError ? "连接失败" : "管理账号";
  return (
    { saving: "正在保存…", error: "保存失败", saved: "已保存" }[session.sync] ||
    "已保存"
  );
}
function toolbar() {
  const mixed = ui.scope === "near" || ui.scope === "done";
  const searchLabel = ui.scope === "notices" ? "搜索通知" : mixed ? "搜索任务与通知" : "搜索任务";
  const dateLabel = ui.scope === "notices" ? "关键日期" : mixed ? "日期范围" : "截止日期";
  return `<div class="query-toolbar"><label class="search-field">${icon("search")}<input id="task-search" type="search" aria-label="${searchLabel}" placeholder="${searchLabel}" value="${esc(ui.query)}" autocomplete="off"></label><label class="course-filter"><span class="sr-only">课程筛选</span><select id="course-filter" aria-label="课程筛选"><option value="">全部课程</option>${courses()
    .map(
      (c) =>
        `<option ${ui.course === c ? "selected" : ""} value="${esc(c)}">${esc(c)}</option>`,
    )
    .join(
      "",
    )}</select></label><button type="button" class="btn filter-button ${ui.filterOpen || ui.source || ui.dateFrom || ui.dateTo ? "selected" : ""}" data-action="filter-toggle" aria-expanded="${ui.filterOpen}" aria-controls="advanced-filters">${icon("filter")}筛选${ui.source || ui.dateFrom || ui.dateTo ? '<span class="filter-dot"></span>' : ""}</button>${ui.query || ui.course || ui.source || ui.dateFrom || ui.dateTo ? '<button type="button" class="btn quiet reset-filters" data-action="filter-reset">清除</button>' : ""}</div>${
    ui.filterOpen
      ? `<div id="advanced-filters" class="advanced-filters"><label class="field">来源<select id="source-filter"><option value="">全部来源</option>${sources()
          .map(
            (s) =>
              `<option ${ui.source === s ? "selected" : ""} value="${esc(s)}">${esc(s)}</option>`,
          )
          .join(
            "",
          )}</select></label><label class="field">${dateLabel}，从<input id="date-from" type="date" value="${esc(ui.dateFrom)}"></label><label class="field">到<input id="date-to" type="date" value="${esc(ui.dateTo)}"></label></div>`
      : ""
  }`;
}
function monthLabel() {
  const d = new Date(ui.anchor + "T12:00:00");
  return `${d.getFullYear()}年 ${d.getMonth() + 1}月`;
}
function calendarTools() {
  return `<div class="calendar-tools"><div class="calendar-period"><h2>${monthLabel()}</h2><div class="calendar-arrows"><button type="button" class="icon-button" data-action="calendar-prev" aria-label="${ui.mode === "month" ? "上个月" : "上一周"}">${icon("chevron", "flip")}</button><button type="button" class="icon-button" data-action="calendar-next" aria-label="${ui.mode === "month" ? "下个月" : "下一周"}">${icon("chevron")}</button><button type="button" class="btn quiet" data-action="calendar-today">今天</button></div></div><div class="calendar-options"><label class="jump-control" title="跳转日期">${icon("calendar")}<span class="sr-only">跳转日期</span><input id="calendar-jump" type="date" value="${esc(ui.selectedDate)}" aria-label="跳转日期"></label><div class="segmented" aria-label="日历范围"><button type="button" data-calendar-mode="month" aria-pressed="${ui.mode === "month"}">月</button><button type="button" data-calendar-mode="week" aria-pressed="${ui.mode === "week"}">周</button></div></div></div>`;
}
function taskResults() {
  if (taskView() === "list")
    return `${imports.taskReviewsStatus()}${renderTaskList(filtered(), { escapeHtml: esc, icon, today, reviewBusy: imports.isReviewBusy(), emptyLabel: ui.scope === "notices" ? "没有符合条件的通知" : ui.scope === "all" ? "没有符合条件的任务" : "没有符合条件的任务或通知" })}`;
  const items = calendarItems();
  return `${calendarTools()}<div class="calendar-layout"><div class="calendar-grid-wrap">${renderCalendar({ tasks: items, anchorDate: ui.anchor, mode: ui.mode, selectedDate: ui.selectedDate, today, allowDateDrag: true, escapeHtml: esc })}</div>${renderDayPanel({ tasks: items, selectedDate: ui.selectedDate, pane: ui.pane, allowDateDrag: true, escapeHtml: esc, icon, today })}</div>`;
}
function workspaceHeading(options) {
  return renderPageHeading({
    ...options,
    navigation: `<button type="button" id="sidebar-show" class="icon-button" data-action="sidebar-toggle" aria-controls="sidebar" aria-expanded="${ui.mobileMenu}" aria-label="展开侧栏">${icon("sidebar")}</button>`,
    metadata: `<time class="main-date" datetime="${today}">${esc(dateLabel())}</time>`,
    escapeHtml: esc,
  });
}
function tasksPage() {
  const view = taskView();
  const viewSwitch = ui.scope === "all"
    ? `<div class="segmented task-view-switch" role="group" aria-label="任务视图"><button type="button" data-view="list" aria-pressed="${view === "list"}">${icon("tasks")}<span>清单</span></button><button type="button" data-view="calendar" aria-pressed="${view === "calendar"}">${icon("calendar")}<span>日历</span></button></div>`
    : "";
  const label =
    { all: "全部任务", near: "临近截止", done: "已完成", notices: "通知" }[
      ui.scope
    ] || "全部任务";
  const heading = workspaceHeading({
    title: label,
    count: headingCount(),
    countId: "result-count",
    controls: viewSwitch,
    actions: ui.scope === "notices" ? "" : `<button type="button" class="btn add-task" data-action="task-new" aria-label="添加任务">${icon("plus")}<span>添加任务</span></button>`,
    escapeHtml: esc,
  });
  return `<section class="task-page ${view === "calendar" ? "is-calendar" : ""}">${heading}${view === "list" ? toolbar() : ""}<div id="task-results">${taskResults()}</div>${state.trash.length ? `<footer class="task-page-footer"><button type="button" class="btn quiet" data-action="open-trash">${icon("trash")}回收站 <span>${state.trash.length}</span></button></footer>` : ""}<datalist id="course-options">${courses()
    .map((c) => `<option value="${esc(c)}">`)
    .join("")}</datalist></section>`;
}
function syncAlert() {
  return loadError
    ? `<div class="connection-alert">${userErrorMarkup(loadError, "tasks_load")}<button type="button" class="btn quiet" data-action="reconnect">重新连接</button></div>`
    : session.sync === "error" && !demo
      ? `<div class="connection-alert task-save-alert"><div class="sync-error-message">${userErrorMarkup(session.failure || session.error, "tasks_save")}<p>待保存操作保留在当前浏览器。${session.conflict ? '重新读取将放弃本页待保存的修改。' : '重试前会先核对服务器是否已经保存。'}</p></div><button type="button" class="btn quiet" data-action="${session.conflict ? "reload" : "retry-save"}">${session.conflict ? "重新读取" : "重试保存"}</button></div>`
      : "";
}
function focusToken(el) {
  if (el?.id) return { id: el.id };
  const key = Object.keys(el?.dataset || {}).find((key) =>
    [
      "action",
      "taskOpen",
      "taskComplete",
      "noticeRead",
      "scope",
      "page",
      "view",
      "calendarDay",
      "calendarMode",
      "calendarPane",
      "accountAction",
    ].includes(key),
  );
  return key ? { key, value: el.dataset[key] } : null;
}
function restoreFocus(token) {
  if (!token) return;
  const el = token.id
    ? document.getElementById(token.id)
    : [...document.querySelectorAll("button,input")].find(
        (el) => el.dataset[token.key] === token.value,
      );
  if (el && !el.disabled) el.focus({ preventScroll: true });
}
function refreshToday() {
  const next = todayKey();
  if (next === today) return false;
  today = next;
  if (drawer.open && !drawerEditing) updateDrawer();
  return true;
}
function syncToday() {
  if (document.visibilityState !== "hidden" && refreshToday()) {
    if (ui.page === "tasks") render();
    else {
      // Keep the import form and any unsaved review draft intact.
      const label = document.querySelector(".main-date");
      if (label) {
        label.dateTime = today;
        label.textContent = dateLabel();
      }
    }
  }
  clearTimeout(dayTimer);
  const now = new Date();
  dayTimer = setTimeout(syncToday,
    new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1) - now);
}
function render() {
  calendarDrag.cancel();
  refreshToday();
  hideSidebarTooltip();
  imports.setVisible(ui.page === "import" && !demo && !loading);
  const focus = document.activeElement,
    token = focusToken(focus),
    focusId = focus?.id,
    start = focus?.selectionStart,
    end = focus?.selectionEnd;
  app.innerHTML = `<div class="workspace ${ui.sidebarCollapsed ? "sidebar-collapsed" : ""} ${ui.mobileMenu ? "menu-open" : ""}">${sidebar()}<button type="button" class="sidebar-scrim" data-action="sidebar-toggle" aria-label="关闭侧栏" tabindex="-1"></button><main id="main" ${ui.mobileMenu ? "inert" : ""}><div class="page-frame"><div id="sync-alert">${syncAlert()}</div>${loading ? `${workspaceHeading({ title: "全部任务" })}<div class="loading-state" role="status">正在读取任务…</div>` : ui.page === "import" ? `<section class="import-page">${imports.render()}</section>` : tasksPage()}</div></main></div>`;
  if (!focusId) restoreFocus(token);
  if (focusId) {
    const next = document.getElementById(focusId);
    if (next) {
      next.focus({ preventScroll: true });
      if (typeof start === "number" && next.setSelectionRange)
        try {
          next.setSelectionRange(start, end);
        } catch {}
    }
  }
  syncTaskControls();
  if (environment && ui.page === "tasks" && (!ui.sidebarCollapsed || matchMedia("(max-width: 700px)").matches))
    document.querySelector(".account-entry").title = environment.detail;
  pruneErrorFeedback();
}
function renderResults() {
  calendarDrag.cancel();
  if (refreshToday()) return render();
  const dayFocus = document.activeElement?.dataset.calendarDay;
  const results = document.querySelector("#task-results");
  if (!results) return;
  results.innerHTML = taskResults();
  syncTaskControls();
  document.querySelector("#result-count").textContent = headingCount();
  if (dayFocus)
    document
      .querySelector(`[data-calendar-day="${dayFocus}"]`)
      ?.focus({ preventScroll: true });
}
function navigatePage(page, scope = ui.scope) {
  const fromMobileMenu = ui.mobileMenu;
  ui.page = page;
  ui.scope = scope;
  ui.mobileMenu = false;
  syncNavigation();
  render();
  window.scrollTo(0, 0);
  if (fromMobileMenu) document.getElementById("page-title")?.focus({ preventScroll: true });
}
function requireAccount() {
  if (demo || session.user) return true;
  account.click({ dataset: { accountAction: "open" } });
  return false;
}
function openTask(id, editing = false) {
  hideSidebarTooltip();
  if (!requireAccount()) return;
  lastFocus = document.activeElement;
  drawerId = id;
  drawerEditing = editing;
  drawerError = null;
  updateDrawer();
  if (!drawer.open) drawer.showModal();
}
function updateDrawer() {
  const task = editableTask(drawerId) || {
    title: "",
    due: "",
    dueTime: "",
    course: "",
    notes: "",
  };
  drawer.innerHTML = renderTaskDrawer({
    task,
    editing: drawerEditing,
    escapeHtml: esc,
    icon,
    deadlineLabel,
  });
  const actionError = drawer.querySelector("#task-action-error");
  if (actionError) actionError.innerHTML = drawerError ? userErrorMarkup(drawerError.error, drawerError.action) : "";
  if (!drawerEditing && task.id) {
    const evidence = drawer.querySelector("#task-evidence");
    if (task.reviewCandidateId) evidence.innerHTML = imports.reviewEvidence(task.reviewCandidateId);
    else if (demo)
      evidence.innerHTML = `<p>${esc(task.notes || "这项任务由手动添加。")}</p>`;
    else void loadTaskEvidence(task.id, evidence, api);
  }
  if (taskBusy(drawerId))
    drawer.querySelectorAll("button,input,textarea,select").forEach(el => {
      if (!["drawer-close", "drawer-cancel"].includes(el.dataset.action)) el.disabled = true;
    });
}
function closeDrawer() {
  drawer.close();
  drawerId = null;
  drawerEditing = false;
  drawerError = null;
  const fallback = document.querySelector('[data-action="task-new"]');
  if (lastFocus?.isConnected) lastFocus.focus({ preventScroll: true });
  else fallback?.focus({ preventScroll: true });
}
const taskDate = (task) => ({ due: task.due || "", dueTime: task.dueTime || "" });
async function saveTaskDeadline(id, next, expected) {
  const epoch = accountVersion();
  if (demo) {
    const task = state.tasks.find((item) => item.id === id);
    if (!task || task.due !== expected.due || (task.dueTime || "") !== expected.dueTime)
      throw new Error("任务日期已变化，请重新核对");
    Object.assign(task, next);
  } else {
    const saved = await setStoredTaskDeadline(id, next, expected);
    if (epoch !== accountVersion()) throw new Error("账号已切换");
    const current = state.tasks.find(task => task.id === id);
    const updated = saved.tasks.find(task => task.id === id);
    if (current && updated) Object.assign(current, taskDate(updated));
  }
}
async function dropTaskOnDate(id, date) {
  const task = draggableTask(id);
  if (!task || !canChangeTask(id) || !shiftDay(date, 0) || task.due === date) return;
  const epoch = accountVersion(), previous = taskDate(task), applied = { due: date, dueTime: previous.dueTime };
  const verb = shiftDay(previous.due, 0) ? "更改" : "设置";
  const dateLabel = isNotice(task) ? "关键日期" : "截止日期";
  const origin = { anchor: ui.anchor, selectedDate: ui.selectedDate, mode: ui.mode, pane: ui.pane };
  busyTasks.add(id);
  render();
  toast(`正在${verb}${dateLabel}…`);
  try {
    await saveTaskDeadline(id, applied, previous);
    if (epoch !== accountVersion()) return;
    if (ui.page === "tasks" && taskView() === "calendar" && ui.mode === origin.mode && ui.pane === origin.pane &&
        ui.anchor === origin.anchor && ui.selectedDate === origin.selectedDate) {
      ui.selectedDate = date;
      syncNavigation("replace");
    }
    toast(`${dateLabel}已${verb === "更改" ? "改为" : "设为"} ${date}${applied.dueTime ? ` ${applied.dueTime}` : ""}`, async () => {
      if (!canChangeTask(id)) throw new Error("正在保存或需要处理保存错误，请稍候再撤销");
      if (drawerEditing && drawer.open) throw new Error("请先关闭任务编辑再撤销");
      const current = state.tasks.find((item) => item.id === id);
      if (!current || current.due !== applied.due || (current.dueTime || "") !== applied.dueTime)
        throw new Error("任务日期已变化，无法撤销这次操作");
      busyTasks.add(id);
      render();
      try {
        await saveTaskDeadline(id, previous, applied);
      } finally {
        finishTask(id, epoch, true);
      }
    });
  } catch (error) {
    if (epoch === accountVersion()) toast(`未${verb}${dateLabel}：${error.message}，${session.conflict ? "请先重新读取任务" : "可重新拖动重试"}`);
  } finally {
    finishTask(id, epoch, true);
  }
}
async function toggleTask(id) {
  if (!canChangeTask(id) || !requireAccount()) return;
  const epoch = accountVersion(), task = editableTask(id);
  if (!task) return;
  if (task.reviewCandidateId) {
    reviewTaskBusy = true;
    busyTasks.add(id);
    drawerError = null;
    render();
    if (drawer.open && drawerId === id) updateDrawer();
    try {
      const saved = await imports.confirmTask(task.reviewCandidateId, task, true);
      if (epoch !== accountVersion()) return;
      if (drawerId === id && saved) drawerId = saved.id;
      toast(isNotice(task) ? '通知已标记为已阅读' : '任务已完成', saved ? () => toggleTask(saved.id) : undefined);
    } catch (error) {
      if (epoch !== accountVersion()) return;
      toastError(error, 'tasks_save');
    } finally {
      if (epoch === accountVersion()) reviewTaskBusy = false;
      finishTask(id, epoch, true);
      if (epoch === accountVersion() && drawer.open && drawerId !== id && !drawerEditing) updateDrawer();
    }
    return;
  }
  drawerError = null;
  const previous = { status: task.status, completedAt: task.completedAt || "" };
  const next = { status: task.status === "done" ? "todo" : "done", completedAt: task.status === "done" ? "" : todayKey() };
  await saveStatus(id, next, previous);
}
async function saveStatus(id, next, expected) {
  if (!canChangeTask(id)) throw new Error("请等待当前任务保存，或先处理保存错误");
  const epoch = accountVersion(), task = state.tasks.find(task => task.id === id);
  if (!task || task.status !== expected.status || (task.completedAt || "") !== expected.completedAt)
    throw new Error("任务状态已变化，无法撤销这次操作");
  const notice = isNotice(task);
  busyTasks.add(id);
  const saving = demo ? Promise.resolve() : setStoredTaskStatus(id, next, expected);
  if (!await stageTaskSave(saving, id, epoch)) return;
  Object.assign(task, next);
  render();
  if (drawer.open && drawerId === id && !drawerEditing) updateDrawer();
  try {
    await saving;
    if (epoch !== accountVersion()) return;
    toast(notice
      ? next.status === "done" ? "通知已标记为已阅读" : "通知已标记为未阅读"
      : next.status === "done" ? "任务已完成" : "任务已重新打开",
    () => saveStatus(id, expected, next));
  } catch (error) {
    if (epoch === accountVersion() && session.sync !== "error") {
      Object.assign(task, expected);
      render();
      toastError(error, "tasks_save");
    }
  } finally {
    finishTask(id, epoch);
  }
}
async function trashTask() {
  const id = drawerId;
  if (!id || !canChangeTask(id)) return;
  const task = state.tasks.find(task => task.id === id);
  if (!task) return;
  const epoch = accountVersion();
  busyTasks.add(id);
  const saving = demo ? Promise.resolve() : trashStoredTasks([id]);
  if (!await stageTaskSave(saving, id, epoch)) return;
  state.tasks = state.tasks.filter(task => task.id !== id);
  state.trash.push(task);
  closeDrawer();
  render();
  try {
    await saving;
    if (epoch !== accountVersion()) return;
    toast("已移入回收站", () => restoreTask(id));
  } catch (error) {
    if (epoch === accountVersion() && session.sync !== "error") {
      state.trash = state.trash.filter(item => item.id !== id);
      state.tasks.push(task);
      render();
      toastError(error, "tasks_delete");
    }
  } finally {
    finishTask(id, epoch);
  }
}
async function restoreTask(id) {
  if (!canChangeTask(id)) throw new Error("请等待当前任务保存，或先处理保存错误");
  const task = state.trash.find(task => task.id === id);
  if (!task) return;
  const epoch = accountVersion();
  busyTasks.add(id);
  const saving = demo ? Promise.resolve() : restoreStoredTasks([id]);
  if (!await stageTaskSave(saving, id, epoch)) return;
  state.trash = state.trash.filter(task => task.id !== id);
  const { recycleBin, ...restored } = task;
  state.tasks.push(restored);
  render();
  // Refresh only the original trash dialog, never reopen one the user closed.
  const trashDialog = dialog.querySelector(".trash-list");
  if (dialog.open && trashDialog) actions["open-trash"]();
  const restoreDialog = dialog.open ? dialog.querySelector(".trash-list") : null;
  try {
    await saving;
    if (epoch === accountVersion()) toast("任务已恢复");
  } catch (error) {
    if (epoch === accountVersion()) {
      if (session.sync !== "error") {
        state.tasks = state.tasks.filter(item => item.id !== id);
        state.trash.push(task);
        render();
      }
      if (session.sync !== "error" || restoreDialog && dialog.open && dialog.querySelector(".trash-list") === restoreDialog)
        toastError(error, "tasks_restore");
    }
  } finally {
    finishTask(id, epoch);
  }
}
function showMobileDay() {
  if (matchMedia("(max-width: 700px)").matches)
    document.querySelector(".day-panel")?.scrollIntoView({
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
      block: "start",
    });
}
function calendarStep(direction) {
  const d = new Date(ui.anchor + "T12:00:00");
  if (ui.mode === "week") {
    ui.anchor = shiftDay(ui.anchor, direction * 7);
    ui.selectedDate = shiftDay(ui.selectedDate, direction * 7);
  } else {
    d.setDate(1);
    d.setMonth(d.getMonth() + direction);
    ui.anchor = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
    ui.selectedDate = ui.anchor;
  }
  ui.pane = "day";
  syncNavigation();
  renderResults();
}
const actions = {
  "error-dismiss": clearUndo,
  "sidebar-toggle": () => {
    const mobile = matchMedia("(max-width: 700px)").matches;
    if (mobile)
      ui.mobileMenu = !ui.mobileMenu;
    else {
      ui.sidebarCollapsed = !ui.sidebarCollapsed;
      remember();
    }
    updateSidebarState();
    document.querySelector(mobile && !ui.mobileMenu ? "#sidebar-show" : "#sidebar-toggle").focus({ preventScroll: true });
  },
  "task-new": () => openTask(null, true),
  "drawer-close": closeDrawer,
  "drawer-edit": () => {
    if (!canChangeTask(drawerId)) return;
    drawerEditing = true;
    updateDrawer();
    drawer.querySelector('[name="title"]').focus();
  },
  "drawer-cancel": () => {
    if (drawerId) {
      drawerEditing = false;
      updateDrawer();
    } else closeDrawer();
  },
  "drawer-trash": trashTask,
  "filter-toggle": () => {
    ui.filterOpen = !ui.filterOpen;
    render();
  },
  "filter-reset": () => {
    ui.query = "";
    ui.course = "";
    ui.source = "";
    ui.dateFrom = "";
    ui.dateTo = "";
    render();
  },
  "calendar-prev": () => calendarStep(-1),
  "calendar-next": () => calendarStep(1),
  "calendar-today": () => {
    ui.anchor = todayKey();
    ui.selectedDate = ui.anchor;
    ui.pane = "day";
    syncNavigation();
    renderResults();
  },
  "dialog-close": closeDialog,
  "retry-save": async () => {
    try {
      await retrySave();
      await refresh();
      toast("已保存");
    } catch (e) {
      toastError(e);
    }
  },
  reconnect: () => actions.reload(false),
  reload: async (discard = true) => {
    loading = true;
    loadError = "";
    render();
    try {
      state = await (discard ? reloadStore() : reconnectStore());
    } catch (e) {
      loadError = e;
    } finally {
      loading = false;
      render();
    }
  },
  "open-trash": () => {
    openDialog(
      `<header class="dialog-header"><h2 id="dialog-heading">回收站</h2><button type="button" class="icon-button" data-action="dialog-close" aria-label="关闭回收站">${icon("close")}</button></header><div class="trash-list">${state.trash.length ? state.trash.map((t) => `<div><span>${esc(t.title)}</span><button type="button" class="btn quiet" data-restore-task="${esc(t.id)}" ${taskBusy(t.id) ? "disabled" : ""}>恢复</button></div>`).join("") : '<p class="empty-state">回收站为空</p>'}</div>`,
    );
  },
};
document.addEventListener("pointerover", (event) => {
  const button = event.target.closest?.("#sidebar [data-sidebar-label]");
  if (!button || button.contains(event.relatedTarget)) return;
  hideSidebarTooltip();
  if (!hasSidebarTooltip(button)) return;
  if (button.matches(":focus-visible")) showSidebarTooltip(button);
  else sidebarTooltipTimer = setTimeout(() => showSidebarTooltip(button), 500);
});
document.addEventListener("pointerout", (event) => {
  const button = event.target.closest?.("#sidebar [data-sidebar-label]");
  if (button && !button.contains(event.relatedTarget)) hideSidebarTooltip();
});
document.addEventListener("focusin", (event) => {
  const button = event.target.closest?.("#sidebar [data-sidebar-label]");
  if (button?.matches(":focus-visible") && hasSidebarTooltip(button)) {
    hideSidebarTooltip();
    showSidebarTooltip(button);
  }
});
document.addEventListener("focusout", hideSidebarTooltip);
document.addEventListener("pointerdown", () => {
  hideSidebarTooltip();
  imports.cancelRecoveryNavigation();
});
window.addEventListener("wheel", () => imports.cancelRecoveryNavigation(), { passive: true });
window.addEventListener("scroll", hideSidebarTooltip, true);
window.addEventListener("resize", hideSidebarTooltip);
document.addEventListener("click", async (e) => {
  imports.cancelRecoveryNavigation();
  const button = e.target.closest("button");
  if (!button) return;
  if (button.dataset.errorFeedback) {
    await submitErrorFeedback(button, {
      send: body => api("/api/error-reports", body, "POST", { timeoutMs: 10000 }),
      frontendRevision: import.meta.env?.VITE_BUILD_REVISION,
      backendRevision,
    });
    return;
  }
  hideSidebarTooltip();
  try {
    if (button.dataset.action && actions[button.dataset.action]) {
      await actions[button.dataset.action]();
      return;
    }
    if (button.dataset.taskEvidence) {
      updateDrawer();
      return;
    }
    if (button.dataset.taskOpen) {
      openTask(button.dataset.taskOpen);
      return;
    }
    if (button.dataset.taskComplete) {
      await toggleTask(button.dataset.taskComplete);
      return;
    }
    if (button.dataset.restoreTask) {
      await restoreTask(button.dataset.restoreTask);
      return;
    }
    if (button.dataset.scope) {
      navigatePage("tasks", button.dataset.scope);
      return;
    }
    if (button.dataset.page) {
      navigatePage(button.dataset.page);
      if (ui.page === "import") void imports.load?.();
      return;
    }
    if (button.dataset.view) {
      if (ui.scope !== "all") return;
      ui.view = button.dataset.view;
      remember();
      syncNavigation();
      render();
      return;
    }
    if (button.dataset.calendarMode) {
      ui.mode = button.dataset.calendarMode;
      if (ui.mode === "week") ui.anchor = ui.selectedDate;
      remember();
      syncNavigation();
      renderResults();
      return;
    }
    if (button.dataset.calendarDay) {
      ui.selectedDate = button.dataset.calendarDay;
      ui.pane = "day";
      syncNavigation();
      renderResults();
      showMobileDay();
      return;
    }
    if (button.dataset.calendarPane) {
      ui.pane = button.dataset.calendarPane;
      syncNavigation();
      renderResults();
      showMobileDay();
      return;
    }
    if (button.dataset.accountAction) {
      await account.click(button);
      return;
    }
    await imports.click(button);
    if (button.dataset.importIgnore && drawer.open && drawerId === `review:${button.dataset.importIgnore}` &&
        !imports.getReviewItems().some(item => item.id === button.dataset.importIgnore)) closeDrawer();
  } catch (error) {
    toastError(error, button.dataset.restoreTask ? "tasks_restore" : "general");
  }
});
document.addEventListener("input", (e) => {
  imports.cancelRecoveryNavigation();
  if (e.target.id === "task-search") {
    ui.query = e.target.value;
    renderResults();
    return;
  }
  if (account.input?.(e)) return;
  imports.input?.(e);
});
document.addEventListener("change", (e) => {
  imports.cancelRecoveryNavigation();
  if (account.change?.(e)) return;
  if (e.target.dataset.noticeRead) {
    void toggleTask(e.target.dataset.noticeRead);
    return;
  }
  if (imports.input?.(e)) return;
  const id = e.target.id,
    value = e.target.value;
  if (id === "course-filter") ui.course = value;
  else if (id === "source-filter") ui.source = value;
  else if (id === "date-from") ui.dateFrom = value;
  else if (id === "date-to") ui.dateTo = value;
  else if (id === "calendar-jump") {
    if (value) {
      ui.anchor = value;
      ui.selectedDate = value;
      ui.pane = "day";
      syncNavigation();
    }
  } else return;
  render();
});
document.addEventListener("toggle", (event) => {
  void imports.toggle(event);
}, true);
document.addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  if (form.id === "task-edit-form") {
    if (!canChangeTask(form.dataset.id)) return;
    const values = Object.fromEntries(new FormData(form)),
      error = form.querySelector("#task-edit-error");
    if (!values.title.trim()) {
      error.textContent = "请输入任务名称";
      return;
    }
    if (values.dueTime && !values.due) {
      error.textContent = "请先填写截止日期";
      return;
    }
    const existing = editableTask(form.dataset.id);
    if (form.dataset.id) {
      try {
        assertCurrentTaskEdit(existing, form.dataset.taskEditSnapshot);
      } catch (cause) {
        error.textContent = cause.message;
        return;
      }
    }
    const epoch = accountVersion();
    if (existing?.reviewCandidateId) {
      const edited = { ...existing, ...values, title: values.title.trim(), course: values.course.trim() };
      if (taskEditSnapshot(edited) === taskEditSnapshot(existing)) {
        drawerEditing = false;
        updateDrawer();
        toast('没有修改，仍保留待核对状态');
        return;
      }
      reviewTaskBusy = true;
      busyTasks.add(existing.id);
      form.querySelectorAll('button,input,textarea,select').forEach(field => { field.disabled = true; });
      render();
      try {
        const saved = await imports.confirmTask(existing.reviewCandidateId, edited, false, Number(form.dataset.reviewVersion));
        if (epoch !== accountVersion()) return;
        if (form.isConnected && drawer.open) closeDrawer();
        toast(isNotice(edited) ? '通知已保存' : '任务已保存');
        if (saved) restoreFocus({ key: 'taskOpen', value: saved.id });
      } catch (cause) {
        if (epoch === accountVersion()) {
          if (form.isConnected) error.innerHTML = userErrorMarkup(cause, 'tasks_save');
          else toastError(cause, 'tasks_save');
        }
      } finally {
        if (epoch === accountVersion()) {
          reviewTaskBusy = false;
          busyTasks.delete(existing.id);
        }
        if (form.isConnected) form.querySelectorAll('button,input,textarea,select').forEach(field => {
          if (!field.dataset.errorFeedback) field.disabled = false;
        });
        render();
      }
      return;
    }
    const draftId = form.dataset.id || newId();
    const task = {
      ...(existing || { id: draftId, status: "todo", source: "手动添加" }),
      ...values, title: values.title.trim(), course: values.course.trim(),
    };
    const expected = existing ? form.dataset.taskEditSnapshot : undefined;
    form.dataset.id = draftId;
    busyTasks.add(draftId);
    const saving = demo ? Promise.resolve() : saveStoredTask(task, expected);
    if (!await stageTaskSave(saving, draftId, epoch)) return;
    if (existing) Object.assign(existing, task);
    else state.tasks.push(task);
    closeDrawer();
    render();
    restoreFocus({ key: "taskOpen", value: draftId });
    try {
      await saving;
      if (epoch === accountVersion()) toast(isNotice(task) ? "通知已保存" : "任务已保存");
    } catch (error) {
      if (epoch === accountVersion() && session.sync !== "error") toastError(error, "tasks_save");
    } finally {
      finishTask(draftId, epoch);
    }
    return;
  }
  try {
    if (await account.submit(form)) return;
    await imports.submit(form);
  } catch (error) {
    toastError(error);
  }
});
document.addEventListener("keydown", (e) => {
  imports.cancelRecoveryNavigation();
  if (e.key === "Escape") hideSidebarTooltip();
  const modalOpen = dialog.open || drawer.open;
  if (e.key === "Tab" && ui.mobileMenu && !modalOpen) {
    const focusable = [
      ...document.querySelectorAll(".sidebar a,.sidebar button"),
    ].filter((el) => !el.disabled && el.getClientRects().length);
    const first = focusable[0],
      last = focusable.at(-1);
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
  if (e.key === "Escape" && ui.mobileMenu && !modalOpen) {
    e.preventDefault();
    ui.mobileMenu = false;
    updateSidebarState();
    document.querySelector("#sidebar-show").focus({ preventScroll: true });
  }
  if (
    (e.metaKey || e.ctrlKey) &&
    e.key === "b" &&
    !modalOpen &&
    !["INPUT", "TEXTAREA"].includes(e.target.tagName)
  ) {
    e.preventDefault();
    actions["sidebar-toggle"]();
  }
});
for (const modal of [dialog, drawer]) {
  let pointerStartedOnBackdrop = false;
  const isBackdrop = (e) => {
    if (e.target !== modal) return false;
    const rect = modal.getBoundingClientRect();
    return (
      e.clientX < rect.left ||
      e.clientX > rect.right ||
      e.clientY < rect.top ||
      e.clientY > rect.bottom
    );
  };
  const resetBackdropPointer = () => {
    pointerStartedOnBackdrop = false;
  };
  // Dragging from inside to outside can also target the modal with a click.
  modal.addEventListener("pointerdown", (e) => {
    pointerStartedOnBackdrop = e.isPrimary && e.button === 0 && isBackdrop(e);
  });
  modal.addEventListener("pointercancel", resetBackdropPointer);
  modal.addEventListener("close", resetBackdropPointer);
  modal.addEventListener("click", (e) => {
    const dismiss = pointerStartedOnBackdrop && isBackdrop(e);
    resetBackdropPointer();
    if (dismiss) modal === drawer ? closeDrawer() : closeDialog();
  });
}
drawer.addEventListener("cancel", (e) => {
  e.preventDefault();
  closeDrawer();
});
window.addEventListener("niuma:account-reset", () => {
  resetErrorFeedback();
  const authForm = dialog.querySelector("[data-account-form]");
  if (
    !(
      session.changing &&
      ["login", "register"].includes(authForm?.dataset.accountForm)
    )
  ) {
    dialog.close();
    dialog.replaceChildren();
  }
  clearUndo();
  state = readStore();
  busyTasks.clear();
  reviewTaskBusy = false;
  imports.reset();
  drawer.close();
  drawerId = null;
  ui.query = "";
  ui.course = "";
  ui.source = "";
  ui.dateFrom = "";
  ui.dateTo = "";
  render();
});
window.addEventListener("popstate", () => {
  Object.assign(ui, readNavigation(new URLSearchParams(location.search), { today: todayKey(), preferences }));
  ui.mobileMenu = false;
  imports.setSource(ui.importSource);
  imports.cancelRecoveryNavigation();
  imports.dismissRecoveryHighlight();
  closeDialog();
  closeDrawer();
  syncNavigation("replace");
  render();
  if (ui.page === "import") void imports.load();
});
window.addEventListener("niuma:sync", () => {
  const label = document.querySelector("#save-status");
  if (label) label.textContent = saveLabel();
  const alert = document.querySelector("#sync-alert");
  if (alert) alert.innerHTML = syncAlert();
  syncTaskControls();
});
matchMedia("(max-width: 700px)").addEventListener("change", () => {
  ui.mobileMenu = false;
  render();
});
document.addEventListener("visibilitychange", syncToday);
window.addEventListener("focus", syncToday);
window.addEventListener("pageshow", syncToday);
syncNavigation("replace");
render();
syncToday();
async function initialize() {
  if (!demo) {
    try {
      state = await initSession();
    } catch (e) {
      loadError = e;
    } finally {
      loading = false;
      render();
      if (ui.page === "import") void imports.load();
      else void imports.loadTaskReviews();
    }
  }
}
void initialize();
api("/api/version")
  .then(async (version) => {
    backendRevision = version.revision;
    environment = describeEnvironment(
      version,
      import.meta.env?.VITE_BUILD_REVISION,
      import.meta.env?.VITE_SOURCE_DIRTY,
    );
    const el = document.querySelector(".account-entry");
    if (el) el.title = environment.detail;
    if (!demo && ["development", "test"].includes(version.environment) && version.accountTools === "/api/environment-tools.js") {
      const module = await import(/* @vite-ignore */ version.accountTools);
      host.accountTools = module.createAccountTools({ ...host, api, session, accountVersion, flushStore,
        reload: () => location.reload() });
    }
  })
  .catch(() => {});
