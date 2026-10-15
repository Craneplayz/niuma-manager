import test from "node:test";
import assert from "node:assert/strict";
import {
  deadlineLabel,
  filterTasks,
  groupTasks,
  shiftDay,
  sortTasks,
  todayKey,
} from "../src/task-model.js";
import { renderCalendar } from "../src/calendar.js";
import { renderTaskDrawer } from "../src/task-drawer.js";
import {
  renderDayPanel,
  renderTaskList,
  renderTaskRow,
} from "../src/task-views.js";

const today = "2026-12-28";
const tasks = [
  {
    id: "undated",
    title: "整理资料",
    status: "todo",
    due: "",
    dueTime: "",
    course: "数据结构",
    source: "文本",
  },
  {
    id: "late",
    title: "补交报告",
    status: "doing",
    due: "2026-12-27",
    dueTime: "23:59",
    course: "数据结构",
    source: "教学网",
  },
  {
    id: "today-late",
    title: "提交实验",
    status: "todo",
    due: today,
    dueTime: "20:00",
    course: "数据结构",
    source: "教学网",
  },
  {
    id: "today-early",
    title: "预习报告",
    status: "todo",
    due: today,
    dueTime: "08:30",
    course: "物理",
    source: "文本",
  },
  {
    id: "tomorrow",
    title: "完成阅读",
    status: "todo",
    due: "2026-12-29",
    dueTime: "",
    course: "英语",
    source: "教学网",
  },
  {
    id: "week",
    title: "提交作业",
    status: "todo",
    due: "2027-01-04",
    dueTime: "",
    course: "物理",
    source: "教学网",
  },
  {
    id: "future",
    title: "课程论文",
    status: "todo",
    due: "2027-01-05",
    dueTime: "",
    course: "英语",
    source: "文本",
  },
  {
    id: "done",
    title: "已完成旧作业",
    status: "done",
    due: "2026-12-01",
    dueTime: "",
    course: "物理",
    source: "教学网",
  },
];
const ids = (list) => list.map((task) => task.id);
const calendarDates = (html) =>
  [
    ...html.matchAll(
      /class="calendar-date-button" data-calendar-day="([^"]+)"/g,
    ),
  ].map((match) => match[1]);

test("task scopes include overdue deadlines and the seventh day, but exclude completed and undated tasks from near", () => {
  assert.deepEqual(ids(filterTasks(tasks, { scope: "near", today })), [
    "late",
    "today-early",
    "today-late",
    "tomorrow",
    "week",
  ]);
  assert.deepEqual(ids(filterTasks(tasks, { scope: "done", today })), ["done"]);
  assert.equal(filterTasks(tasks, { scope: "all", today }).length, 7);
});

test("combined query, course, source, and inclusive date filters preserve deadline order", () => {
  assert.deepEqual(
    ids(
      filterTasks(tasks, {
        query: "报告 数据结构",
        course: "数据结构",
        source: "教学网",
        today,
      }),
    ),
    ["late"],
  );
  assert.deepEqual(
    ids(filterTasks(tasks, { dateFrom: today, dateTo: "2026-12-29", today })),
    ["today-early", "today-late", "tomorrow"],
  );
  assert.deepEqual(
    filterTasks(tasks, { dateFrom: "2027-01-06", dateTo: "2027-01-04", today }),
    [],
  );
  const manual = [
    { id: "manual", title: "整理资料", notes: "隐藏的原文", status: "todo" },
  ];
  assert.deepEqual(ids(filterTasks(manual, { source: "手动添加", today })), [
    "manual",
  ]);
  assert.deepEqual(filterTasks(manual, { query: "隐藏的原文", today }), []);
  const namedAll = [
    { id: "named-all", title: "任务", course: "all", source: "all", status: "todo" },
    ...manual,
  ];
  assert.deepEqual(ids(filterTasks(namedAll, { course: "all", source: "all", today })), ["named-all"]);
});

