import {
  deadlineLabel,
  groupTasks,
  isNotice,
  isTaskOverdue,
  shiftDay,
  sortTasks,
  todayKey,
} from "./task-model.js";
import { renderAgendaWeek } from "./calendar.js";
import { isBulkSelectable, renderBulkCheckbox } from './task-bulk-ui.js';
import { renderCourseBadge } from "./course-identity.js";
import { buildCalendarActivity, calendarDayTasks, activitySummary } from "./calendar-activity.js";

const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const noIcon = () => "";

export function renderTaskRow(
  task,
  {
    escapeHtml = escape,
    icon = noIcon,
    today = todayKey(),
    compact = false,
    hideDeadline = false,
    now = new Date(),
    dateDraggable = false,
    bulk = null,
  } = {},
) {
  const title = escapeHtml(task.title);
  const notice = isNotice(task);
  const done = task.status === "done";
  const overdue = notice
    ? !done && Boolean(shiftDay(task.due, 0)) && task.due < today
    : isTaskOverdue(task, today, now);
  const meta = [task.source].filter(value => value && value !== task.course);
  if (!notice && task.status === "doing") meta.push("进行中");
  if (task.quickRecognition?.status === "pending") meta.push("AI 识别中");
  const due = hideDeadline ? "" : compact
    ? /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(task.dueTime || "")
      ? task.dueTime
      : ""
    : notice
      ? task.due ? `关键时间：${deadlineLabel(task, today)}` : "未注明时间"
      : deadlineLabel(task, today);
  const action = notice
    ? done ? "标记未阅读：" : "标记已阅读："
    : done ? "恢复任务：" : "完成任务：";
  const drag = dateDraggable
    ? ` data-calendar-drag-task="${escapeHtml(task.id)}" title="点击查看${notice ? "通知" : "任务"}，拖到日历日期${shiftDay(task.due, 0) ? "更改" : "设置"}${notice ? "关键日期" : task.timeKind === "event" ? "活动日期" : "截止日期"}"` : "";
  const marker = bulk?.active && isBulkSelectable(task)
    ? renderBulkCheckbox(task, { selected: bulk.selected, escapeHtml, disabled: bulk.busy || bulk.isBusy?.(task.id) })
    : `<button type="button" class="task-complete" data-task-complete="${escapeHtml(task.id)}" aria-label="${action}${title}" ${notice ? `role="checkbox" aria-checked="${done}"` : `aria-pressed="${done}"`}><span class="task-complete-mark">${done ? icon("check") : ""}</span></button>`;
  return `<article class="task-row${notice ? " is-notice" : ""}${compact ? " is-compact" : ""}${done ? notice ? " is-read" : " is-completed" : ""}${overdue ? " is-overdue" : ""}${bulk?.selected.has(task.id) ? ' is-bulk-selected' : ''}" data-task-id="${escapeHtml(task.id)}">${marker}<button type="button" class="task-open" data-task-open="${escapeHtml(task.id)}"${drag}><span class="task-title">${notice ? '<span class="notice-label">通知</span>' : ""}${title}</span><span class="task-meta">${renderCourseBadge(task.course)}${meta.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}</span></button>${due ? `<span class="task-deadline">${escapeHtml(due)}</span>` : ""}</article>`;
}

export function renderTaskList(
  tasks,
  { escapeHtml = escape, icon = noIcon, today = todayKey(), now = new Date(), emptyLabel = "没有符合条件的任务", bulk = null } = {},
) {
  if (!tasks.length) return `<p class="task-empty">${escapeHtml(emptyLabel)}</p>`;
  const options = { escapeHtml, icon, today, now, bulk };
  if (tasks.every(isNotice) || tasks.every((task) => task.status === "done"))
    return `<div class="task-list">${sortTasks(tasks)
      .map((task) => renderTaskRow(task, options))
      .join("")}</div>`;
  const labels = tasks.some(isNotice)
    ? { overdue: tasks.some(task => task.due === today && isTaskOverdue(task, today, now)) ? "时间已过" : "早于今天",
        today: "今天", tomorrow: "明天", upcoming: "未来 7 天", future: "之后", undated: "无日期" }
    : null;
  return `<div class="task-list">${groupTasks(tasks, today, now)
    .map(
      (group) =>
        `<section class="task-group"><h2 class="task-group-heading">${labels?.[group.tone] || group.label}<span>${group.tasks.length}</span></h2>${group.tasks.map((task) => renderTaskRow(task, options)).join("")}</section>`,
    )
    .join("")}</div>`;
}

