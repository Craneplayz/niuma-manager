import { shiftDay, todayKey } from "./task-model.js";

// Activity uses recorded business dates verbatim: never infer completion from due dates.
function validDate(value) {
  return typeof value === "string" ? shiftDay(value, 0) : "";
}

function activityOf(task, today) {
  if (!task || typeof task !== "object" || task.content_type === "notice" ||
      task.recycleBin || task.status === "cancelled" || task.reviewCandidateId) return null;
  const done = task.status === "done" || task.status === "completed";
  const date = validDate(done ? task.completedAt : typeof task.due === "string" ? task.due : task.due?.date);
  if (!date) return null;
  return { date, kind: done ? "done" : date < today ? "overdue" : "pending" };
}

/** One mutually exclusive entry per confirmed task: actual completion day or current due day. */
export function buildCalendarActivity(tasks = [], { today = todayKey() } = {}) {
  const baseDay = validDate(today) || todayKey();
  const result = new Map();
  for (const task of Array.isArray(tasks) ? tasks : []) {
    const activity = activityOf(task, baseDay);
    if (!activity) continue;
    if (!result.has(activity.date)) result.set(activity.date, { pending: 0, done: 0, overdue: 0 });
    result.get(activity.date)[activity.kind]++;
  }
  return result;
}

/** Return original records in their original order so day-panel actions retain task identity. */
export function calendarDayTasks(tasks = [], date, { today = todayKey() } = {}) {
  const selected = validDate(date);
  if (!selected) return [];
  const baseDay = validDate(today) || todayKey();
  return (Array.isArray(tasks) ? tasks : []).filter(task => activityOf(task, baseDay)?.date === selected);
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
