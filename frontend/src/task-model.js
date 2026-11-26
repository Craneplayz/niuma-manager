const DAY_MS = 86_400_000;

export const isNotice = (task) => task.content_type === "notice";

export const taskEditSnapshot = (task) => JSON.stringify([
  task.title || "", task.course || "", task.due || "", task.dueTime || "", task.notes || "", task.content_type || "task",
]);

export function assertCurrentTaskEdit(task, snapshot) {
  if (!task || taskEditSnapshot(task) !== snapshot)
    throw new Error("任务内容已变化，请取消编辑后重新打开；当前草稿仍保留。");
}

function readDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return null;
  const date = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(date.getTime()) &&
    date.toISOString().slice(0, 10) === value
    ? date
    : null;
}

function dueOf(task) {
  const date = typeof task.due === "string" ? task.due : task.due?.date;
  return readDate(date) ? date : "";
}

export function taskDueTime(task) {
  const time = typeof task.dueTime === "string" ? task.dueTime
    : task.due?.precision === "date" ? "" : task.due?.time;
  return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(time || "") ? time : "";
}

export function todayKey(now = new Date()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

export function isTaskOverdue(task, today, now) {
  if (task.timeKind === "event" || isNotice(task) || task.reviewCandidateId || task.recycleBin ||
      ["done", "completed", "cancelled"].includes(task.status)) return false;
  const date = dueOf(task);
  if (!date || date !== today) return Boolean(date && date < today);
  const time = taskDueTime(task);
  if (!time || !(now instanceof Date) || todayKey(now) !== today) return false;
  return now.getHours() * 60 + now.getMinutes() >= Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
}

export function shiftDay(date, days) {
  const parsed = readDate(date);
  if (!parsed || !Number.isFinite(days)) return "";
  return new Date(parsed.getTime() + Math.trunc(days) * DAY_MS)
    .toISOString()
    .slice(0, 10);
}

export function sortTasks(tasks) {
  return [...(Array.isArray(tasks) ? tasks : [])].sort((a, b) => {
    const statusOrder = Number(a.status === "done") - Number(b.status === "done");
    if (statusOrder) return statusOrder;
    const aDue = dueOf(a),
      bDue = dueOf(b);
    if (aDue !== bDue) return !aDue ? 1 : !bDue ? -1 : aDue.localeCompare(bDue);
    return (
      (taskDueTime(a) || "99:99").localeCompare(taskDueTime(b) || "99:99") ||
      String(a.id ?? "").localeCompare(String(b.id ?? ""), undefined, {
        numeric: true,
      })
    );
  });
}

export function filterTasks(
  tasks,
  {
    scope = "all",
    query = "",
    course = "",
    source = "",
    dateFrom = "",
    dateTo = "",
    today = todayKey(),
  } = {},
) {
  const baseDay = readDate(today) ? today : todayKey();
  const nearEnd = shiftDay(baseDay, 7);
  const from = readDate(dateFrom) ? dateFrom : "";
  const to = readDate(dateTo) ? dateTo : "";
  const words = String(query)
    .trim()
    .toLocaleLowerCase()
    .split(/\s+/)
    .filter(Boolean);
  const result = (Array.isArray(tasks) ? tasks : []).filter((task) => {
    const notice = isNotice(task);
    const done = task.status === "done";
    const due = dueOf(task);
    if (scope === "notices") {
      if (!notice) return false;
    } else if (scope === "calendar") {
      if (!notice && done) return false;
    } else {
      if ((scope === "all" && notice && !(task.timeKind === "event" && due)) || (scope === "done" ? !done : done)) return false;
    }
    if (scope === "near" && (task.timeKind === "event" || !due || due > nearEnd)) return false;
    if (course && task.course !== course) return false;
    if (source && (task.source || "手动添加") !== source)
      return false;
    if ((from && (!due || due < from)) || (to && (!due || due > to)))
      return false;
    if (words.length) {
      const haystack = [task.title, task.course, task.source || "手动添加"]
        .filter(Boolean)
        .join(" ")
        .toLocaleLowerCase();
      if (!words.every((word) => haystack.includes(word))) return false;
    }
    return true;
  });
  return sortTasks(result);
}

export function groupTasks(tasks, today = todayKey(), now = new Date()) {
  const day = readDate(today) ? today : todayKey();
  const tomorrow = shiftDay(day, 1),
    nearEnd = shiftDay(day, 7);
  const groups = [
    { label: "已逾期", tone: "overdue", tasks: [] },
    { label: "今天截止", tone: "today", tasks: [] },
    { label: "明天截止", tone: "tomorrow", tasks: [] },
    { label: "未来 7 天", tone: "upcoming", tasks: [] },
    { label: "之后截止", tone: "future", tasks: [] },
    { label: "活动安排", tone: "events", tasks: [] },
    { label: "无截止日期", tone: "undated", tasks: [] },
  ];
  for (const task of sortTasks(tasks)) {
    const due = dueOf(task);
    const index = task.timeKind === "event" ? 5 : !due
      ? 6
      : due < day || isTaskOverdue(task, day, now)
        ? 0
        : due === day
          ? 1
          : due === tomorrow
            ? 2
            : due <= nearEnd
              ? 3
              : 4;
    groups[index].tasks.push(task);
  }
  return groups.filter((group) => group.tasks.length);
}

export function deadlineLabel(task, today = todayKey()) {
  const due = dueOf(task);
  if (!due) return task.timeKind === "event" ? "未定活动时间" : "无截止日期";
  const day = readDate(today) ? today : todayKey();
  const [year, month, date] = due.split("-");
  const label =
    due === day
      ? "今天"
      : due === shiftDay(day, 1)
        ? "明天"
        : `${year === day.slice(0, 4) ? "" : `${year} 年 `}${Number(month)} 月 ${Number(date)} 日`;
  const time = taskDueTime(task);
  return `${label}${time ? ` ${time}` : ""}`;
}
