import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import type { Page } from "playwright";
import { describe, expect, it, vi } from "vitest";
import {
  enrichAssignmentSource,
  enrichAssignmentSources,
  isPkuAssignmentUrl,
  parseAssignmentDetailHtml,
  readAssignmentDetail,
} from "../src/pku-assignment.js";
import { toRecognitionRequests } from "../src/pku-recognition.js";
import { enrichSourceAttachments } from "../src/pku-attachments.js";
import { extractCourseSourcesFromHtml } from "../src/pku-source.js";

const detailUrl = "https://course.pku.edu.cn/webapps/assignment/uploadAssignment?content_id=synthetic&course_id=course";

async function fixture(name: string): Promise<string> {
  return readFile(new URL(`fixtures/pku-assignment/${name}`, import.meta.url), "utf8");
}

function source(body = "外层作业说明") {
  const html = `<ul id="content_listContainer"><li id="contentListItem:synthetic"><div class="item" id="synthetic"><h3><a href="${detailUrl}">列表标题</a></h3></div><div class="details"><p>${body}</p></div></li></ul>`;
  return extractCourseSourcesFromHtml(html, {
    course: "Synthetic course",
    section: "课程作业",
    sourceUrl: "https://course.pku.edu.cn/webapps/blackboard/content/listContent.jsp?course_id=course",
    collectedAt: "2026-09-30T12:00:00+08:00",
  })[0];
}

function page(html: string, destination = detailUrl) {
  return {
    goto: vi.fn().mockResolvedValue({ ok: () => true, status: () => 200 }),
    url: vi.fn().mockReturnValue(destination),
    locator: vi.fn().mockReturnValue({ count: async () => 0 }),
    content: vi.fn().mockResolvedValue(html),
  } as unknown as Pick<Page, "goto" | "url" | "locator" | "content">;
}

describe("Blackboard assignment detail parsing", () => {
  it("reads the Chinese assignment-information block but not the submission form", async () => {
    const detail = parseAssignmentDetailHtml(await fixture("active-zh.html"));
    expect(detail).toEqual({
      state: "active",
      title: "第二章作业",
      description: "课本《离散数学》第3版\n上第二章课后作业10，16，26，31，36，46，49",
      due_raw: "2026年12月31日 星期四 下午11:59",
    });
    expect(JSON.stringify(detail)).not.toContain("不得读取的草稿内容");
  });

  it("supports English labels without normalizing the raw date", async () => {
    expect(parseAssignmentDetailHtml(await fixture("active-en.html"))).toEqual({
      state: "active",
      title: "Essay 1",
      description: "Read chapter one and upload the essay.",
      due_raw: "Thursday, December 31, 2026 11:59 PM",
    });
  });

  it("keeps an unset due date empty and lets the listing body remain the fallback", async () => {
    const detail = parseAssignmentDetailHtml(await fixture("active-no-due.html"));
    expect(detail).toEqual({ state: "active", title: "自选练习", description: "", due_raw: null });
    const enriched = enrichAssignmentSource(source("外层仍有说明"), detailUrl, detail);
    expect(enriched.body).toBe("外层仍有说明");
  });

  it("recognizes submission history without exposing files, grades or attempt times", async () => {
    const detail = parseAssignmentDetailHtml(await fixture("submitted-zh.html"));
    expect(detail).toEqual({ state: "submitted", heading_raw: "复查提交历史记录：人工智能基础 第一次概念题作业", title: "人工智能基础 第一次概念题作业", description: "", due_raw: null });
    const original = source();
    const enriched = enrichAssignmentSource(original, detailUrl, detail);
    expect(enriched.input_id).toBe(original.input_id);
    expect(enriched.body).toBe("外层作业说明\n提交状态：已提交");
    expect(enriched.text_quality).toBe("partial");
    expect(enriched.references[0]).toMatchObject({ content_read: false, read_status: "partial", reason: "submission_history_only" });
    expect(enriched.assignment).toEqual({ state: "submitted", due_raw: null });
    expect(JSON.stringify(enriched)).not.toMatch(/学生私人|10\.00|26-5-17|学号姓名/);
  });

  it("returns unknown instead of treating arbitrary page text as assignment evidence", () => {
    expect(parseAssignmentDetailHtml("<html><body><h1>系统消息</h1><p>2026-12-31 23:59</p></body></html>"))
      .toEqual({ state: "unknown", title: null, description: "", due_raw: null });
  });
});

