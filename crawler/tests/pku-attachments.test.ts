import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { describe, expect, it, vi } from "vitest";
import { enrichSourceAttachments, extractAttachmentText, isTeacherAttachmentUrl, MAX_ATTACHMENT_BYTES } from "../src/pku-attachments.js";
import { extractCourseSourcesFromHtml } from "../src/pku-source.js";
import { enrichAssignmentSource, parseAssignmentDetailHtml } from "../src/pku-assignment.js";
import { toTeachingRequests } from "../src/pku-teaching-evidence.js";
import { toRecognitionRequests } from "../src/pku-recognition.js";

const url = "https://course.pku.edu.cn/bbcswebdav/pid-1-dt-content-rid-2/HW1.txt";
function source(label = "HW1.txt", href = url, body = "请按附件要求提交。") {
  return extractCourseSourcesFromHtml(`<ul id="content_listContainer"><li id="a"><div class="item"><h3>HW1</h3></div><div class="details"><p>${body}</p><a href="${href}">${label}</a></div></li></ul>`, { course: "Synthetic", section: "作业", sourceUrl: "https://course.pku.edu.cn/course?course_id=c1", collectedAt: "2026-10-07T12:00:00+08:00" })[0];
}
function textFile(text: string) { return { bytes: new TextEncoder().encode(text), contentType: "text/plain" }; }

