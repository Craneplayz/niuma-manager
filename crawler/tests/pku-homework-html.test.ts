import { DateTime } from "luxon";
import { describe, expect, it } from "vitest";
import { buildHomeworkScrapeResult, extractHomeworkRecordsFromHtml } from "../src/pku-homework.js";

const context = { course: "Synthetic course", section: "课程作业", sourceUrl: "https://example.test/content", asOf: DateTime.fromISO("2026-09-25T12:00:00+08:00") };
// Synthetic Blackboard-shaped fixtures; no private course HTML is checked in.
const item = (id: string, title: string, body = "", href = "") => `<li id="${id}"><div class="item"><h3>${href ? `<a href="${href}">${title}</a>` : title}</h3></div><div class="details">${body}</div></li>`;
const page = (items: string) => `<nav>课程作业<h3>口语训练</h3>答疑讨论</nav><h1>课程作业</h1><div>内容</div><ul id="content_listContainer">${items}</ul>`;
const assignment = "/webapps/assignment/uploadAssignment?content_id=test&mode=view";

describe("Blackboard homework item extraction", () => {
  it("uses the real Lab title and keeps prose and attachments inside one record", () => {
    const records = extractHomeworkRecordsFromHtml(page(item("lab1", "Lab 1 [2026/9/15 - 2026/10/19]", `<p>本次作业需要一份报告。</p><p>有关本次作业的要求详见附件。</p><p>提交要求：将代码和报告打包，本次 lab 截止时间为 2026 年 10 月 19 日 23:59.</p><ul class="attachments"><li><a href="/file.pdf">Lab1作业.pdf (100 KB)</a></li></ul>`, assignment)), context);
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({ title: "Lab 1 [2026/9/15 - 2026/10/19]", deadline: "2026-10-19T23:59:00+08:00", source_item_id: "lab1", source_url: "https://example.test/content#lab1" });
    expect(records[0].description).toContain("Lab1作业.pdf");
    expect(records[0].description).toContain("有关本次作业的要求详见附件。");
  });
  it("recognizes native assignments without homework keywords", () => {
    const records = extractHomeworkRecordsFromHtml(page(item("zero", "Lab 0", "本次作业无需提交任何文件。", assignment) + item("one", "1. 集合", "上第一章课后作业4，5，16", assignment)), context);
    expect(records.map((record) => record.title)).toEqual(["Lab 0", "1. 集合"]);
    expect(records.every((record) => record.deadline === null)).toBe(true);
  });
  it("ignores navigation, folders and unrelated lecture materials", () => {
    const html = page(item("folder", "课程作业", "", "/webapps/blackboard/content/listContent.jsp?content_id=folder") + item("slides", "第一讲课件", "报告分数说明"));
    expect(extractHomeworkRecordsFromHtml(html, context)).toEqual([]);
    expect(extractHomeworkRecordsFromHtml("<body>课程作业\n口语训练\n作业一</body>", context)).toEqual([]);
  });
  it("never splits chapter homework prose into extra tasks", () => {
    const records = extractHomeworkRecordsFromHtml(page(item("ch2", "第二章作业", "<p>上第二章课后作业10，16，26</p>", assignment) + item("ch3", "第三章作业", "<p>第3章课后作业5，12，14</p>", assignment)), context);
    expect(records.map((record) => record.title)).toEqual(["第二章作业", "第三章作业"]);
    expect(records[0].description).not.toContain("第三章");
  });
  it("keeps homework attachments within their heading even without an assignment link", () => {
    const records = extractHomeworkRecordsFromHtml(page(item("hw", "小班研讨题和作业题-第2讲", '<ul><li><a href="/file.pdf">小班研讨题和作业题-第2讲.pdf (100 KB)</a></li></ul>')), context);
    expect(records).toHaveLength(1);
    expect(records[0].title).toBe("小班研讨题和作业题-第2讲");
  });
  it("retains title and body of homework announcements, excluding publication metadata", () => {
    const html = `<ul id="announcementList"><li id="notice"><h3 class="item">本周安排</h3><div class="details"><p>发布时间: 2026年9月25日</p><div class="vtbegenerated"><p>请完成第一章作业。</p><p>截止时间：待定</p></div></div><div class="announcementInfo">发布至：作业课程</div></li><li><h3>加群通知</h3><div class="details"><div class="vtbegenerated">请加入群聊</div></div></li></ul>`;
    const records = extractHomeworkRecordsFromHtml(html, context);
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({ title: "本周安排", deadline: null, deadline_raw: "截止时间：待定" });
    expect(records[0].description).not.toContain("发布时间");
    expect(records[0].description).not.toContain("发布至");
  });
  it("keeps deadline fields separate across item boundaries and preserves conflicts", () => {
    const records = extractHomeworkRecordsFromHtml(page(item("a", "作业一", "<p>时间待定</p>") + item("b", "作业二", "<p>截止日期：</p><p>2026-09-30 18时30分</p>") + item("c", "作业三", "<p>截止：2026-09-30</p><p>截止：2026-10-01</p>")), context);
    expect(records.map((record) => record.deadline)).toEqual([null, "2026-09-30T18:30:00+08:00", null]);
    expect(records[2].deadline_raw).toContain("2026-10-01");
  });
  it("deduplicates an item seen in multiple sections but keeps distinct same-title items", () => {
    const records = extractHomeworkRecordsFromHtml(page(item("a", "作业", "时间待定", assignment) + item("b", "作业", "时间待定", assignment)), context);
    const repeated = extractHomeworkRecordsFromHtml(page(item("a", "作业", "时间待定", assignment)), { ...context, section: "教学内容", sourceUrl: "https://example.test/other" });
    expect(buildHomeworkScrapeResult([...records, ...repeated], { asOf: context.asOf, term: null, coursesChecked: 1, sectionsChecked: 2 }).records).toEqual(records);
  });
});
