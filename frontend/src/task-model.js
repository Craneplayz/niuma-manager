const DAY_MS = 86_400_000;

export const isNotice = (task) => task.content_type === "notice";

function readDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value || ""))) return null;
  const date = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(date.getTime()) &&
    date.toISOString().slice(0, 10) === value
    ? date
    : null;
}

function dueOf(task) {
  return readDate(task.due) ? task.due : "";
}

function timeOf(task) {
  return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(String(task.dueTime || ""))
    ? task.dueTime
    : "";
}

export function todayKey() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
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
    const aDue = dueOf(a),
      bDue = dueOf(b);
    if (aDue !== bDue) return !aDue ? 1 : !bDue ? -1 : aDue.localeCompare(bDue);
    return (
      (timeOf(a) || "99:99").localeCompare(timeOf(b) || "99:99") ||
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
      if (notice || (scope === "done" ? !done : done)) return false;
    }
    if (scope === "near" && (!due || due > nearEnd)) return false;
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

export function groupTasks(tasks, today = todayKey()) {
  const day = readDate(today) ? today : todayKey();
  const tomorrow = shiftDay(day, 1),
    nearEnd = shiftDay(day, 7);
  const groups = [
    { label: "已逾期", tone: "overdue", tasks: [] },
    { label: "今天截止", tone: "today", tasks: [] },
    { label: "明天截止", tone: "tomorrow", tasks: [] },
    { label: "未来 7 天", tone: "upcoming", tasks: [] },
    { label: "之后截止", tone: "future", tasks: [] },
    { label: "无截止日期", tone: "undated", tasks: [] },
  ];
  for (const task of sortTasks(tasks)) {
    const due = dueOf(task);
    const index = !due
      ? 5
      : due < day
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
  if (!due) return "无截止日期";
  const day = readDate(today) ? today : todayKey();
  const [year, month, date] = due.split("-");
  const label =
    due === day
      ? "今天"
      : due === shiftDay(day, 1)
        ? "明天"
        : `${year === day.slice(0, 4) ? "" : `${year} 年 `}${Number(month)} 月 ${Number(date)} 日`;
  return `${label}${timeOf(task) ? ` ${timeOf(task)}` : ""}`;
}