describe("read-only assignment enrichment", () => {
  it("accepts only authenticated PKU assignment URLs", () => {
    expect(isPkuAssignmentUrl(detailUrl)).toBe(true);
    expect(isPkuAssignmentUrl("https://course.pku.edu.cn/webapps/blackboard/content/listContent.jsp")).toBe(false);
    expect(isPkuAssignmentUrl("https://example.test/webapps/assignment/uploadAssignment")).toBe(false);
    expect(isPkuAssignmentUrl("javascript:alert(1)")).toBe(false);
  });

  it("uses navigation and page reads only, with no click or form API exposed", async () => {
    const mock = page(await fixture("active-zh.html"));
    const result = await readAssignmentDetail(mock, detailUrl);
    expect(result.state).toBe("active");
    expect(mock.goto).toHaveBeenCalledOnce();
    expect(mock.content).toHaveBeenCalledOnce();
    expect(mock).not.toHaveProperty("click");
    expect(mock).not.toHaveProperty("fill");
  });

  it("rejects HTTP errors and authentication redirects", async () => {
    const failed = page("");
    vi.mocked(failed.goto).mockResolvedValue({ ok: () => false, status: () => 500 } as never);
    await expect(readAssignmentDetail(failed, detailUrl)).rejects.toThrow("HTTP 500");
    await expect(readAssignmentDetail(page("", "https://course.pku.edu.cn/webapps/bb-sso-BBLEARN/login.html"), detailUrl))
      .rejects.toThrow("login may have expired");
  });

  it("enriches assignment records and preserves downstream v0.2 compatibility", async () => {
    const original = source();
    const [enriched] = await enrichAssignmentSources(page(await fixture("active-zh.html")), [original]);
    expect(enriched.input_id).toBe(original.input_id);
    expect(enriched.title).toBe("第二章作业");
    expect(enriched.body).toContain("到期日期：2026年12月31日 星期四 下午11:59");
    expect(enriched.references[0].content_read).toBe(true);
    expect(enriched.assignment).toEqual({ state: "active", due_raw: "2026年12月31日 星期四 下午11:59" });
    const record = toRecognitionRequests([enriched], "assignment:test")[0].records[0];
    expect(record.text).toContain("到期日期：2026年12月31日 星期四 下午11:59");
    expect(record.upstream_hints.assignment).toEqual(enriched.assignment);
    expect(record.upstream_hints.raw.assignment_due_date).toBe("2026年12月31日 星期四 下午11:59");
    const validation = spawnSync(process.env.PYTHON ?? "python3", ["-c", "import json,sys; from ddl_text.contract_v02 import validate_request; validate_request(json.load(sys.stdin))"], {
      cwd: fileURLToPath(new URL("../../", import.meta.url)), input: JSON.stringify(toRecognitionRequests([enriched], "assignment:validate")[0]), encoding: "utf8",
    });
    expect(validation.error).toBeUndefined();
    expect(validation.stderr).toBe("");
    expect(validation.status).toBe(0);
  });

  it("retains listing evidence when a detail page cannot be read", async () => {
    const original = source();
    const mock = page("");
    vi.mocked(mock.goto).mockRejectedValue(new Error("timeout"));
    const [result] = await enrichAssignmentSources(mock, [original]);
    expect(result.input_id).toBe(original.input_id);
    expect(result.body).toBe(original.body);
    expect(result.warnings).toContain("assignment detail could not be read; listing text was retained");
  });
});

