import { describe, expect, it } from "vitest";
import { cleanSourceUrl, deduplicateCourseSources, extractCourseSourcesFromHtml, parseSourcePublication } from "../src/pku-source.js";

const context = { course: "Synthetic course", section: "课程通知", sourceUrl: "https://example.test/content?course_id=c1", collectedAt: "2026-09-26T09:00:00+08:00" };
const contentItem = (id: string, title: string, body: string, href = "") => `<li id="contentListItem:${id}"><div class="item" id="${id}"><h3>${href ? `<a href="${href}">${title}</a>` : title}</h3></div><div class="details">${body}</div></li>`;
const page = (items: string) => `<nav><h3>课程作业</h3></nav><ul id="content_listContainer">${items}</ul>`;

describe("complete course source collection", () => {
  it("preserves non-homework announcements and teaching materials without task decisions", () => {
    const html = `<ul id="announcementList"><li id="n1"><h3>加群通知</h3><div class="details"><p>发布时间: 2026年9月25日 星期五 下午01时30分45秒 CST</p><div class="vtbegenerated"><p>请加入课程群。</p><p>提前阅读教材。</p></div></div><div class="announcementInfo">发帖者：老师</div></li></ul>${page(contentItem("m1", "第一讲课件", "<p>本讲内容简介</p>"))}`;
    const records = extractCourseSourcesFromHtml(html, context);
    expect(records.map((r) => r.title)).toEqual(["加群通知", "第一讲课件"]);
    expect(records[0].body).toBe("请加入课程群。\n提前阅读教材。");
    expect(records[0].published_at).toEqual({ value: "2026-09-25T13:30:45+08:00", precision: "datetime", basis: "source" });
    expect(records[0].collected_at).toBe(context.collectedAt);
    expect(records[1].published_at).toEqual({ value: null, precision: "unknown", basis: "unknown" });
    expect(records[0].body).not.toContain("发布时间");
  });
  it("keeps paragraphs and attachments inside their source, marking unread contents", () => {
    const [record] = extractCourseSourcesFromHtml(page(contentItem("hw", "作业一", '<p>请交报告。</p><p>截止时间待定。</p><a href="/report.pdf?access_token=secret">报告模板.pdf</a><img src="/figure.png" alt="图示">', "/webapps/assignment/uploadAssignment?content_id=hw")), context);
    expect(record.kind).toBe("assignment");
    expect(record.text_quality).toBe("partial");
    expect(record.references).toHaveLength(3);
    expect(record.references.every((ref) => ref.content_read === false)).toBe(true);
    expect(record.references[1].url).toBe("https://example.test/report.pdf");
    expect(record.body).toContain("截止时间待定。");
  });
  it("does not claim complete text for title-only files or image-only bodies", () => {
    const records = extractCourseSourcesFromHtml(page(contentItem("a", "教材", "", "/book.pdf") + contentItem("b", "通知", '<img src="/notice.png">')), context);
    expect(records.map((r) => r.text_quality)).toEqual(["summary", "summary"]);
    expect(records.every((r) => r.warnings.length > 0)).toBe(true);
  });
  it("preserves missing text as missing instead of manufacturing evidence", () => {
    const [record] = extractCourseSourcesFromHtml(page(contentItem("empty", "", "")), context);
    expect(record.text_quality).toBe("missing");
  });
  it("ignores navigation and folder links without filtering substantive content", () => {
    expect(extractCourseSourcesFromHtml(page(contentItem("folder", "资料", "", "/webapps/blackboard/content/listContent.jsp?content_id=x")), context)).toEqual([]);
    expect(extractCourseSourcesFromHtml("<body>作业一</body>", context)).toEqual([]);
  });
  it("uses platform IDs stable across title edits and section views, scoped to courses", () => {
    const one = extractCourseSourcesFromHtml(page(contentItem("x", "原标题", "正文")), context)[0];
    const two = extractCourseSourcesFromHtml(page(contentItem("x", "更新标题", "新的正文")), { ...context, sourceUrl: "https://example.test/other?course_id=c1", section: "另一栏目" })[0];
    const other = extractCourseSourcesFromHtml(page(contentItem("x", "原标题", "正文")), { ...context, sourceUrl: "https://example.test/content?course_id=c2" })[0];
    expect(one.source_item_id).toBe("x");
    expect(two.input_id).toBe(one.input_id);
    expect(other.input_id).not.toBe(one.input_id);
    expect(deduplicateCourseSources([one, two, other])).toEqual([two, other]);
  });
  it("does not collapse separate platform items with the same title", () => {
    const records = extractCourseSourcesFromHtml(page(contentItem("a", "练习", "正文") + contentItem("b", "练习", "正文")), context);
    expect(deduplicateCourseSources(records)).toHaveLength(2);
  });
});

describe("source metadata fidelity", () => {
  it.each([null, "发布时间：待定", "发布时间：9月25日", "发布时间：2026-09-25 25:30", "发布时间：2026-02-30"])("keeps unsupported publication %s unknown", (raw) => {
    expect(parseSourcePublication(raw)).toEqual({ value: null, precision: "unknown", basis: "unknown" });
  });
  it("retains date precision without adding midnight", () => {
    expect(parseSourcePublication("发布时间：2026-09-25")).toEqual({ value: "2026-09-25", precision: "date", basis: "source" });
    expect(parseSourcePublication("发布时间：2026-09-25 18:30:15").value).toBe("2026-09-25T18:30:15+08:00");
  });
  it("removes credentials and rejects non-http source links", () => {
    expect(cleanSourceUrl("https://user:pass@example.test/a?course_id=c1&token=x&session=y#token=z")).toBe("https://example.test/a?course_id=c1");
    expect(cleanSourceUrl("javascript:alert(1)")).toBeNull();
  });
});