test("grouping uses all six deadline buckets across a year boundary and sorting never mutates input", () => {
  const originalIds = ids(tasks);
  const grouped = groupTasks(filterTasks(tasks, { today }), today);
  assert.deepEqual(
    grouped.map((group) => group.tone),
    ["overdue", "today", "tomorrow", "upcoming", "future", "undated"],
  );
  assert.deepEqual(ids(grouped[1].tasks), ["today-early", "today-late"]);
  assert.deepEqual(ids(grouped[3].tasks), ["week"]);
  sortTasks(tasks);
  assert.deepEqual(ids(tasks), originalIds);
});

test("date arithmetic handles leap years and year rollover without accepting impossible dates", () => {
  assert.equal(shiftDay("2024-02-28", 1), "2024-02-29");
  assert.equal(shiftDay("2026-12-31", 1), "2027-01-01");
  assert.equal(shiftDay("2027-01-01", -1), "2026-12-31");
  assert.equal(shiftDay("2026-02-30", 1), "");
  assert.match(todayKey(), /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(groupTasks([{ id: "bad", due: "2026-02-30", status: "todo" }], today)[0].tone, "undated");
  assert.equal(deadlineLabel(tasks[2], today), "今天 20:00");
  assert.equal(deadlineLabel(tasks[4], today), "明天");
  assert.equal(deadlineLabel(tasks[5], today), "2027 年 1 月 4 日");
  assert.equal(deadlineLabel(tasks[0], today), "无截止日期");
});

test("calendar is a Monday-first deadline projection that handles leap months and cross-year weeks", () => {
  const month = renderCalendar({ tasks, anchorDate: "2026-10-14", today });
  assert.equal(calendarDates(month)[0], "2026-09-28");
  assert.equal(calendarDates(month).at(-1), "2026-11-01");
  assert.equal(calendarDates(month).length, 35);
  const leap = calendarDates(
    renderCalendar({ anchorDate: "2024-02-14", today }),
  );
  assert.ok(leap.includes("2024-02-29"));
  const week = calendarDates(
    renderCalendar({ anchorDate: "2026-12-31", mode: "week", today }),
  );
  assert.deepEqual(week, [
    "2026-12-28",
    "2026-12-29",
    "2026-12-30",
    "2026-12-31",
    "2027-01-01",
    "2027-01-02",
    "2027-01-03",
  ]);
  assert.doesNotMatch(month, /data-task-open="undated"/);
  for (const mode of ["month", "week"]) {
    const html = renderCalendar({ tasks, anchorDate: today, mode, today });
    assert.doesNotMatch(html, /data-task-open="done"/);
  }
});

test("calendar caps summaries, exposes the remainder, escapes imported task content, and has no nested buttons", () => {
  const crowded = [
    {
      id: 'quoted"id',
      title: "<img src=x>",
      status: "todo",
      due: today,
      dueTime: "08:00",
    },
    {
      id: "two",
      title: "第二项",
      status: "todo",
      due: today,
      dueTime: "09:00",
    },
    {
      id: "three",
      title: "第三项",
      status: "todo",
      due: today,
      dueTime: "10:00",
    },
  ];
  const html = renderCalendar({
    tasks: crowded,
    anchorDate: today,
    selectedDate: today,
    today,
  });
  assert.equal((html.match(/data-task-open=/g) || []).length, 2);
  assert.match(html, /\+1 项/);
  assert.match(html, /&lt;img/);
  assert.match(html, /data-task-open="quoted&quot;id"/);
  assert.doesNotMatch(html, /<img/);
  const stack = [];
  for (const match of html.matchAll(/<\/?button\b[^>]*>/g)) {
    if (match[0].startsWith("</")) assert.equal(stack.pop(), "button");
    else {
      assert.equal(stack.length, 0);
      stack.push("button");
    }
  }
  assert.equal(stack.length, 0);
});

test("task and day renderers use string ids, expose completion, and keep completed work out of overdue sections", () => {
  const malicious = {
    id: 'source"task',
    title: "<script>alert(1)</script>",
    status: "doing",
    due: today,
    dueTime: "",
    course: "<b>课程</b>",
    source: "教学网",
  };
  const row = renderTaskRow(malicious, { today });
  assert.match(row, /data-task-open="source&quot;task"/);
  assert.match(row, /data-task-complete="source&quot;task"/);
  assert.match(row, /&lt;script&gt;/);
  assert.doesNotMatch(row, /<script>|<b>/);
  const doneList = renderTaskList([tasks[7]], { today });
  assert.doesNotMatch(doneList, /已逾期/);
  assert.match(doneList, /aria-pressed="true"/);
  const panel = renderDayPanel({ tasks, selectedDate: today, today });
  assert.match(panel, /data-task-open="today-early"/);
  assert.doesNotMatch(panel, /data-task-open="undated"/);
  const undated = renderDayPanel({
    tasks,
    selectedDate: today,
    pane: "undated",
    today,
  });
  assert.match(undated, /data-task-open="undated"/);
  assert.doesNotMatch(undated, /data-task-open="today-early"/);
});

test("near and completed scopes include notices while all tasks remains task-only", () => {
  const mixed = [
    { id: "undated-task", title: "加群", status: "todo", due: "" },
    { id: "dated-notice", title: "公示结果", content_type: "notice", status: "todo", due: today },
    { id: "old-notice", title: "上课地点", content_type: "notice", status: "done", due: "" },
    { id: "done-task", title: "提交作业", content_type: "task", status: "done", due: today },
  ];
  assert.deepEqual(ids(filterTasks(mixed, { scope: "all", today })), ["undated-task"]);
  assert.deepEqual(ids(filterTasks(mixed, { scope: "near", today })), ["dated-notice"]);
  assert.deepEqual(ids(filterTasks(mixed, { scope: "done", today })), ["done-task", "old-notice"]);
  assert.deepEqual(ids(filterTasks(mixed, { scope: "notices", today })), ["dated-notice", "old-notice"]);
  assert.deepEqual(ids(filterTasks(mixed, { scope: "calendar", today })), ["dated-notice", "old-notice", "undated-task"]);
  assert.deepEqual(ids(filterTasks(mixed, { scope: "notices", query: "公示", dateFrom: today, today })), ["dated-notice"]);
  assert.equal(mixed[2].status, "done");
});

test("notice lists and details expose synchronized reversible read controls without task completion wording", () => {
  const notice = { id: "notice", title: "停课通知", content_type: "notice", status: "done", due: "2026-12-27", notes: "课程暂停" };
  const list = renderTaskList([notice], { today });
  assert.match(list, /notice-label|关键时间/);
  assert.match(list, /data-task-complete="notice"[^>]*role="checkbox"[^>]*aria-checked="true"/);
  assert.match(list, /标记未阅读：停课通知/);
  assert.match(list, /is-read/);
  assert.doesNotMatch(list, /is-completed|is-overdue|已逾期|完成任务/);
  const drawer = renderTaskDrawer({ task: notice, escapeHtml: String, icon: () => "", deadlineLabel });
  assert.match(drawer, /通知详情|关键时间/);
  assert.match(drawer, /type="checkbox"[^>]*data-notice-read="notice"[^>]*checked/);
  assert.match(drawer, /已阅读/);
  assert.doesNotMatch(drawer, /完成任务|未完成/);
  const unread = { ...notice, status: "todo" };
  assert.match(renderTaskRow(unread, { today }), /role="checkbox"[^>]*aria-checked="false"/);
  assert.match(renderTaskRow(unread, { today }), /标记已阅读：停课通知/);
  const unreadDrawer = renderTaskDrawer({ task: unread, escapeHtml: String, icon: () => "", deadlineLabel });
  assert.match(unreadDrawer, /未阅读/);
  assert.doesNotMatch(unreadDrawer, /data-notice-read="notice"[^>]*checked/);
  const editing = renderTaskDrawer({ task: notice, editing: true, escapeHtml: String, icon: () => "", deadlineLabel });
  assert.match(editing, /编辑通知|关键日期|保存通知/);
  assert.doesNotMatch(editing, /截止日期|保存任务/);
});

test("month, week and day panels distinguish dated notices and retain undated notices", () => {
  const mixed = [tasks[2], { id: "notice", title: "考试地点", content_type: "notice", status: "done", due: today }, { id: "undated-notice", title: "上课教室", content_type: "notice", status: "todo", due: "" }];
  for (const mode of ["month", "week"]) {
    const markup = renderCalendar({ tasks: mixed, anchorDate: today, today, mode });
    assert.match(markup, /calendar-task is-notice/);
    assert.match(markup, /通知关键时间：考试地点/);
    assert.match(markup, /任务截止：提交实验/);
    assert.doesNotMatch(markup, /data-task-open="undated-notice"/);
  }
  const panel = renderDayPanel({ tasks: mixed, selectedDate: today, today });
  assert.match(panel, /data-task-complete="today-late"/);
  assert.match(panel, /data-task-complete="notice"[^>]*role="checkbox"[^>]*aria-checked="true"/);
  const undated = renderDayPanel({ tasks: mixed, selectedDate: today, pane: "undated", today });
  assert.match(undated, /data-task-open="undated-notice"/);
  assert.match(undated, /data-task-complete="undated-notice"[^>]*role="checkbox"[^>]*aria-checked="false"/);
});

test("mixed near lists honor notice dates, read status and combined filters without calling notice times overdue", () => {
  const notices = [
    { id: "past-notice", title: "教室调整", content_type: "notice", status: "todo", due: shiftDay(today, -1), course: "物理", source: "教学网" },
    { id: "week-notice", title: "分组公示", content_type: "notice", status: "todo", due: shiftDay(today, 7), course: "物理", source: "教学网" },
    { id: "future-notice", content_type: "notice", status: "todo", due: shiftDay(today, 8) },
    { id: "undated-notice", content_type: "notice", status: "todo", due: "" },
    { id: "invalid-notice", content_type: "notice", status: "todo", due: "2026-02-30" },
    { id: "read-notice", content_type: "notice", status: "done", due: today },
  ];
  const near = filterTasks([...tasks, ...notices], { scope: "near", today });
  assert.deepEqual(ids(near.filter(item => item.content_type === "notice")), ["past-notice", "week-notice"]);
  assert.deepEqual(ids(filterTasks(notices, { scope: "near", query: "公示", course: "物理", source: "教学网", dateFrom: today, dateTo: shiftDay(today, 7), today })), ["week-notice"]);
  const markup = renderTaskList(near, { today });
  assert.match(markup, /早于今天/);
  assert.doesNotMatch(markup, />已逾期</);
  assert.match(markup, /data-task-complete="past-notice"/);
});

test('task edits reject changed or removed records while allowing independent status and metadata updates',async()=>{
 const {taskEditSnapshot,assertCurrentTaskEdit}=await import('../src/task-model.js');
 const original={id:'task',title:'报告',notes:'提交 PDF',course:'概率论',due:'2026-10-20',dueTime:'18:00',status:'todo'};
 const snapshot=taskEditSnapshot(original);
 assert.doesNotThrow(()=>assertCurrentTaskEdit({...original,status:'done',serverMetadata:{updated:true}},snapshot));
 for(const change of [{title:'新标题'},{notes:'新的原文要求'},{course:'新课程'},{due:'2026-10-21'},{dueTime:'20:00'}]){
  assert.throws(()=>assertCurrentTaskEdit({...original,...change},snapshot),/内容已变化/);
 }
 assert.throws(()=>assertCurrentTaskEdit(undefined,snapshot),/内容已变化/);
});
