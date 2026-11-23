import test from "node:test";
import assert from "node:assert/strict";
import { analyzeQuickTask, parseQuickTask } from "../src/quick-task.js";

const options = { today: "2026-11-18", courses: ["数算", "大学英语", "计算机", "计算机系统", "ICS"] };
const normal = (title, due = "", dueTime = "", course = "") => ({ title, due, dueTime, course });

function check(input, task, tokens, overrides) {
  const context = { ...options, ...overrides };
  const analysis = analyzeQuickTask(input, context);
  assert.deepEqual(analysis.task, task);
  assert.deepEqual(parseQuickTask(input, context), task);
  assert.deepEqual(analysis.spans.map(({ start, end }) => input.slice(start, end)), tokens);
  for (let index = 0; index < analysis.spans.length; index++) {
    const { start, end } = analysis.spans[index];
    assert.ok(start >= (analysis.spans[index - 1]?.end ?? 0) && start < end && end <= input.length);
  }
}

test("a typed task and its highlighted time agree, including the screenshot example", () => {
  check("明天早上8点上数算课", { ...normal("上数算课", "2026-11-19", "08:00", "数算"), timeKind: "event" }, ["明天早上8点"]);
  check("请在明天的早上8点上课", { ...normal("上课", "2026-11-19", "08:00"), timeKind: "event" }, ["明天的早上8点"]);
  check("明晚8点前交实验报告", normal("交实验报告", "2026-11-19", "20:00"), ["明晚8点"]);
  check("11-20下午三点半还书", normal("还书", "2026-11-20", "15:30"), ["11-20下午三点半"]);
  check("下周三晚八点交第1-3题", normal("交第1-3题", "2026-11-25", "20:00"), ["下周三晚八点"]);
  check("ICS 报告，截止到明天", normal("ICS 报告", "2026-11-19", "", "ICS"), ["明天"]);
});

test("positions point into the unmodified input regardless of whitespace, emoji, tags and full-width digits", () => {
  const input = "  #明天 📎  周五 整理文档 晚上８：０５ ";
  check(input, normal("📎 整理文档", "2026-11-20", "20:05", "明天"), ["周五", "晚上８：０５"]);
  assert.equal(analyzeQuickTask(input, options).spans[0].start, input.indexOf("周五"));
  check("＃大学英语 明天检查作业", normal("检查作业", "2026-11-19", "", "大学英语"), ["明天"]);
  check("早上8点上数算课 分类：课堂", { ...normal("上数算课", "2026-11-18", "08:00", "课堂"), timeKind: "event" }, ["早上8点"]);
});

test("a date-only task stays date-only, a time respects the selected day, explicit dates override it", () => {
  const selected = { defaultDate: "2026-12-03", defaultCourse: "物理" };
  check("写报告", normal("写报告", "2026-12-03", "", "物理"), [], selected);
  check("下午三点还书", normal("还书", "2026-12-03", "15:00", "物理"), ["下午三点"], selected);
  check("明天还书", normal("还书", "2026-11-19", "", "物理"), ["明天"], selected);
  check("今晚交报告", normal("今晚交报告", "2026-11-18"), ["今晚"]);
  check("明天下午写报告", normal("下午写报告", "2026-11-19"), ["明天"]);
  check("明天早上8点", normal("", "2026-11-19", "08:00"), ["明天早上8点"]);
});

test("calendar arithmetic handles week boundaries, leap years and an explicitly requested past day", () => {
  check("下周一开会", { ...normal("开会", "2027-01-04"), timeKind: "event" }, ["下周一"], { today: "2026-12-31" });
  check("2月29日值班", { ...normal("值班", "2028-02-29"), timeKind: "event" }, ["2月29日"], { today: "2028-02-28" });
  check("2025-11-17 09:00 整理笔记", normal("整理笔记", "2025-11-17", "09:00"), ["2025-11-17", "09:00"]);
  check("2026.11.20T17:00提交报告", normal("提交报告", "2026-11-20", "17:00"), ["2026.11.20T17:00"]);
  check("2月30日交论文", normal("2月30日交论文"), []);
});

test("ranges, multiple possibilities, negations and invalid clocks stay in the original wording", () => {
  for (const input of ["明天或后天交报告", "周三到五交报告", "下午三到五点来办公室", "周五上午8点到9点练习",
    "不是明天交作业", "明天不用交实验", "下周三之后再交论文", "明天25:10还书", "今天中午12:89开会"]) {
    check(input, normal(input), [], { defaultDate: "2026-12-03" });
  }
  check("明天整理三点建议", normal("整理三点建议", "2026-11-19"), ["明天"]);
  check("有一点时间整理资料", normal("有一点时间整理资料"), []);
  check("八点一起来上课", normal("一起来上课", "2026-11-18", "08:00"), ["八点"]);
});

test("known course detection keeps the user's title intact and avoids accidental substring matches", () => {
  check("复习计算机系统", normal("复习计算机系统", "", "", "计算机系统"), []);
  check("数算和大学英语作业", normal("数算和大学英语作业"), []);
  check("复习basics", normal("复习basics"), []);
});

