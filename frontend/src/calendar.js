import { isNotice, sortTasks } from "./task-model.js";
import { courseIdentity, renderCourseBadge, renderCourseDot } from "./course-identity.js";
import { buildCalendarActivity, activitySummary } from "./calendar-activity.js";
import { renderCalendarSurface } from "./calendar-surface.js";

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];
const DAY_MS = 86400000;
const ACTIVITY_KINDS = [["pending", "待完成"], ["done", "已完成"], ["overdue", "已逾期"]];

export function renderCalendarActivityLegend() {
  return `<div class="calendar-activity-legend" aria-label="任务活动图例"><div class="calendar-activity-keys">${ACTIVITY_KINDS.map(([kind, label]) => `<span class="calendar-activity-key"><span class="calendar-activity-cell" data-activity="${kind}" data-level="3" aria-hidden="true"></span>${label}</span>`).join("")}</div><p>颜色越深，数量越多<span>完成按完成日，其余按截止日；今天有时刻的任务按时刻判断</span></p></div>`;
}

function readDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return null;
  const date = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(date.getTime()) &&
    date.toISOString().slice(0, 10) === value
    ? date
    : null;
}

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function addDays(date, count) {
  return new Date(date.getTime() + count * DAY_MS);
}

function mondayOf(date) {
  return addDays(date, -((date.getUTCDay() + 6) % 7));
}

function accessibleDate(date) {
  return `${date.getUTCFullYear()} 年 ${date.getUTCMonth() + 1} 月 ${date.getUTCDate()} 日，星期${WEEKDAYS[(date.getUTCDay() + 6) % 7]}`;
}

function fallbackEscape(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
}

/**
 * Render dated tasks. The caller owns filtering, navigation, and the selected-day panel.
 * Handle data-calendar-day to select a date and data-task-open to open a task.
 */
