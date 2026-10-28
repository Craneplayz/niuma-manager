import { shiftDay, todayKey } from "./task-model.js";

const validDate = value => typeof value === "string" ? shiftDay(value, 0) : "";
const isDone = task => task.status === "done" || task.status === "completed";
const isTask = task => task && typeof task === "object" && task.content_type !== "notice" &&
  task.status !== "cancelled" && !task.recycleBin && !task.reviewCandidateId;

/** Derive one truthful sidebar companion from confirmed, saved task records. */
export function selectSidebarCompanion(tasks = [], options = {}) {
  const { today = todayKey(), ready = true } = options || {};
  const day = validDate(today);
  if (!ready || !day) return {
    key: "unknown", character: "cow", pose: "arrive", ready: false,
    message: "牛牛在这儿，陪你把事情一件件理清楚。",
    completedToday: null, overdue: null, pending: null, taskCount: null,
  };

  const records = (Array.isArray(tasks) ? tasks : []).filter(isTask);
  let completedToday = 0, overdue = 0, pending = 0;
  for (const task of records) {
    if (isDone(task)) {
      if (validDate(task.completedAt) === day) completedToday++;
    } else {
      pending++;
      const due = validDate(typeof task.due === "string" ? task.due : task.due?.date);
      if (due && due < day) overdue++;
    }
  }
  const counts = { completedToday, overdue, pending, taskCount: records.length, ready: true };
  const result = (key, character, pose, message) => ({ key, character, pose, message, ...counts });

  if (overdue >= 3) return result("overdue-high", "horse", "checklist", completedToday
    ? `今天完成 ${completedToday} 项，真不错！还有 ${overdue} 项逾期，咱们收个尾？`
    : `还有 ${overdue} 项逾期呢，不能一直晾着啦，先挑一项？`);
  if (overdue) return result("overdue-low", "horse", "settle", completedToday
    ? `今天完成 ${completedToday} 项啦！还有 ${overdue} 项逾期，要不要先看看？`
    : `还有 ${overdue} 项过了截止日，要不要先看看？`);
  if (completedToday >= 3) return result("progress", "cow", "dance",
    `今天完成 ${completedToday} 项，真棒！跟牛牛跳个小舞吧～`);
  if (completedToday) return result("small-progress", "cow", "celebrate",
    `今天完成 ${completedToday} 项啦，每一步都算数。`);
  if (pending) return result("steady", "cow", "arrive", "不用一口气想完，先挑一件就好。");
  if (records.length) return result("all-clear", "cow", "wave", "这会儿歇一歇，想安排下一步时叫我。");
  return result("idle", "cow", "settle", "清单慢慢安排，牛牛先陪你待一会儿。");
}
