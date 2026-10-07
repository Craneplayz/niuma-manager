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