export function renderCalendar({
  tasks = [],
  activityTasks = tasks,
  anchorDate,
  mode = "month",
  selectedDate,
  activityPriority = "all",
  today,
  now = new Date(),
  allowDateDrag = false,
  escapeHtml = fallbackEscape,
} = {}) {
  const anchor =
    readDate(anchorDate) ||
    readDate(today) ||
    readDate(new Date().toISOString().slice(0, 10));
  const selectedISO = readDate(selectedDate) ? selectedDate : isoDate(anchor);
  const todayISO = readDate(today) ? today : "";
  const isWeek = mode === "week";
  const byDate = new Map();
  const activity = buildCalendarActivity(activityTasks, { today: todayISO || isoDate(anchor), now });

  for (const task of Array.isArray(tasks) ? tasks : []) {
    if ((!isNotice(task) && task.status === "done") || !readDate(task.due)) continue;
    if (!byDate.has(task.due)) byDate.set(task.due, []);
    byDate.get(task.due).push(task);
  }
  for (const [date, members] of byDate) byDate.set(date, sortTasks(members));

  function taskButton(task, date) {
    const time = task.dueTime ? String(task.dueTime) : "";
    const notice = isNotice(task);
    const course = courseIdentity(task.course);
    const label = `${notice ? "通知关键时间：" : "任务截止："}${task.title}，课程：${course.label || "未分类"}，${accessibleDate(date)}${time ? ` ${time}` : ""}`;
    const drag = allowDateDrag && task.status !== "done"
      ? ` data-calendar-drag-task="${escapeHtml(task.id)}" title="点击查看${notice ? "通知" : "任务"}，拖到其他日期更改${notice ? "关键日期" : "截止日期"}"` : "";
    return `<button type="button" class="calendar-task${notice ? " is-notice" : ""}" data-task-open="${escapeHtml(task.id)}"${drag} aria-label="${escapeHtml(label)}"><span class="calendar-task-context">${notice ? '<span class="notice-label">通知</span>' : ""}${time ? `<span class="calendar-task-time">${escapeHtml(time)}</span>` : ""}${renderCourseBadge(task.course, { compact: true })}</span><span class="calendar-task-title">${escapeHtml(task.title)}</span></button>`;
  }

  function day(date, index) {
    const iso = isoDate(date);
    const members = byDate.get(iso) || [];
    const counts = activity.get(iso) || { pending: 0, done: 0, overdue: 0 };
    const activityText = activitySummary(counts);
    const isToday = iso === todayISO;
    const isSelected = iso === selectedISO;
    const isOtherMonth = !isWeek && date.getUTCMonth() !== anchor.getUTCMonth();
    const classes = [
      "calendar-day",
      isToday && "is-today",
      isSelected && "is-selected",
      isOtherMonth && "is-other-month",
    ]
      .filter(Boolean)
      .join(" ");
    const unit = members.some(isNotice) ? "条内容" : "项任务";
    const dayCourses = [...new Map(members.map(task => [courseIdentity(task.course).key, task.course])).values()];
    const courseNames = dayCourses.map(course => courseIdentity(course).label || "未分类");
    const label = `${accessibleDate(date)}${isToday ? "，今天" : ""}，${members.length ? `${members.length} ${unit}` : "无截止内容"}，${activityText}${courseNames.length ? `，课程：${courseNames.join("、")}` : ""}`;
    const surface = renderCalendarSurface({ date: iso, counts, mode: isWeek ? "week" : "month", selectedKind: isSelected ? activityPriority : "", escapeHtml });
    const courseDots = !isWeek && dayCourses.length
      ? `<span class="calendar-day-course-dots" aria-hidden="true">${dayCourses.slice(0, 3).map(renderCourseDot).join("")}</span>`
      : "";
    const weekday = isWeek
      ? `<span class="calendar-day-weekday">星期${WEEKDAYS[index]}</span>`
      : "";
    const mobileCount = members.length
      ? `<span class="calendar-day-mobile-count">${members.length} 项</span>`
      : '<span class="calendar-day-mobile-count calendar-day-mobile-empty" aria-hidden="true">·</span>';
    const count =
      isWeek && members.length
        ? `<span class="calendar-day-count">${members.length} 项</span>`
        : "";
    const visible = isWeek ? members : members.slice(0, 2);
    const overflow =
      !isWeek && members.length > 2
        ? `<button type="button" class="calendar-more" data-calendar-day="${iso}" aria-label="查看 ${accessibleDate(date)}的全部 ${members.length} ${unit}">+${members.length - 2} 项</button>`
        : "";
    const empty =
      isWeek && !members.length
        ? '<p class="calendar-day-empty">无截止内容</p>'
        : "";
    return `<section class="${classes}" data-calendar-drop-date="${iso}" role="listitem" aria-label="${escapeHtml(accessibleDate(date))}">${surface}<div class="calendar-day-heading">${weekday}<button type="button" class="calendar-date-button" data-calendar-day="${iso}" aria-label="${escapeHtml(label)}" title="${escapeHtml(activityText)}" aria-pressed="${isSelected}"><span class="calendar-day-number"${isToday ? ' aria-current="date"' : ""}>${date.getUTCDate()}</span></button>${count}${!isWeek ? mobileCount : ""}${courseDots}</div><div class="calendar-day-tasks">${visible.map((task) => taskButton(task, date)).join("")}${overflow}${empty}</div></section>`;
  }

  if (isWeek) {
    const start = mondayOf(anchor);
    const end = addDays(start, 6);
    return `<div class="calendar-view calendar-week" role="list" aria-label="${start.getUTCFullYear()} 年 ${start.getUTCMonth() + 1} 月 ${start.getUTCDate()} 日至 ${end.getUTCFullYear()} 年 ${end.getUTCMonth() + 1} 月 ${end.getUTCDate()} 日，截止日程">${Array.from({ length: 7 }, (_, index) => day(addDays(start, index), index)).join("")}</div>`;
  }

  const first = new Date(
    Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth(), 1),
  );
  const last = new Date(
    Date.UTC(anchor.getUTCFullYear(), anchor.getUTCMonth() + 1, 0),
  );
  const start = mondayOf(first);
  const count =
    Math.ceil(((last.getTime() - start.getTime()) / DAY_MS + 1) / 7) * 7;
  return `<div class="calendar-view calendar-month" style="--calendar-weeks: ${count / 7}" aria-label="${anchor.getUTCFullYear()} 年 ${anchor.getUTCMonth() + 1} 月任务日历"><div class="calendar-weekdays" aria-hidden="true">${WEEKDAYS.map((weekday) => `<span>${weekday}</span>`).join("")}</div><div class="calendar-month-grid" role="list">${Array.from({ length: count }, (_, index) => day(addDays(start, index), index % 7)).join("")}</div></div>`;
}