export function renderDayPanel({
  tasks = [],
  activityTasks = tasks,
  selectedDate,
  classesMarkup = '',
  pane = "day",
  allowDateDrag = false,
  escapeHtml = escape,
  icon = noIcon,
  today = todayKey(),
  now = new Date(),
} = {}) {
  const isUndated = pane === "undated";
  const date = shiftDay(selectedDate, 0) || today;
  const dueTasks = tasks.filter(task => isNotice(task) || !["done", "completed"].includes(task.status));
  const undated = dueTasks.filter((task) => !shiftDay(task.due, 0));
  const dated = dueTasks.filter((task) => task.due === date);
  const visible = sortTasks(isUndated ? undated : dated);
  const completed = calendarDayTasks(activityTasks, date, { today, now })
    .filter(task => ["done", "completed"].includes(task.status));
  const counts = buildCalendarActivity(activityTasks, { today, now }).get(date) || { pending: 0, done: 0, overdue: 0 };
  const activity = isUndated ? "" : activitySummary(counts);
  const hasNotices = tasks.some(isNotice);
  const kind = hasNotices ? "内容" : "任务";
  const [year, month, day] = date.split("-");
  const dateText = `${year === today.slice(0, 4) ? "" : `${year} 年 `}${Number(month)} 月 ${Number(day)} 日`;
  const weekday = ["日", "一", "二", "三", "四", "五", "六"][
    new Date(`${date}T00:00:00Z`).getUTCDay()
  ];
  const draggable = task => allowDateDrag && !["done", "completed"].includes(task.status);
  const hint = visible.some(draggable)
    ? `<p class="calendar-drag-hint">按住${hasNotices ? "任务或通知" : "任务"}拖到日历日期，${isUndated ? "设置" : "更改"}${hasNotices || visible.some(task => task.timeKind === "event") ? "日期" : "截止日期"}</p>` : "";
  const dueRows = visible.length
    ? visible.map((task) => renderTaskRow(task, { escapeHtml, icon, today, now, compact: true, dateDraggable: draggable(task) })).join("")
    : `<p class="task-empty">${isUndated ? `没有无日期${kind}` : `当天没有${hasNotices ? "任务与通知" : "截止任务"}`}</p>`;
  const dayHeading = visible.some(task => task.timeKind === "event") ? "当日安排" : "截止与通知";
  const weekNavigation = isUndated ? "" : renderAgendaWeek(date, today, icon);
  const addToDay = isUndated ? "" : `<button type="button" id="agenda-add-task" class="btn agenda-add-task" data-action="task-new" data-quick-date="${date}">${icon("plus")}添加到这天</button>`;
  return `<aside class="day-panel" aria-label="${isUndated ? `无日期${kind}` : `${escapeHtml(dateText)}任务活动与日程`}">${weekNavigation}<div class="calendar-pane-switch" role="group" aria-label="日历内容范围"><button type="button" data-calendar-pane="day" class="${isUndated ? "" : "is-selected"}" aria-pressed="${!isUndated}">当日${kind}${dated.length + completed.length ? `<span>${dated.length + completed.length}</span>` : ""}</button><button type="button" data-calendar-pane="undated" class="${isUndated ? "is-selected" : ""}" aria-pressed="${isUndated}">无日期${undated.length ? `<span>${undated.length}</span>` : ""}</button></div><header class="day-panel-heading"><h2 aria-live="polite">${isUndated ? hasNotices ? "无日期内容" : "无截止日期" : escapeHtml(dateText)}</h2>${isUndated ? "" : `<span>${date === today ? "今天 · " : ""}星期${weekday}</span>`}${addToDay}</header>${classesMarkup}${isUndated ? "" : `<p class="day-panel-activity">${escapeHtml(activity)}</p>`}${hint}<div class="day-panel-tasks">${isUndated ? dueRows : `<section class="day-panel-due" aria-label="${dayHeading}"><h3>${dayHeading}<span>${visible.length}</span></h3>${dueRows}</section><section class="day-panel-completed" aria-label="这天完成"><h3>这天完成<span>${completed.length}</span></h3>${completed.length ? completed.map(task => renderTaskRow({ ...task, status: "done" }, { escapeHtml, icon, today, compact: true, hideDeadline: true })).join("") : '<p class="task-empty">这天还没有完成记录</p>'}</section>`}</div></aside>`;
}