/** A small real PDF without images, generated entirely from synthetic text. */
function pdf(text: string, blankPage = false, visual?: "stencil" | "bad-image" | "image" | "repeated-image", embedded = false) {
  const drawing = visual ? (visual === "repeated-image" ? Array.from({ length: 12 }, (_, i) => `q 20 0 0 20 ${i * 22} 40 cm /Im1 Do Q`).join("\n") : "q 100 0 0 100 50 400 cm /Im1 Do Q") : "";
  const stream = `BT /F1 12 Tf 50 700 Td (${text}) Tj ET\n${drawing}`;
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", `<< /Type /Pages /Kids [3 0 R${blankPage ? " 6 0 R" : ""}] /Count ${blankPage ? 2 : 1} >>`, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> ${visual ? "/XObject << /Im1 6 0 R >>" : ""} >> /Contents 5 0 R >>`, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`, ...(visual ? [visual === "stencil" ? "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ImageMask true /BitsPerComponent 1 /Length 1 >>\nstream\n\x00\nendstream" : `<< /Type /XObject /Subtype /Image /Width ${visual === "bad-image" ? 0 : 1} /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\nA\nendstream`] : blankPage ? ["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"] : [])];
  if (embedded) {
    const id = objects.length + 1;
    const instructions = "SYNTHETIC_EMBEDDED_INSTRUCTIONS: Due 2026-12-31";
    objects[0] = `<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << /Names [(instructions.txt) ${id} 0 R] >> >> >>`;
    objects.push(`<< /Type /Filespec /F (instructions.txt) /EF << /F ${id + 1} 0 R >> >>`, `<< /Type /EmbeddedFile /Length ${instructions.length} >>\nstream\n${instructions}\nendstream`);
  }
  let document = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, i) => { offsets.push(document.length); document += `${i + 1} 0 obj\n${object}\nendobj\n`; });
  const start = document.length;
  document += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`;
  return { bytes: new TextEncoder().encode(document), contentType: "application/pdf" };
}

describe("teacher attachment acquisition", () => {
  it.each(["https://evil.test/bbcswebdav/pid-1-dt-content-rid-2/a.pdf", "https://course.pku.edu.cn/webapps/assignment/download?file=private", "https://course.pku.edu.cn/bbcswebdav/users/a/a.pdf", "https://course.pku.edu.cn/bbcswebdav/pid-1-dt-attachment-rid-2/a.pdf", "https://course.pku.edu.cn/bbcswebdav/courses/c1/submissions/a.pdf"])("rejects unsafe/private attachment %s", (href) => {
    expect(isTeacherAttachmentUrl(href)).toBe(false);
  });
  it("reads text with explicit attachment provenance and retains stable item identity", async () => {
    const original = source();
    const read = vi.fn().mockResolvedValue(textFile("Submit HW1 before 2026-12-31 18:00."));
    const [result] = await enrichSourceAttachments([original], read);
    expect(result.input_id).toBe(original.input_id);
    expect(result.body).toContain("附件正文：HW1.txt\nSubmit HW1 before 2026-12-31 18:00.");
    expect(result.references[0]).toMatchObject({ read_status: "complete", content_read: true, reason: null });
    expect(result.text_quality).toBe("complete");
    expect(toRecognitionRequests([result])[0].records[0].upstream_hints.references[0].text).toContain("2026-12-31");
  });
  // Two isolated parsers can each take up to 15s, including cold startup on Windows.
  it("reads an actual text PDF and keeps a blank/scanned page partial", async () => {
    const complete = await extractAttachmentText(pdf("Submit by 2026-12-31."), "pdf");
    expect(complete.text).toContain("Submit by 2026-12-31.");
    expect(complete.partial).toBe(false);
    expect((await extractAttachmentText(pdf("Submit report.", true), "pdf")).partial).toBe(true);
  }, 35_000);
  it("parses the same real PDF repeatedly without taking down the caller", async () => {
    const file = pdf("Submit by 2026-12-31.");
    for (let i = 0; i < 10; i++) {
      expect(await extractAttachmentText(file, "pdf")).toEqual({ text: "Submit by 2026-12-31.", partial: false });
    }
  }, 30_000);
  it("also completes consecutive PDF parses in a standalone Node caller outside Vitest", () => {
    const script = `
      import { readFileSync } from "node:fs";
      const { extractAttachmentText } = await import(${JSON.stringify(new URL("../src/pku-attachments.ts", import.meta.url).href)});
      const file = { bytes: new Uint8Array(readFileSync(0)), contentType: "application/pdf" };
      for (let i = 0; i < 10; i++) {
        const result = await extractAttachmentText(file, "pdf");
        if (result.text !== "Submit by 2026-12-31." || result.partial) throw new Error("wrong PDF result");
      }
      console.log("10 consecutive PDF parses passed");
    `;
    const result = spawnSync(process.execPath, ["--import", "tsx", "--input-type=module", "--eval", script], {
      cwd: new URL("../", import.meta.url), input: pdf("Submit by 2026-12-31.").bytes, encoding: "utf8", timeout: 30_000,
    });
    expect(result.error).toBeUndefined();
    expect(result.signal).toBeNull();
    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout.trim()).toBe("10 consecutive PDF parses passed");
  }, 35_000);
  it("exports multiple distinct PDFs and continues after a broken PDF", async () => {
    const sources = ["First", "Broken", "Second", "Third"].map((name, index) => ({
      ...source(`${name}.pdf`, url.replace("HW1.txt", `${name}.pdf`)), input_id: `synthetic:pdf-sequence:${index}`,
    }));
    const read = vi.fn(async (href: string) => href.includes("Broken")
      ? { bytes: new TextEncoder().encode("%PDF-1.4\nnot a valid PDF"), contentType: "application/pdf" }
      : pdf(href.includes("First") ? "Submit first." : href.includes("Second") ? "Submit second." : "Submit third."));
    const results = await enrichSourceAttachments(sources, read);
    expect(read).toHaveBeenCalledTimes(4);
    expect(results[1].references[0]).toMatchObject({ read_status: "failed", reason: "parse_failed", content_read: false });
    for (const index of [0, 2, 3]) {
      expect(results[index].references[0]).toMatchObject({ read_status: "complete", reason: null, content_read: true });
    }
    const records = toRecognitionRequests(results)[0].records;
    expect(records[0].text).toContain("Submit first.");
    expect(records[2].text).toContain("Submit second.");
    expect(records[3].text).toContain("Submit third.");
  }, 30_000);
  it("can parse independent PDFs concurrently without sharing native parser state", async () => {
    const results = await Promise.all(["First", "Second", "Third"].map((name) => extractAttachmentText(pdf(`Submit ${name}.`), "pdf")));
    expect(results).toEqual(["First", "Second", "Third"].map((name) => ({ text: `Submit ${name}.`, partial: false })));
  }, 30_000);
  it("keeps document-level embedded files unread through the real PDF worker and export", async () => {
    const file = pdf("Submit report; see embedded file.", false, undefined, true);
    const extracted = await extractAttachmentText(file, "pdf");
    expect(extracted).toMatchObject({ partial: true });
    expect(extracted.text).toContain("Submit report; see embedded file.");
    expect(extracted.text).not.toContain("SYNTHETIC_EMBEDDED_INSTRUCTIONS");
    const [result] = await enrichSourceAttachments([source("Homework.pdf", url.replace("txt", "pdf"))], async () => file);
    expect(result.references[0]).toMatchObject({ content_read: false, read_status: "partial", reason: "partial_pdf" });
    const record = toRecognitionRequests([result])[0].records[0];
    expect(record.text_quality).toBe("partial");
    expect(JSON.stringify(record)).not.toContain("SYNTHETIC_EMBEDDED_INSTRUCTIONS");
  }, 35_000);
  it.each(["stencil", "bad-image", "image", "repeated-image"] as const)("keeps %s graphics/degraded PDFs partial through the real worker and export", async (visual) => {
    const file = pdf("Submit report by 2026-12-31.", false, visual);
    const extracted = await extractAttachmentText(file, "pdf");
    expect(extracted.text).toContain("Submit report by 2026-12-31.");
    expect(extracted.partial).toBe(true);
    const [result] = await enrichSourceAttachments([source("Homework.pdf", url.replace("txt", "pdf"))], async () => file);
    expect(result.text_quality).toBe("partial");
    expect(result.references[0]).toMatchObject({ content_read: false, read_status: "partial", reason: "partial_pdf" });
    expect(toRecognitionRequests([result])[0].records[0].text_quality).toBe("partial");
  }, 35_000);
  it("detects PDF behind an opaque URL without a filename, caches it and preserves exported evidence", async () => {
    const href = "https://course.pku.edu.cn/bbcswebdav/pid-1-dt-content-rid-2/xid-2_1";
    const original = extractCourseSourcesFromHtml(`<ul id="content_listContainer"><li id="material"><div class="item"><h3><a href="${href}">下载附件</a></h3></div><div class="details"></div></li></ul>`, { course: "Synthetic", section: "教学内容", sourceUrl: "https://course.pku.edu.cn/course?course_id=c1", collectedAt: "2026-10-07T12:00:00+08:00" })[0];
    const read = vi.fn().mockResolvedValue(pdf("Submit report.", false, "image"));
    const [result, repeated] = await enrichSourceAttachments([original, source("另一标题", href)], read);
    expect(result.input_id).toBe(original.input_id);
    expect(result.references[0]).toMatchObject({ url: href, read_status: "partial", content_read: false, reason: "partial_pdf" });
    expect(repeated.references[0].label).toBe("另一标题");
    expect(result.body).toContain("Submit report.");
    expect(result.body_evidence).toEqual(original.body_evidence);
    const record = toTeachingRequests([result]).requests[0].records[0].record;
    expect(record.body_status).toBe("summary");
    expect(record.references[0]).toMatchObject({ read_state: "partial", text: "Submit report." });
    expect(read).toHaveBeenCalledOnce();
  }, 20_000);
  it("reads a direct file preview reference without an extension", async () => {
    const href = "https://course.pku.edu.cn/webapps/blackboard/execute/content/file?cmd=view&content_id=_1_1&course_id=_2_1";
    const original = source("教师材料", href);
    expect(original.references[0].kind).toBe("attachment");
    const [result] = await enrichSourceAttachments([original], async () => textFile("Submit report."));
    expect(result.references[0]).toMatchObject({ url: href, read_status: "complete", text: "Submit report." });
  });
  it("recognizes PDF bytes with a generic MIME but leaves unknown binary formats unsupported", async () => {
    const href = url.replace("HW1.txt", "xid-2_1");
    const file = { ...pdf("Submit report."), contentType: "application/octet-stream" };
    const [result] = await enrichSourceAttachments([source("教师材料", href)], async () => file);
    expect(result.references[0].read_status).toBe("complete");
    for (const contentType of ["application/octet-stream", "application/zip"]) {
      const [unsupported] = await enrichSourceAttachments([source("教师材料", href)], async () => ({ bytes: new TextEncoder().encode("binary"), contentType }));
      expect(unsupported.references[0]).toMatchObject({ read_status: "unsupported", reason: "unsupported_format" });
    }
  }, 20_000);
  it("does not reuse a format-specific failure for a different format", async () => {
    const href = "https://course.pku.edu.cn/bbcswebdav/pid-1-dt-content-rid-2/xid-2_1";
    const read = vi.fn().mockResolvedValue(pdf("Submit report."));
    const [bad, known, repeated] = await enrichSourceAttachments([source("Homework.txt", href), source("Homework.pdf", href), source("Same.pdf", href)], read);
    expect(bad.references[0].content_read).toBe(false);
    expect(known.references[0].read_status).toBe("complete");
    expect(repeated.references[0]).toMatchObject({ read_status: "complete", label: "Same.pdf" });
    expect(read).toHaveBeenCalledTimes(2);
  }, 20_000);
  it("caches repeated attachments across records within one run", async () => {
    const read = vi.fn().mockResolvedValue(textFile("Submit report."));
    await enrichSourceAttachments([source(), source()], read);
    expect(read).toHaveBeenCalledOnce();
  });
  it.each(["txt", "pdf"] as const)("preserves cached %s text after both fresh and cached per-record overflows", async (format) => {
    const text = "SYNTHETIC_REQUIRED_DEADLINE_2026_12_31 ".repeat(6);
    const file = format === "txt" ? textFile(text) : pdf(text, false, "image");
    const href = url.replace("txt", format);
    const sources = [99_950, 20, 99_950, 20].map((size, index) => ({ ...source(`HW${index}.${format}`, href, "a".repeat(size)), input_id: `synthetic:cache-overflow:${index}` }));
    const read = vi.fn().mockResolvedValue(file);
    const results = await enrichSourceAttachments(sources, read);
    for (const index of [0, 2]) {
      expect(results[index].references[0]).toMatchObject({ read_status: "failed", content_read: false, reason: "too_large" });
      expect(results[index].references[0].text).toBeUndefined();
      expect(results[index].text_quality).toBe("partial");
    }
    for (const index of [1, 3]) {
      expect(results[index].references[0]).toMatchObject({ label: `HW${index}.${format}`, read_status: format === "txt" ? "complete" : "partial", reason: format === "txt" ? null : "partial_pdf" });
      expect(results[index].references[0].text).toContain("SYNTHETIC_REQUIRED_DEADLINE");
      expect(results[index].body).toContain("SYNTHETIC_REQUIRED_DEADLINE");
    }
    const records = toRecognitionRequests(results)[0].records;
    expect(records[1].text_quality).toBe(format === "txt" ? "complete" : "partial");
    expect(records[3].text).toContain("SYNTHETIC_REQUIRED_DEADLINE");
    expect(read).toHaveBeenCalledOnce();
  }, 20_000);
  it("keeps ZIP and external links unread without making a request", async () => {
    const read = vi.fn();
    const results = await enrichSourceAttachments([source("HW1.zip", url.replace("txt", "zip")), source("HW1.txt", "https://evil.test/HW1.txt")], read);
    expect(read).not.toHaveBeenCalled();
    expect(results.map((r) => r.references[0].reason)).toEqual(["unsupported_format", "untrusted_attachment_url"]);
    expect(results.every((r) => r.text_quality === "partial")).toBe(true);
  });
  it("keeps invalid attachment URLs unread without interrupting other sources", async () => {
    const read = vi.fn().mockResolvedValue(textFile("Submit report."));
    const bad = source("Homework.pdf");
    bad.references[0].url = "";
    const [invalid, valid] = await enrichSourceAttachments([bad, source()], read);
    expect(invalid.references[0].reason).toBe("untrusted_attachment_url");
    expect(invalid.text_quality).toBe("partial");
    expect(valid.references[0].read_status).toBe("complete");
    expect(read).toHaveBeenCalledOnce();
  });
  it("sanitizes errors and keeps source evidence for manual review", async () => {
    const original = source();
    const [result] = await enrichSourceAttachments([original], async () => { throw new Error("private cookie/token"); });
    expect(result.body).toBe(original.body);
    expect(result.text_quality).toBe("partial");
    expect(result.references[0].read_status).toBe("failed");
    expect(JSON.stringify(result)).not.toContain("private cookie/token");
  });
  it.each([textFile(""), textFile("\u0000binary"), { bytes: new Uint8Array(MAX_ATTACHMENT_BYTES + 1), contentType: "text/plain" }, { bytes: new TextEncoder().encode("<html>Login</html>"), contentType: "text/html" }])("rejects empty, binary, oversized and login responses", async (file) => {
    const [result] = await enrichSourceAttachments([source()], async () => file);
    expect(result.references[0].content_read).toBe(false);
    expect(result.text_quality).toBe("partial");
  });
  it("keeps multiple large attachments within the v0.2 record limit", async () => {
    const original = source();
    original.references.push({ ...original.references[0], url: url.replace("rid-2", "rid-3") });
    const [result] = await enrichSourceAttachments([original], async () => textFile("a".repeat(50_000)));
    expect(result.text_quality).toBe("partial");
    expect(result.references[1].reason).toBe("too_large");
    expect(toRecognitionRequests([result])[0].records[0].text.length).toBeLessThanOrEqual(100_000);
  });
  it("does not turn a missing teacher description into complete based on a due date", async () => {
    const original = { ...source(), body: "到期日期：2026-12-31", text_quality: "partial" as const, references: [] };
    expect((await enrichSourceAttachments([original], vi.fn()))[0].text_quality).toBe("partial");
  });
});

it("reproduces the shared synthetic source-to-text examples", async () => {
  const fixture = JSON.parse(await readFile(new URL("../../contracts/source-to-text/teaching-source-snapshot-v02-cases.json", import.meta.url), "utf8")) as { cases: { id: string; html: string; course_entry?: { text: string; url: string }; detail_html?: string; detail_url?: string; request: ReturnType<typeof toRecognitionRequests>[number]; expected_text_quality: string; teaching_export: ReturnType<typeof toTeachingRequests> }[] };
  for (const example of fixture.cases) {
    const expected = example.request.records[0];
    let sources = extractCourseSourcesFromHtml(example.html, { course: expected.course_or_project!, section: expected.upstream_hints.section, sourceUrl: expected.source_url, collectedAt: expected.collected_at, courseEntry: example.course_entry });
    if (example.detail_html && example.detail_url) sources = sources.map((source) => enrichAssignmentSource(source, example.detail_url!, parseAssignmentDetailHtml(example.detail_html!, example.detail_url)));
    if (example.id === "text-attachment-read") sources = await enrichSourceAttachments(sources, async () => textFile("请在2026年12月31日18点前提交报告。"));
    if (example.id === "download-failed") sources = await enrichSourceAttachments(sources, async () => { throw new Error("synthetic transport failure"); });
    expect(toRecognitionRequests(sources, example.request.request_id)[0], example.id).toEqual(example.request);
    expect(toTeachingRequests(sources, example.request.request_id), example.id).toEqual(example.teaching_export);
  }
});
