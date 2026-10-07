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

describe("substantive references versus page chrome", () => {
  const body = "请在2026年12月31日18点前提交报告。";
  it.each([
    '<img src="/images/ci/icons/cmlink_generic.gif" alt="" srcset="/deadline.png 1x, /deadline2.png 2x">',
    '<picture><source media="(min-width: 800px)" srcset="/deadline.png 800w, /deadline2.png 1600w"><img src="/images/ci/icons/cmlink_generic.gif" alt=""></picture>',
    '<img srcset="/deadline.png 1x, /deadline2.png 2x">',
  ])("retains responsive substantive candidates behind a missing/decorative fallback: %s", (image) => {
    const [record] = extractCourseSourcesFromHtml(page(contentItem("responsive", "作业", `<p>${body}</p>${image}`)), context);
    expect(record.text_quality).toBe("partial");
    expect(record.references.map((ref) => ref.url)).toEqual(["https://example.test/deadline.png", "https://example.test/deadline2.png"]);
    expect(record.references.every((ref) => ref.kind === "image" && !ref.content_read)).toBe(true);
  });
  it("preserves commas in image URLs and never exports data-URI candidates", () => {
    const [record] = extractCourseSourcesFromHtml(page(contentItem("commas", "作业", `<p>${body}</p><img srcset="data:image/png;base64,SYNTHETIC_BYTES 1x, /deadline,large.png 2x, /deadline.png 3x">`)), context);
    expect(record.references.map((ref) => ref.url)).toEqual(["", "https://example.test/deadline,large.png", "https://example.test/deadline.png"]);
    expect(JSON.stringify(record)).not.toContain("SYNTHETIC_BYTES");
    expect(record.text_quality).toBe("partial");
  });
  it("deduplicates fallback candidates and ignores responsive decorative-only assets", () => {
    const records = extractCourseSourcesFromHtml(page(contentItem("real", "作业", `<p>${body}</p><img src="/deadline.png" srcset="/deadline.png 1x, /deadline.png 2x">`) + contentItem("icons", "作业", `<p>${body}</p><picture><source srcset="/images/ci/ng/cal_year_event.gif 2x"><img src="/images/ci/icons/cmlink_generic.gif" srcset="/images/ci/icons/cmlink_generic.gif 1x" alt=""></picture>`)), context);
    expect(records[0].references).toHaveLength(1);
    expect(records[0].text_quality).toBe("partial");
    expect(records[1].references).toEqual([]);
    expect(records[1].text_quality).toBe("complete");
  });
  it("keeps responsive image-only content as a summary with unread references", () => {
    const [record] = extractCourseSourcesFromHtml(page(contentItem("only-image", "作业", '<picture><source srcset="/deadline.png 1x"><img alt=""></picture>')), context);
    expect(record.text_quality).toBe("summary");
    expect(record.references[0]).toMatchObject({ kind: "image", content_read: false, read_status: "unread" });
  });
  it("ignores known empty-alt UI icons and navigation", () => {
    const [record] = extractCourseSourcesFromHtml(page(contentItem("icon", "作业", `<p>${body}<img src="/images/ci/icons/cmlink_generic.gif" alt=""><img src="/images/ci/icons/cmlink_generic.gif" alt="文件"><img src="/images/ci/ng/cal_year_event.gif" alt="单击获得更多选项"></p><nav><a href="/menu">菜单</a></nav><a href="#top">回顶部</a>`)), context);
    expect(record.text_quality).toBe("complete");
    expect(record.references).toEqual([]);
    expect(record.body).not.toContain("菜单");
  });
  it.each(["补充附件", "期限见附件"])("keeps an unread %s partial", (label) => {
    const [record] = extractCourseSourcesFromHtml(page(contentItem("attachment", "作业", `<p>${body}</p><a href="/hw.pdf">${label}</a>`)), context);
    expect(record.text_quality).toBe("partial");
    expect(record.references[0].read_status).toBe("unread");
  });
  it("retains meaningful UI-path images and unknown platform images and ordinary images with empty alt", () => {
    const [record] = extractCourseSourcesFromHtml(page(contentItem("img", "作业", `<p>${body}</p><img src="/images/ci/notice.png" alt="截止时间截图"><img src="/actual.png" alt=""><img src="/images/ci/custom.png" alt="">`)), context);
    expect(record.references).toHaveLength(3);
    expect(record.references.every((ref) => ref.kind === "image" && ref.reason === "image_not_read")).toBe(true);
    expect(record.text_quality).toBe("partial");
  });
});

it("keeps embedded/non-http images as unread without exporting their data", () => {
  const [record] = extractCourseSourcesFromHtml(page(contentItem("inline", "作业", '<p>请按图中期限提交。</p><img src="data:image/png;base64,private-data">')), context);
  expect(record.text_quality).toBe("partial");
  expect(record.references[0]).toMatchObject({ kind: "image", url: "", content_read: false });
  expect(JSON.stringify(record)).not.toContain("private-data");
});
