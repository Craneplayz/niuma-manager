import { shiftDay, todayKey } from "./task-model.js";

// Activity uses recorded business dates verbatim: never infer completion from due dates.
function validDate(value) {
  return typeof value === "string" ? shiftDay(value, 0) : "";
}

function clockOnDay(now, day) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) return null;
  const local = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return local === day ? now : null;
}

function dueTime(task) {
  const time = typeof task.dueTime === "string" ? task.dueTime
    : task.due?.precision === "date" ? "" : task.due?.time;
  return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(time || "") ? time : "";
}

export function calendarActivityOf(task, { today = todayKey(), now = new Date() } = {}) {
  if (!task || typeof task !== "object" || task.content_type === "notice" ||
      task.recycleBin || task.status === "cancelled" || task.reviewCandidateId) return null;
  const day = validDate(today) || todayKey();
  const done = task.status === "done" || task.status === "completed";
  const date = validDate(done ? task.completedAt : typeof task.due === "string" ? task.due : task.due?.date);
  if (!date) return null;
  const clock = clockOnDay(now, day), time = dueTime(task);
  const expiredToday = date === day && time && clock &&
    clock.getHours() * 60 + clock.getMinutes() >= Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
  return { date, kind: done ? "done" : date < day || expiredToday ? "overdue" : "pending" };
}

/** One mutually exclusive entry per confirmed task: actual completion day or current due day. */
export function buildCalendarActivity(tasks = [], { today = todayKey(), now = new Date() } = {}) {
  const baseDay = validDate(today) || todayKey();
  const result = new Map();
  for (const task of Array.isArray(tasks) ? tasks : []) {
    const activity = calendarActivityOf(task, { today: baseDay, now });
    if (!activity) continue;
    if (!result.has(activity.date)) result.set(activity.date, { pending: 0, done: 0, overdue: 0 });
    result.get(activity.date)[activity.kind]++;
  }
  return result;
}

/** Return original records in their original order so day-panel actions retain task identity. */
export function calendarDayTasks(tasks = [], date, { today = todayKey(), now = new Date() } = {}) {
  const selected = validDate(date);
  if (!selected) return [];
  const baseDay = validDate(today) || todayKey();
  return (Array.isArray(tasks) ? tasks : []).filter(task => calendarActivityOf(task, { today: baseDay, now })?.date === selected);
}

/** The next timed deadline that can change today's surface without a page reload. */
export function nextCalendarActivityChange(tasks = [], { today = todayKey(), now = new Date() } = {}) {
  const day = validDate(today), clock = clockOnDay(now, day);
  if (!clock) return null;
  let next = Infinity;
  for (const task of Array.isArray(tasks) ? tasks : []) {
    const activity = calendarActivityOf(task, { today: day, now: clock });
    if (activity?.kind !== "pending" || activity.date !== day) continue;
    const time = dueTime(task);
    if (!time) continue;
    const deadline = new Date(`${day}T${time}:00`);
    if (validDate(day) && deadline.getHours() === Number(time.slice(0, 2)) &&
        deadline.getMinutes() === Number(time.slice(3)) && deadline.getTime() > clock.getTime())
      next = Math.min(next, deadline.getTime());
  }
  return Number.isFinite(next) ? next : null;
}

function countValue(value) {
  return Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
}

/** Absolute density bands stay comparable when navigating between months. */
export function activityLevel(count) {
  const value = countValue(count);
  return value === 0 ? 0 : value === 1 ? 1 : value <= 3 ? 2 : value <= 6 ? 3 : 4;
}

/** Plain text for date labels and tooltips; no markup or task content is interpolated. */
export function activitySummary(counts = {}) {
  const labels = [["pending", "待完成"], ["done", "已完成"], ["overdue", "已逾期"]];
  const parts = labels.flatMap(([key, label]) => {
    const count = countValue(counts?.[key]);
    return count ? [`${label} ${count} 项`] : [];
  });
  return parts.length ? parts.join("，") : "无任务活动";
}
