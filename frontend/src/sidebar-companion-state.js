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
    message: "我在这儿呢。",
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
    ? `今天做完 ${completedToday} 项了。还有 ${overdue} 项逾期，先挑一件？`
    : `还有 ${overdue} 项逾期，先挑一件做吧。`);
  if (overdue) return result("overdue-low", "horse", "settle", completedToday
    ? `今天做完 ${completedToday} 项了。还有 ${overdue} 项逾期，记得看看。`
    : `有 ${overdue} 项逾期了，记得看一眼。`);
  if (completedToday >= 3) return result("progress", "cow", "dance",
    `今天做完 ${completedToday} 项啦，跟我跳两下？`);
  if (completedToday) return result("small-progress", "cow", "celebrate",
    `今天做完 ${completedToday} 项啦，挺好。`);
  if (pending) return result("steady", "cow", "arrive", "先看看要做哪件。");
  if (records.length) return result("all-clear", "cow", "wave", "现在没待办，歇会儿吧。");
  return result("idle", "cow", "settle", "有要做的事，记在这儿吧。");
}