describe("#110 submitted state handoff", () => {
  it.each(["复查提交历史记录：同名作业", "Review Submission History: Same homework"])("exports %s as submitted without reading private history", async (heading) => {
    const html = `<h1>${heading}</h1><div class="attempt">SYNTHETIC_PRIVATE_ANSWER<a href="/private.pdf">SYNTHETIC_PRIVATE_FILE</a><div class="grade">SYNTHETIC_PRIVATE_GRADE</div></div><button>开始新的 / Start New</button>`;
    const original = source();
    const [enriched] = await enrichAssignmentSources(page(html), [original]);
    const read = vi.fn();
    const [result] = await enrichSourceAttachments([enriched], read);
    const record = toRecognitionRequests([result])[0].records[0];
    expect(record.upstream_hints.assignment).toEqual({ state: "submitted", due_raw: null });
    expect(record.input_id).toBe(original.input_id);
    expect(record.source_item_id).toBe(original.source_item_id);
    expect(new URL(record.source_url).searchParams.get("course_id")).toBe("course");
    expect(new URL(record.upstream_hints.references[0].url).searchParams.get("content_id")).toBe("synthetic");
    expect(record.text_quality).toBe("partial");
    expect(record.upstream_hints.references[0]).toMatchObject({ content_read: false, read_status: "partial", reason: "submission_history_only" });
    expect(record.text).toContain("提交状态：已提交");
    expect(JSON.stringify(record)).not.toContain("SYNTHETIC_PRIVATE");
    expect(read).not.toHaveBeenCalled();
  });

  it.each([
    '<p>复查提交历史记录：HW</p>',
    '<nav><h1>复查提交历史记录：HW</h1></nav>',
    '<div id="assignmentDescription"><h2>Review Submission History: instructions</h2></div>',
    '<div id="assignmentInfo"><h2>Review Submission History: instructions</h2></div>',
    '<div id="assignmentSubmission"><h1>复查提交历史记录：HW</h1></div>',
    '<div class="attempt"><h1>Review Submission History: old attempt</h1></div>',
  ])("does not use body, navigation or private headings as the page state: %s", (content) => {
    const detail = parseAssignmentDetailHtml(`<h1>Upload Assignment: HW</h1>${content}`, detailUrl);
    expect(detail.state).toBe("active");
  });

  it("preserves the state even if a teacher attachment cannot be read", async () => {
    const original = source('<a href="/bbcswebdav/pid-1-dt-content-rid-2/HW.txt">HW.txt</a>');
    const detail = parseAssignmentDetailHtml('<h1>Review Submission History: HW</h1>', detailUrl);
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(original, detailUrl, detail)], async () => { throw new Error("synthetic download failure"); });
    expect(result.assignment?.state).toBe("submitted");
    expect(result.references[1]).toMatchObject({ content_read: false, reason: "download_failed" });
    expect(toRecognitionRequests([result])[0].records[0].text_quality).toBe("partial");
  });

  it("keeps unknown pages and failed reads unsubmitted", async () => {
    const html = '<h1>系统消息</h1><p>提交失败，草稿已保存。</p>';
    const [unknown] = await enrichAssignmentSources(page(html), [source()]);
    expect(unknown.assignment?.state).toBe("unknown");
    const failed = page('<h1>Review Submission History: HW</h1>');
    vi.mocked(failed.goto).mockRejectedValue(new Error("timeout"));
    const [unread] = await enrichAssignmentSources(failed, [source()]);
    expect(unread.assignment?.state).not.toBe("submitted");
    expect(unread.references[0].reason).toBe("detail_failed");
  });

  it.each([
    detailUrl.replace("course_id=course", "course_id=other"),
    detailUrl.replace("content_id=synthetic", "content_id=other"),
    detailUrl.replace("&course_id=course", ""),
    detailUrl.replace("content_id=synthetic&", ""),
  ])("does not assign another or unidentified page's submitted state to this source: %s", async (destination) => {
    const mock = page('<h1>Review Submission History: Same homework</h1>', destination);
    const [result] = await enrichAssignmentSources(mock, [source()]);
    expect(result.assignment?.state).not.toBe("submitted");
    expect(result.references[0].reason).toBe("detail_failed");
    expect(mock.content).not.toHaveBeenCalled();
  });

  it("retains identity across status and title changes and separates same-name assignments", () => {
    const listing = (courseId: string, contentId: string, title: string) => extractCourseSourcesFromHtml(`<ul id="content_listContainer"><li id="contentListItem:${contentId}"><div class="item" id="${contentId}"><h3><a href="https://course.pku.edu.cn/webapps/assignment/uploadAssignment?course_id=${courseId}&content_id=${contentId}">${title}</a></h3></div><div class="details">Prepare a report and bring a printed copy.</div></li></ul>`, { course: "Same course name", section: "作业", sourceUrl: `https://course.pku.edu.cn/webapps/blackboard/content/listContent.jsp?course_id=${courseId}`, collectedAt: "2026-10-07T12:00:00+08:00" })[0];
    const original = listing("course-one", "hw-one", "Same homework");
    const renamed = listing("course-one", "hw-one", "Renamed homework");
    const submitted = parseAssignmentDetailHtml('<h1>Review Submission History: Renamed homework</h1>');
    const result = enrichAssignmentSource(renamed, renamed.references[0].url, submitted);
    expect(result.input_id).toBe(original.input_id);
    expect(result.source_item_id).toBe(original.source_item_id);
    expect(result.body).toContain("Prepare a report and bring a printed copy.");
    expect(listing("course-two", "hw-one", "Same homework").input_id).not.toBe(original.input_id);
    expect(listing("course-one", "hw-two", "Same homework").input_id).not.toBe(original.input_id);
    const repeated = enrichAssignmentSource(result, renamed.references[0].url, submitted);
    expect({ ...repeated, warnings: [...repeated.warnings].sort() }).toEqual({ ...result, warnings: [...result.warnings].sort() });
    const active = enrichAssignmentSource(renamed, renamed.references[0].url, { state: "active", title: "Renamed homework", description: "Resubmit the report.", due_raw: null });
    expect(active.assignment?.state).toBe("active");
    expect(active.input_id).toBe(original.input_id);
  });

  it("retains submitted metadata when the text budget has no room for the status marker", () => {
    const original = source();
    original.body = "a".repeat(100_000 - original.title.length - 2);
    const detail = parseAssignmentDetailHtml(`<h1>Review Submission History: ${original.title}</h1>`);
    const result = enrichAssignmentSource(original, detailUrl, detail);
    expect(result.body).toBe(original.body);
    expect(result.assignment?.state).toBe("submitted");
    expect(result.references[0].reason).toBe("too_large");
    expect(toRecognitionRequests([result])[0].records[0].text).toHaveLength(100_000);
  });
});

describe("assignment reference coverage", () => {
  it("includes attachments discovered only inside teacher instructions", () => {
    const detail = parseAssignmentDetailHtml('<h1>Upload Assignment: HW1</h1><div id="assignmentDescription">Submit before 2026-12-31 18:00.<a href="/bbcswebdav/pid-1-dt-content-rid-2/HW1.pdf">HW1.pdf</a><img src="/images/ci/icons/cmlink_generic.gif" alt=""></div><div class="attempt"><a href="/private.pdf">Private.pdf</a></div>', detailUrl);
    const enriched = enrichAssignmentSource(source(), detailUrl, detail);
    expect(enriched.references).toHaveLength(2);
    expect(enriched.references[1].kind).toBe("attachment");
    expect(enriched.text_quality).toBe("partial");
    expect(JSON.stringify(enriched)).not.toContain("Private.pdf");
  });
  it("does not call a due-date-only detail complete", () => {
    const enriched = enrichAssignmentSource(source(""), detailUrl, { state: "active", title: "HW", description: "", due_raw: "2026-12-31" });
    expect(enriched.text_quality).toBe("partial");
  });
});

