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
  check("明天早上8点上数算课", normal("上数算课", "2026-11-19", "08:00", "数算"), ["明天早上8点"]);
  check("请在明天的早上8点上课", normal("上课", "2026-11-19", "08:00"), ["明天的早上8点"]);
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
  check("早上8点上数算课 分类：课堂", normal("上数算课", "2026-11-18", "08:00", "课堂"), ["早上8点"]);
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
  check("下周一开会", normal("开会", "2027-01-04"), ["下周一"], { today: "2026-12-31" });
  check("2月29日值班", normal("值班", "2028-02-29"), ["2月29日"], { today: "2028-02-28" });
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
