import {
  deadlineLabel,
  groupTasks,
  isNotice,
  shiftDay,
  sortTasks,
  todayKey,
} from "./task-model.js";

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
    dateDraggable = false,
  } = {},
) {
  const notice = isNotice(task);
  const done = task.status === "done";
  const overdue = !done && Boolean(shiftDay(task.due, 0)) && task.due < today;
  const meta = [...new Set([task.course, task.source].filter(Boolean))];
  if (!notice && task.status === "doing") meta.push("进行中");
  const due = compact
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
    ? ` data-calendar-drag-task="${escapeHtml(task.id)}" title="点击查看${notice ? "通知" : "任务"}，拖到日历日期${shiftDay(task.due, 0) ? "更改" : "设置"}${notice ? "关键日期" : "截止日期"}"` : "";
  const marker = `<button type="button" class="task-complete" data-task-complete="${escapeHtml(task.id)}" aria-label="${action}${escapeHtml(task.title)}" ${notice ? `role="checkbox" aria-checked="${done}"` : `aria-pressed="${done}"`}><span class="task-complete-mark">${done ? icon("check") : ""}</span></button>`;
  return `<article class="task-row${notice ? " is-notice" : ""}${compact ? " is-compact" : ""}${done ? notice ? " is-read" : " is-completed" : ""}${overdue ? " is-overdue" : ""}" data-task-id="${escapeHtml(task.id)}">${marker}<button type="button" class="task-open" data-task-open="${escapeHtml(task.id)}"${drag}><span class="task-title">${notice ? '<span class="notice-label">通知</span>' : ""}${escapeHtml(task.title)}</span>${meta.length ? `<span class="task-meta">${meta.map((value) => `<span>${escapeHtml(value)}</span>`).join("")}</span>` : ""}</button>${due ? `<span class="task-deadline">${escapeHtml(due)}</span>` : ""}</article>`;
}

export function renderTaskList(
  tasks,
  { escapeHtml = escape, icon = noIcon, today = todayKey(), emptyLabel = "没有符合条件的任务" } = {},
) {
  if (!tasks.length) return `<p class="task-empty">${escapeHtml(emptyLabel)}</p>`;
  const options = { escapeHtml, icon, today };
  if (tasks.every(isNotice) || tasks.every((task) => task.status === "done"))
    return `<div class="task-list">${sortTasks(tasks)
      .map((task) => renderTaskRow(task, options))
      .join("")}</div>`;
  const labels = tasks.some(isNotice)
    ? { overdue: "早于今天", today: "今天", tomorrow: "明天", upcoming: "未来 7 天", future: "之后", undated: "无日期" }
    : null;
  return `<div class="task-list">${groupTasks(tasks, today)
    .map(
      (group) =>
        `<section class="task-group"><h2 class="task-group-heading">${labels?.[group.tone] || group.label}<span>${group.tasks.length}</span></h2>${group.tasks.map((task) => renderTaskRow(task, options)).join("")}</section>`,
    )
    .join("")}</div>`;
}

export function renderDayPanel({
  tasks = [],
  selectedDate,
  pane = "day",
  allowDateDrag = false,
  escapeHtml = escape,
  icon = noIcon,
  today = todayKey(),
} = {}) {
  const isUndated = pane === "undated";
  const date = shiftDay(selectedDate, 0) || today;
  const undated = tasks.filter((task) => !shiftDay(task.due, 0));
  const dated = tasks.filter((task) => task.due === date);
  const visible = sortTasks(isUndated ? undated : dated);
  const hasNotices = tasks.some(isNotice);
  const kind = hasNotices ? "内容" : "任务";
  const [year, month, day] = date.split("-");
  const dateText = `${year === today.slice(0, 4) ? "" : `${year} 年 `}${Number(month)} 月 ${Number(day)} 日`;
  const weekday = ["日", "一", "二", "三", "四", "五", "六"][
    new Date(`${date}T00:00:00Z`).getUTCDay()
  ];
  const draggable = task => allowDateDrag && task.status !== "done";
  const hint = visible.some(draggable)
    ? `<p class="calendar-drag-hint">按住${hasNotices ? "任务或通知" : "任务"}拖到日历日期，${isUndated ? "设置" : "更改"}${hasNotices ? "日期" : "截止日期"}</p>` : "";
  return `<aside class="day-panel" aria-label="${isUndated ? `无日期${kind}` : `${escapeHtml(dateText)}${hasNotices ? "任务与通知" : "截止任务"}`}"><div class="calendar-pane-switch" role="group" aria-label="日历内容范围"><button type="button" data-calendar-pane="day" class="${isUndated ? "" : "is-selected"}" aria-pressed="${!isUndated}">当日${kind}${dated.length ? `<span>${dated.length}</span>` : ""}</button><button type="button" data-calendar-pane="undated" class="${isUndated ? "is-selected" : ""}" aria-pressed="${isUndated}">无日期${undated.length ? `<span>${undated.length}</span>` : ""}</button></div><header class="day-panel-heading"><h2>${isUndated ? hasNotices ? "无日期内容" : "无截止日期" : escapeHtml(dateText)}</h2>${isUndated ? "" : `<span>${date === today ? "今天 · " : ""}星期${weekday}</span>`}</header>${hint}<div class="day-panel-tasks">${visible.length ? visible.map((task) => renderTaskRow(task, { escapeHtml, icon, today, compact: true, dateDraggable: draggable(task) })).join("") : `<p class="task-empty">${isUndated ? `没有无日期${kind}` : `当天没有${hasNotices ? "任务与通知" : "截止任务"}`}</p>`}</div></aside>`;
}