describe("review regression: description containers and text budgets", () => {
  it.each(["form", 'form id="uploadAssignmentForm" class="submission"', 'div class="submission"', 'div class="attempt"', 'div class="grade"', 'nav', 'div role="navigation"'])("excludes matching private/UI containers under %s from the exported evidence", async (container) => {
    const tag = container.split(" ")[0];
    const html = `<h1>Upload Assignment: HW</h1><div data-assignment-description>Teacher instructions.</div>
      <${container} data-assignment-description>SYNTHETIC_PRIVATE_ROOT<a href="/private-root.txt">SYNTHETIC_PRIVATE_FILE</a>
        <div data-assignment-description>SYNTHETIC_PRIVATE_DRAFT<a href="/private-draft.txt">SYNTHETIC_PRIVATE_DRAFT_FILE</a></div>
        <div class="assignmentInfo"><div class="vtbegenerated">SYNTHETIC_PRIVATE_ATTEMPT<img src="/private.png"></div></div>
      </${tag}><div id="assignmentInfo"><div class="vtbegenerated">Teacher deadline: 2026-12-31.</div></div>`;
    const detail = parseAssignmentDetailHtml(html, detailUrl);
    expect(detail.description).toBe("Teacher instructions.\n\nTeacher deadline: 2026-12-31.");
    expect(detail.references).toBeUndefined();
    const read = vi.fn();
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(source(""), detailUrl, detail)], read);
    const exported = JSON.stringify(toRecognitionRequests([result]));
    expect(exported).not.toContain("SYNTHETIC_PRIVATE");
    expect(exported).not.toContain("/private");
    expect(read).not.toHaveBeenCalled();
    expect(result.text_quality).toBe("complete");
  });
  it("continues past empty placeholders to collect the real deadline and attachment", () => {
    const html = '<h1>Upload Assignment: HW</h1><div data-assignment-description></div><div id="assignmentInfo"><div class="vtbegenerated">Submit report by 2026-10-12.<a href="/bbcswebdav/pid-1-dt-content-rid-2/HW.txt">HW.txt</a></div></div>';
    const detail = parseAssignmentDetailHtml(html, detailUrl);
    const result = enrichAssignmentSource(source("请按详情说明完成"), detailUrl, detail);
    expect(detail.description).toContain("Submit report by 2026-10-12.");
    expect(result.body).toContain("2026-10-12");
    expect(result.references).toHaveLength(2);
    expect(result.text_quality).toBe("partial");
  });
  it("keeps image-only containers and multiple later teacher blocks", () => {
    const detail = parseAssignmentDetailHtml('<h1>Upload Assignment: HW</h1><div data-assignment-description><img src="/notice.png"></div><div id="assignmentInfo"><div class="vtbegenerated">Submit before 2026-10-12.</div><div class="vtbegenerated">Bring a printed report.</div></div>', detailUrl);
    expect(detail.description).toContain("Submit before 2026-10-12.");
    expect(detail.description).toContain("Bring a printed report.");
    expect(detail.references?.[0].kind).toBe("image");
    expect(enrichAssignmentSource(source(), detailUrl, detail).text_quality).toBe("partial");
  });
  it("retains a whole listing on oversized detail merge and still exports other records", async () => {
    const original = source("a".repeat(60_000));
    const detail = { state: "active" as const, title: "HW", description: "b".repeat(60_000), due_raw: "2026-12-31 18:00" };
    const enriched = enrichAssignmentSource(original, detailUrl, detail);
    const normal = { ...source("Submit report by 2026-12-31."), input_id: "synthetic:normal" };
    const records = await enrichSourceAttachments([enriched, normal], vi.fn());
    const [request] = toRecognitionRequests(records, "review:text-limit");
    expect(request.records).toHaveLength(2);
    const fallback = request.records[0];
    expect(fallback.text).toBe(`${original.title}\n\n${original.body}`);
    expect(fallback.text_quality).toBe("partial");
    expect(fallback.upstream_hints.references[0]).toMatchObject({ content_read: false, read_status: "failed", reason: "too_large" });
    expect(fallback.upstream_hints.raw.assignment_due_date).toBe(detail.due_raw);
    expect(fallback.upstream_hints.warnings.join(" ")).toContain("manual review");
    expect(request.records[1].text).toContain(normal.body);
  });
  it("counts title, separator and due-date text at the exact v0.2 boundary", () => {
    const title = "HW";
    const due = "2026-12-31";
    const fact = `\n到期日期：${due}`;
    const description = "a".repeat(100_000 - title.length - 2 - fact.length);
    const original = source("");
    const exact = enrichAssignmentSource(original, detailUrl, { state: "active", title, description, due_raw: due });
    expect(toRecognitionRequests([exact])[0].records[0].text).toHaveLength(100_000);
    const overflow = enrichAssignmentSource(original, detailUrl, { state: "active", title, description: description + "a", due_raw: due });
    expect(overflow.body).toBe(original.body);
    expect(overflow.references[0].reason).toBe("too_large");
    expect(overflow.text_quality).toBe("summary");
    expect(() => toRecognitionRequests([overflow])).not.toThrow();
  });
});

