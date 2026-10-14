const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];
const DAY_MS = 86400000;

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
  anchorDate,
  mode = "month",
  selectedDate,
  today,
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

  for (const task of Array.isArray(tasks) ? tasks : []) {
    if (task.status === "done" || !readDate(task.due)) continue;
    if (!byDate.has(task.due)) byDate.set(task.due, []);
    byDate.get(task.due).push(task);
  }
  for (const members of byDate.values()) {
    members.sort(
      (a, b) =>
        String(a.dueTime || "99:99").localeCompare(
          String(b.dueTime || "99:99"),
        ) ||
        String(a.id).localeCompare(String(b.id), undefined, { numeric: true }),
    );
  }

  function taskButton(task, date) {
    const time = task.dueTime ? String(task.dueTime) : "";
    const label = `${task.title}，${accessibleDate(date)}${time ? ` ${time}` : ""}`;
    return `<button type="button" class="calendar-task" data-task-open="${escapeHtml(task.id)}" aria-label="${escapeHtml(label)}">${time ? `<span class="calendar-task-time">${escapeHtml(time)}</span>` : ""}<span class="calendar-task-title">${escapeHtml(task.title)}</span></button>`;
  }

  function day(date, index) {
    const iso = isoDate(date);
    const members = byDate.get(iso) || [];
    const isToday = iso === todayISO;
    const isSelected = iso === selectedISO;
    const isOtherMonth = !isWeek && date.getUTCMonth() !== anchor.getUTCMonth();
    const classes = [
      "calendar-day",
      isToday && "is-today",
      isSelected && "is-selected",
      isOtherMonth && "is-other-month",
      !members.length && "is-empty",
    ]
      .filter(Boolean)
      .join(" ");
    const label = `${accessibleDate(date)}${isToday ? "，今天" : ""}，${members.length ? `${members.length} 项任务` : "暂无任务"}`;
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
        ? `<button type="button" class="calendar-more" data-calendar-day="${iso}" aria-label="查看 ${accessibleDate(date)}的全部 ${members.length} 项任务">+${members.length - 2} 项</button>`
        : "";
    const empty =
      isWeek && !members.length
        ? '<p class="calendar-day-empty">无任务</p>'
        : "";
    return `<section class="${classes}" role="listitem" aria-label="${escapeHtml(accessibleDate(date))}"><button type="button" class="calendar-date-button" data-calendar-day="${iso}" aria-label="${escapeHtml(label)}" aria-pressed="${isSelected}">${weekday}<span class="calendar-day-number"${isToday ? ' aria-current="date"' : ""}>${date.getUTCDate()}</span>${count}${!isWeek ? mobileCount : ""}</button><div class="calendar-day-tasks">${visible.map((task) => taskButton(task, date)).join("")}${overflow}${empty}</div></section>`;
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
  return `<div class="calendar-view calendar-month" aria-label="${anchor.getUTCFullYear()} 年 ${anchor.getUTCMonth() + 1} 月任务日历"><div class="calendar-weekdays" aria-hidden="true">${WEEKDAYS.map((weekday) => `<span>${weekday}</span>`).join("")}</div><div class="calendar-month-grid" role="list">${Array.from({ length: count }, (_, index) => day(addDays(start, index), index % 7)).join("")}</div></div>`;
}
