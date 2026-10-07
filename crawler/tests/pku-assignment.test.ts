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
    expect(detail).toEqual({ state: "unknown", history_only: true, title: "人工智能基础 第一次概念题作业", description: "", due_raw: null });
    const original = source();
    const enriched = enrichAssignmentSource(original, detailUrl, detail);
    expect(enriched.input_id).toBe(original.input_id);
    expect(enriched.body).toBe("外层作业说明");
    expect(enriched.text_quality).toBe("partial");
    expect(enriched.references[0].reason).toBe("submission_history_only");
    expect(enriched.assignment).toEqual({ state: "unknown", due_raw: null });
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