describe("review regression: private metadata and assignment wrapper forms", () => {
  it.each(['div class="submission"', 'div class="attempt"', 'div class="grade"', "form", "nav"])("ignores private headings and dates under %s before selecting teacher metadata", async (container) => {
    const tag = container.split(" ")[0];
    const html = `<${container}><h1>Review Submission History: SYNTHETIC_PRIVATE_TITLE</h1><div class="assignmentInfo"><div><span>Due Date</span><span>2026-11-01 SYNTHETIC_PRIVATE_GRADE_93</span></div></div></${tag}>
      <h1>Upload Assignment: HW</h1><section id="assignmentInfo"><div class="metaField"><span>Due Date</span><span>2026-12-31</span><div class="grade">SYNTHETIC_PRIVATE_GRADE_87</div></div><div class="vtbegenerated">Teacher instructions.</div></section>`;
    const detail = parseAssignmentDetailHtml(html, detailUrl);
    expect(detail).toMatchObject({ state: "active", title: "HW", description: "Teacher instructions.", due_raw: "2026-12-31" });
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(source(), detailUrl, detail)], vi.fn());
    const exported = JSON.stringify(toRecognitionRequests([result]));
    expect(exported).not.toContain("SYNTHETIC_PRIVATE");
    expect(exported).not.toContain("2026-11-01");
    expect(exported).toContain("2026-12-31");
  });
  it("does not obtain dates from outside teacher information or climb beyond its boundary", () => {
    const detail = parseAssignmentDetailHtml('<h1>Upload Assignment: HW</h1><div><span>Due Date</span><span>2030-01-01</span></div><section id="assignmentInfo"><div class="metaField"><span>Due Date</span></div><div class="vtbegenerated">Read chapter 12.</div><div>Points Possible: 100</div></section>', detailUrl);
    expect(detail.due_raw).toBeNull();
    expect(detail.description).toBe("Read chapter 12.");
  });
  it("reads teacher text, date and attachments inside the known outer form while excluding controls/private regions", async () => {
    const html = '<form id="uploadAssignmentForm"><h1>Upload Assignment: HW</h1><section id="assignmentInfo"><div class="metaField"><span>Due Date</span><span>2026-12-31</span></div><div class="vtbegenerated">Teacher instructions.<a href="/bbcswebdav/pid-1-dt-content-rid-2/HW.txt">HW.txt</a></div></section><textarea>SYNTHETIC_PRIVATE_CONTROL</textarea><div class="submission"><div data-assignment-description>SYNTHETIC_PRIVATE_DRAFT<a href="/private.pdf">Private.pdf</a></div></div><div class="attempt"><div class="assignmentInfo"><div class="vtbegenerated">SYNTHETIC_PRIVATE_ATTEMPT</div></div></div></form>';
    const detail = parseAssignmentDetailHtml(html, detailUrl);
    expect(detail.description).toContain("Teacher instructions.");
    expect(detail.due_raw).toBe("2026-12-31");
    expect(detail.references).toHaveLength(1);
    const read = vi.fn().mockResolvedValue({ bytes: new TextEncoder().encode("Submit the teacher report."), contentType: "text/plain" });
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(source("See teacher instructions."), detailUrl, detail)], read);
    const [record] = toRecognitionRequests([result])[0].records;
    expect(record.text_quality).toBe("complete");
    expect(record.text).toContain("2026-12-31");
    expect(record.text).toContain("Submit the teacher report.");
    expect(JSON.stringify(record)).not.toContain("SYNTHETIC_PRIVATE");
    expect(JSON.stringify(record)).not.toContain("Private.pdf");
    expect(read).toHaveBeenCalledOnce();
  });
  it.each([
    '<form id="unknownForm"><div id="assignmentInfo"><div class="vtbegenerated">Unrecognized form text.</div></div></form>',
    '<div class="unknownDescription">Unrecognized description.</div>',
  ])("keeps missing/unread teacher details partial despite a nonempty listing: %s", async (description) => {
    const detail = parseAssignmentDetailHtml(`<h1>Upload Assignment: HW</h1>${description}`, detailUrl);
    const original = source("See teacher instructions.");
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(original, detailUrl, detail)], vi.fn());
    expect(result.body).toBe(original.body);
    expect(result.text_quality).toBe("partial");
    expect(result.references[0]).toMatchObject({ content_read: false, read_status: "failed", reason: "detail_unrecognized" });
    expect(result.warnings.join(" ")).toContain("manual review");
    expect(toRecognitionRequests([result])[0].records[0].text_quality).toBe("partial");
  });
});