test("attendance notices produce a reusable title, venue and event time without a model", () => {
  for (const [source, title, place] of [
    ["请各位同学于明天早晨10点前往百周年纪念讲堂观看学院周年汇演", "观看学院周年汇演", "百周年纪念讲堂"],
    ["明天上午10点到理教 208 参加迎新说明会", "参加迎新说明会", "理教 208"],
    ["请在图书馆参加读书会", "参加读书会", "图书馆"],
  ]) {
    const { task, localOnly } = analyzeQuickTask(source, options);
    assert.equal(localOnly, true);
    assert.equal(task.title, title);
    assert.equal(task.notes, "地点：" + place);
    assert.equal(task.timeKind, "event");
    if (source.includes("10点")) assert.equal(task.dueTime, "10:00");
  }
  const delivery = analyzeQuickTask("请各位同学于明天早晨10点前提交报名表", options);
  assert.equal(delivery.task.title, "提交报名表");
  assert.equal(delivery.task.timeKind, undefined);
  assert.equal(delivery.localOnly, true);
  for (const source of ["明天或后天去讲堂参加彩排", "请明天10点前往讲堂观看演出，然后提交反馈"]) {
    const parsed = analyzeQuickTask(source, options);
    assert.ok(!parsed.localOnly);
    assert.equal(parsed.task.due, "");
  }
});

test("structured posters keep a time range and do not replace an explicit historical date", () => {
  const text = "【校园读书会】译者见面会，开放报名！\\n活动时间：11 月 12 日（周四）14:00–17:30\\n活动地点：西馆 201\\n我已报名";
  const options = { today: "2026-11-22" };
  const { task, localOnly } = analyzeQuickTask(text, options);
  assert.equal(localOnly, true);
  assert.equal(task.due, "2026-11-12");
  assert.equal(task.dueTime, "14:00");
  assert.equal(task.title, "参加【校园读书会】译者见面会");
  assert.match(task.notes, /西馆 201/);
  assert.match(task.notes, /14:00–17:30/);
  for (const ambiguous of [text.replace("周四", "周一"), text.replace("–17:30", " 或 17:30"), text.replace("我已报名", "我取消报名")])
    assert.ok(!analyzeQuickTask(ambiguous, options).localOnly);
});

test("a rescheduled lecture, a prose invitation and a conditional service keep separate roles", () => {
  const context = { today: "2026-11-22" };
  const course = parseQuickTask("这周四（11.19）研讨课在原教室照常上课。原计划是安排交流会，因场地原因，改到12.3周四。", context);
  assert.equal(course.due, "2026-12-03"); assert.match(course.notes, /2026-11-19 研讨课在原教室/);
  assert.equal(course.title, "交流会");
  const invite = parseQuickTask("新生交流会，11月18日（周三）19:30将于教学楼520举行。欢迎大家扫码入群，群内用于签到。", context);
  assert.equal(invite.title, "新生交流会"); assert.match(invite.notes, /教学楼520/); assert.doesNotMatch(invite.notes, /已报名/);
  const service = parseQuickTask("转发服务中心通知：符合条件的同学可免费领取材料，其他同学自费。领取时间：每周三下午均可。领取地点：一楼大厅。请携带校园卡。", context);
  assert.equal(service.content_type, "notice"); assert.equal(service.due, "");
  for (const fact of ["自费", "每周三下午", "一楼大厅", "校园卡"]) assert.ok(service.notes.includes(fact));
});

test("a spoken exam reminder keeps its course and half hour without the request to record it", () => {
  const parsed = analyzeQuickTask("明天上午10点半有数算考试，你帮我记一下", { today: "2026-11-22" });
  assert.equal(parsed.localOnly, true);
  assert.deepEqual(parsed.task, { title: "数算考试", course: "数算", due: "2026-11-23", dueTime: "10:30", timeKind: "event" });
  assert.equal(parseQuickTask("明天上午10点半有一场期末考试，帮我记录一下", options).course, "");
});

test("labelled exam uses the assessment date and keeps instructions and end time", () => {
  const source = [
    "各位同学：","现将《医学专业英语》课程期末考核相关安排通知如下，请大家知悉并按时参加。",
    "考核时间：2026 年 12 月 10 日（星期四） 14:00–15:30","考核地点：理教 204 教室",
    "考核形式：闭卷笔试","课程名称：医学专业英语","注意事项：",
    "① 请携带学生证，提前 15 分钟到场；迟到超过 30 分钟不得入场。",
    "② 禁止携带手机、资料、草稿纸等，按考场要求存放个人物品。",
    "③ 答题使用黑色签字笔，遵守考场纪律，诚信应考。","任课教师","2026 年 11 月 23 日",
  ];
  for (const separator of ["\n", "\\\n"]) {
    const { task, localOnly } = analyzeQuickTask(source.join(separator), {today:"2026-11-22", defaultCourse:"其他课程"});
    assert.equal(localOnly, true);
    assert.deepEqual([task.title, task.course, task.due, task.dueTime, task.timeKind],
      ["医学专业英语期末考核", "医学专业英语", "2026-12-10", "14:00", "event"]);
    for (const fact of ["14:00–15:30", "理教 204 教室", "闭卷笔试", ...source.slice(7,10)]) assert.ok(task.notes.includes(fact));
  }
});

test("an exam announcement with separate deliverables takes the multi-item route", () => {
  const text = "《离散数学》期末考试。\n考试时间：2027年1月8日 09:00–11:00\n作业一：提交证明题。\n作业二：提交实验报告。";
  const { localOnly } = analyzeQuickTask(text, { today: "2026-12-26" });
  assert.ok(!localOnly);
});

test("typed and pasted assignments use the same course identity", () => {
  const course = "数据结构与算法 (A)(26-27学年第1学期)";
  const { task } = analyzeQuickTask("明天上午10点半有数算考试", {today: "2026-11-22", courses: [course]});
  assert.equal(task.course, course);
  assert.equal(task.dueTime, "10:30");
});