describe("assignment collection invariants across supported layouts", () => {
  it.each(["作业信息", "Assignment Information"])("recognizes the safe %s fallback without a separate page heading or date field", async (heading) => {
    const detail = parseAssignmentDetailHtml(`<h2>${heading}</h2><div data-assignment-description>Submit the report before 2026-12-31.</div>`, detailUrl);
    expect(detail).toMatchObject({ state: "active", title: null, description: "Submit the report before 2026-12-31." });
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(source(), detailUrl, detail)], vi.fn());
    expect(toRecognitionRequests([result])[0].records[0].text_quality).toBe("complete");
    expect(result.body).toContain("2026-12-31");
  });
  it.each(["作业信息", "Assignment Information"])("does not use a private %s heading to recognize an otherwise unknown page", (heading) => {
    expect(parseAssignmentDetailHtml(`<div class="grade"><h2>${heading}</h2></div><div data-assignment-description>Unsupported page text.</div>`, detailUrl).state).toBe("unknown");
  });
  const regions = ['id="assignmentInfo"', 'class="assignmentInfo"', 'data-role="assignment-info"'];
  const dates = [
    '<span>Due Date</span><span>2026-12-31</span><br><span>23:59</span>',
    '<div class="metaField"><div class="label"><strong>Due Date</strong></div><div class="value"><strong>2026-12-31</strong><br><span>23:59</span></div></div>',
    '<span>Due Date</span> 2026-12-31 23:59 ',
  ];
  const layouts = regions.flatMap((region) => dates.flatMap((date, index) => [false, true].flatMap((wrapped) => [false, true].map((fallbackHeading) => ({ region, date, index, wrapped, fallbackHeading })))));
  it.each(layouts)("preserves dates, independent content and unread images in $region / date $index / wrapper $wrapped / fallback $fallbackHeading", async ({ region, date, wrapped, fallbackHeading }) => {
    const heading = fallbackHeading ? '<h2>Assignment Information</h2>' : '<h1>上载作业：HW</h1>';
    const teacher = `${heading}<section ${region}>${date}<div data-assignment-description>Prepare the report.<div class="vtbegenerated">Use teacher materials.<img src="/deadline-note.png"></div></div></section><div class="assignment-description">Bring a printed copy.</div>`;
    const privateArea = '<div class="attempt"><h1>Review Submission History: SYNTHETIC_PRIVATE_TITLE</h1><div class="assignmentInfo"><span>Due Date</span><span>2030-01-01</span><div class="vtbegenerated">SYNTHETIC_PRIVATE_BODY</div></div></div><div id="assignmentSubmission"><h2>Review Submission History: SYNTHETIC_PRIVATE_ID_TITLE</h2><div class="assignmentInfo"><span>Due Date</span><span>2030-01-01</span><div class="vtbegenerated">SYNTHETIC_PRIVATE_ID_BODY</div></div></div>';
    const html = wrapped ? `<form id="uploadAssignmentForm">${privateArea}${teacher}<textarea>SYNTHETIC_PRIVATE_DRAFT</textarea></form>` : `${privateArea}${teacher}`;
    const detail = parseAssignmentDetailHtml(html, detailUrl);
    expect(detail.state).toBe("active");
    expect(detail.due_raw).toBe("2026-12-31 23:59");
    expect(detail.due_unread).toBeUndefined();
    expect(detail.description.match(/Use teacher materials\./g)).toHaveLength(1);
    expect(detail.description).toContain("Bring a printed copy.");
    expect(detail.references).toHaveLength(1);
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(source("Listing instructions."), detailUrl, detail)], vi.fn());
    const [record] = toRecognitionRequests([result])[0].records;
    expect(record.text_quality).toBe("partial");
    expect(record.text).toContain("Listing instructions.");
    expect(record.text).toContain("2026-12-31 23:59");
    expect(record.text.match(/Use teacher materials\./g)).toHaveLength(1);
    expect(record.upstream_hints.references[0]).toMatchObject({ kind: "detail", content_read: true });
    expect(record.upstream_hints.references[1]).toMatchObject({ kind: "image", content_read: false });
    expect(JSON.stringify(record)).not.toContain("SYNTHETIC_PRIVATE");
    expect(JSON.stringify(record)).not.toContain("2030-01-01");
  });
  it.each([
    '<span>Due Date</span><span class="value"></span>',
    '<span>Due Date</span><span class="value">待确定</span>',
    '<div class="metaField"><span>Due Date</span></div><span>Points Possible: 100</span>',
    '<div class="metaField"><span>Due Date</span></div><span>2030-01-01</span>',
    '<div class="metaField"><span>Due Date</span></div>',
  ])("keeps an unread due-date field partial without borrowing chapter numbers: %s", async (date) => {
    const detail = parseAssignmentDetailHtml(`<h1>Upload Assignment: HW</h1><section id="assignmentInfo">${date}<div class="vtbegenerated">Read chapter 12.<a href="/bbcswebdav/pid-1-dt-content-rid-2/HW.txt">HW.txt</a></div></section>`, detailUrl);
    expect(detail.due_raw).toBeNull();
    expect(detail.due_unread).toBe(true);
    const read = vi.fn().mockResolvedValue({ bytes: new TextEncoder().encode("Teacher attachment text."), contentType: "text/plain" });
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(source(), detailUrl, detail)], read);
    expect(result.body).toContain("Read chapter 12.");
    expect(result.body).toContain("Teacher attachment text.");
    expect(result.text_quality).toBe("partial");
    expect(result.references[0]).toMatchObject({ content_read: false, read_status: "failed", reason: "detail_unrecognized" });
    expect(result.references[1].content_read).toBe(true);
    expect(result.warnings.join(" ")).toContain("manual review");
    expect(toRecognitionRequests([result])[0].records[0].text_quality).toBe("partial");
  });
  it.each(["Not set", "未设置"])("treats an explicit %s as no deadline rather than unread metadata", (unset) => {
    const detail = parseAssignmentDetailHtml(`<h1>Upload Assignment: HW</h1><section id="assignmentInfo"><span>Due Date</span><span>${unset}</span><div class="vtbegenerated">Read the teacher notes.</div></section>`, detailUrl);
    expect(detail.due_raw).toBeNull();
    expect(detail.due_unread).toBeUndefined();
    expect(enrichAssignmentSource(source(), detailUrl, detail).text_quality).toBe("complete");
  });
  const fieldLayouts = [
    { name: "inline class labels", render: (value: string) => `<span class="label">Due Date</span><span class="value">${value}</span><span class="label">Available from</span><span class="value">2026-10-01</span>` },
    { name: "inline role labels", render: (value: string) => `<span data-role="label"><strong>Due Date</strong></span><span data-role="value">${value}</span><span data-role="label">Available from</span><span data-role="value">2026-10-01</span>` },
    { name: "generic parent fallback", render: (value: string) => `<div><span class="label"><strong>Due Date</strong></span><span class="value">${value}</span><span class="label">Available from</span><span class="value">2026-10-01</span></div>` },
    { name: "nested next label", render: (value: string) => `<div><span class="label">Due Date</span><span class="value">${value}</span><span><strong data-role="label">Available from</strong><span>2026-10-01</span></span></div>` },
    { name: "next label within value", render: (value: string) => `<div><span class="label">Due Date</span><span class="value">${value}<span class="label">Available from</span><span>2026-10-01</span></span></div>` },
    { name: "block fields", render: (value: string) => `<div><div class="label"><strong>Due Date</strong></div><div class="value">${value}</div><div data-role="label">Available from</div><div class="value">2026-10-01</div></div>` },
    { name: "metadata rows", render: (value: string) => `<div class="metaField"><span class="label"><strong>Due Date</strong></span><span class="value">${value}</span></div><div class="metaField"><span class="label">Available from</span><span class="value">2026-10-01</span></div>` },
    { name: "table cells", render: (value: string) => `<table><tr><td><strong>Due Date</strong></td><td class="value">${value}</td><td class="label">Available from</td><td>2026-10-01</td></tr></table>` },
    { name: "native table headers", render: (value: string) => `<table><tr><th>Due Date</th> \n<!-- separator --><td>${value}</td><th>Available from</th><td>2026-10-01</td></tr></table>` },
    { name: "nested table headers", render: (value: string) => `<table><tr><th><strong>Due Date</strong></th><td>${value}</td><th><em>Available from</em></th><td>2026-10-01</td></tr></table>` },
    { name: "ARIA table headers", render: (value: string) => `<table><tr><td role="rowheader"><strong>Due Date</strong></td><td>${value}</td><td role="columnheader">Available from</td><td>2026-10-01</td></tr></table>` },
    { name: "unmarked table fields", render: (value: string) => `<table><tr><td>Due Date</td><td>${value}</td><td>Available from</td><td>2026-10-01</td></tr></table>` },
    { name: "table row boundary", render: (value: string) => `<table><tr><th>Due Date</th><td>${value}</td></tr><tr><th>Available from</th><td>2026-10-01</td></tr></table>` },
    { name: "native next label within value", render: (value: string) => `<span class="label">Due Date</span><span class="value">${value}<label>Available from</label><span>2026-10-01</span></span>` },
    { name: "ARIA next label within value", render: (value: string) => `<span class="label">Due Date</span><span class="value">${value}<span role="rowheader">Available from</span><span>2026-10-01</span></span>` },
    { name: "own-text label with child value", render: (value: string) => `<div>Due Date<span class="value">${value}</span><span class="label">Available from</span><span class="value">2026-10-01</span></div>` },
    { name: "missing value node", render: (value: string) => `<div><span class="label">Due Date</span>${value ? `<span class="value">${value}</span>` : ""}<span class="label">Available from</span><span class="value">2026-10-01</span></div>` },
    { name: "pure label wrappers", render: (value: string) => `<div><div><span class="label"><strong>Due Date</strong></span></div>${value ? `<span class="value">${value}</span>` : ""}<span data-role="label">Available from</span><span class="value">2026-10-01</span></div>` },
  ];
  const fieldValues = [
    { name: "empty", html: "", due: null, unread: true },
    { name: "unset", html: "Not set", due: null, unread: false },
    { name: "date", html: "2026-12-31", due: "2026-12-31", unread: false },
    { name: "split date and time", html: "<strong>2026-12-31</strong><br><span>23:59</span>", due: "2026-12-31 23:59", unread: false },
    { name: "date punctuation across nested nodes", html: "<strong>2026</strong>-<span>12</span>-<span>31</span>", due: "2026-12-31", unread: false },
  ];
  it.each(fieldLayouts.flatMap((layout) => fieldValues.map((value) => ({ layout: layout.name, ...value, fields: layout.render(value.html) }))))(
    "bounds the $name due value within $layout through final export", async ({ fields, due, unread }) => {
      const detail = parseAssignmentDetailHtml(`<h1>Upload Assignment: HW</h1><section id="assignmentInfo">${fields}<div class="vtbegenerated">Submit the report.<a href="/bbcswebdav/pid-1-dt-content-rid-2/HW.txt">HW.txt</a></div></section>`, detailUrl);
      expect(detail.due_raw).toBe(due);
      expect(Boolean(detail.due_unread)).toBe(unread);
      const read = vi.fn().mockResolvedValue({ bytes: new TextEncoder().encode("Teacher attachment text."), contentType: "text/plain" });
      const [result] = await enrichSourceAttachments([enrichAssignmentSource(source(), detailUrl, detail)], read);
      const record = toRecognitionRequests([result])[0].records[0];
      expect(record.text_quality).toBe(unread ? "partial" : "complete");
      expect(record.upstream_hints.assignment).toMatchObject({ due_raw: due });
      expect(record.upstream_hints.references[0]).toMatchObject({ content_read: !unread, read_status: unread ? "failed" : "complete" });
      expect(record.upstream_hints.references[1].content_read).toBe(true);
      expect(record.text).toContain("Teacher attachment text.");
      expect(JSON.stringify(record)).not.toContain("Available from");
      expect(JSON.stringify(record)).not.toContain("2026-10-01");
    },
  );
  it.each([
    '<table><tr><th>Due Date</th><th>Available from</th><td>2026-10-01</td></tr></table>',
    '<table><tr><th><strong>Due Date</strong></th></tr><tr><th>Available from</th><td>2026-10-01</td></tr></table>',
  ])("keeps a missing table value cell unread: %s", async (fields) => {
    const detail = parseAssignmentDetailHtml(`<h1>Upload Assignment: HW</h1><section id="assignmentInfo">${fields}<div class="vtbegenerated">Submit the report.</div></section>`, detailUrl);
    expect(detail.due_raw).toBeNull();
    expect(detail.due_unread).toBe(true);
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(source(), detailUrl, detail)], vi.fn());
    const record = toRecognitionRequests([result])[0].records[0];
    expect(record.text_quality).toBe("partial");
    expect(record.upstream_hints.references[0]).toMatchObject({ content_read: false, read_status: "failed" });
    expect(JSON.stringify(record)).not.toContain("2026-10-01");
  });
  it.each([false, true])("retains multiple dates while flagging conflicts=%s", (conflict) => {
    const second = conflict ? "2027-01-01" : "2026-12-31";
    const detail = parseAssignmentDetailHtml(`<h1>Upload Assignment: HW</h1><section id="assignmentInfo"><div><span>Due Date</span><span>2026-12-31</span></div><div><span>Due Date</span><span>${second}</span></div><div class="vtbegenerated">Teacher instructions.</div></section>`, detailUrl);
    expect(detail.due_raw).toBe(conflict ? "2026-12-31 / 2027-01-01" : "2026-12-31");
    const result = enrichAssignmentSource(source(), detailUrl, detail);
    expect(result.text_quality).toBe(conflict ? "partial" : "complete");
    expect(result.body).toContain(second);
  });
  it("keeps nested long teacher text once and preserves independent equal fragments and references", () => {
    const longText = "a".repeat(60_000);
    const detail = parseAssignmentDetailHtml(`<h1>Upload Assignment: HW</h1><div id="assignmentInfo"><div class="vtbegenerated">${longText}<div data-assignment-description>Nested instruction.<img src="/nested.png"></div></div></div><div data-assignment-description><p>Independent introduction.</p><div class="assignmentInfo"><div class="vtbegenerated">Nested instruction.<a href="/independent.txt">Independent.txt</a></div></div></div>`, detailUrl);
    expect(detail.description.length).toBeLessThan(61_000);
    expect(detail.description.split(longText)).toHaveLength(2);
    expect(detail.description.match(/Nested instruction\./g)).toHaveLength(2);
    expect(detail.references?.map((ref) => ref.kind)).toEqual(["image", "attachment"]);
    const result = enrichAssignmentSource(source(), detailUrl, detail);
    expect(result.body).toContain(longText);
    expect(result.references[0]).toMatchObject({ kind: "detail", content_read: true, reason: null });
    expect(toRecognitionRequests([result])[0].records[0].text.length).toBeLessThan(61_000);
  });
  it("exports a direct date as complete and does not treat labels in teacher prose as metadata", async () => {
    const detail = parseAssignmentDetailHtml('<h2>Assignment Information</h2><section id="assignmentInfo"><span>Due Date</span><span>2026-12-31</span><div class="vtbegenerated">Submit the report.<span>Due Date</span><span>Read chapter 12.</span></div></section>', detailUrl);
    expect(detail.due_raw).toBe("2026-12-31");
    expect(detail.due_unread).toBeUndefined();
    const [result] = await enrichSourceAttachments([enrichAssignmentSource(source(), detailUrl, detail)], vi.fn());
    const record = toRecognitionRequests([result])[0].records[0];
    expect(record.text_quality).toBe("complete");
    expect(record.text).toContain("到期日期：2026-12-31");
    expect(record.text).toContain("Read chapter 12.");
    expect(record.upstream_hints.references[0]).toMatchObject({ content_read: true, read_status: "complete" });
  });
});
