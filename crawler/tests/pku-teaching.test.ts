import { describe, expect, it, vi } from "vitest";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { extractCourseSourcesFromHtml } from "../src/pku-source.js";
import { enrichAssignmentSource, enrichAssignmentSources, parseAssignmentDetailHtml } from "../src/pku-assignment.js";
import { enrichSourceAttachments } from "../src/pku-attachments.js";
import { toTeachingRequests } from "../src/pku-teaching.js";
const base = "https://course.pku.edu.cn";
const detailUrl = `${base}/webapps/assignment/uploadAssignment?course_id=c1&content_id=hw`;
const txtUrl = `${base}/bbcswebdav/pid-1-dt-content-rid-2/HW.txt`;
function source(body = "请交报告，截止2026年12月31日。", assignment = false, id = "hw") {
  return extractCourseSourcesFromHtml(`<ul id="content_listContainer"><li><div class="item" id="${id}"><h3>${assignment ? `<a href="${detailUrl}">作业</a>` : "作业"}</h3></div><div class="details">${body}</div></li></ul>`, {
    course: "课程", section: "作业", sourceUrl: `${base}/list?course_id=c1`, collectedAt: "2026-10-09T12:00:00+08:00",
    courseEntry: { text: "26-27学年第1学期：课程", url: `${base}/webapps/blackboard/execute/launcher?id=c1&url=x` },
  })[0];
}
const record = (s: ReturnType<typeof source>) => toTeachingRequests([s]).requests[0].records[0].record;

describe("teaching v0.4 producer", () => {
  it("keeps body complete with an unread supplementary attachment and preserves the unknown term", () => {
    const s = source(`请交报告，截止2026年12月31日。附件供参考。<a href="${txtUrl}">HW.txt</a>`);
    expect(s.text_quality).toBe("partial");
    const r = record(s);
    expect(r.body_status).toBe("complete");
    expect(r.references[0]).toMatchObject({ read_state: "unread", text: null });
    expect(r.course_term).toBeNull(); expect(r.published_at).toBeNull();
    expect(r.source_metadata_text).toBe("26-27学年第1学期：课程");
    expect(JSON.stringify(r)).not.toContain("source_verified");
  });
  it("does not let attachment text become body evidence or complete a title-only source", async () => {
    const s = source(`<a href="${txtUrl}">HW.txt</a>`);
    const original = s.body_evidence!.text;
    const [enriched] = await enrichSourceAttachments([s], async () => ({ bytes: new TextEncoder().encode("ATTACHMENT_ONLY_ACTION: hand in by 2026-12-31"), contentType: "text/plain" }));
    const r = record(enriched);
    expect(enriched.body).toContain("ATTACHMENT_ONLY_ACTION");
    expect(r.text).toBe(original); expect(r.text).not.toContain("ATTACHMENT_ONLY_ACTION");
    expect(r.references[0].text).toContain("ATTACHMENT_ONLY_ACTION");
  });
  it("retains the body gap when only an image or attachment supplied task content", async () => {
    const s = source(`<img src="/notice.png"><a href="${txtUrl}"></a>`);
    expect(s.body_evidence!.status).toBe("summary");
    const [enriched] = await enrichSourceAttachments([s], async () => ({ bytes: new TextEncoder().encode("Submit by 2026-12-31"), contentType: "text/plain" }));
    expect(record(enriched).body_status).toBe("summary");
  });
  it("absorbs successfully read native teacher details once, leaving real attachments independent", () => {
    const s = source("列表摘要", true);
    const d = parseAssignmentDetailHtml(`<h1>上载作业：报告</h1><div data-assignment-description>DETAIL_ACTION: 请提交报告。<a href="${txtUrl}">补充.txt</a></div><div id="assignmentInfo"><div class="metaField"><span class="label">到期日期</span><span>2026-12-31</span></div></div>`, detailUrl);
    const r = record(enrichAssignmentSource(s, detailUrl, d));
    expect(r.text).toContain("DETAIL_ACTION"); expect(r.text).toContain("2026-12-31");
    expect(r.body_status).toBe("complete");
    expect(r.references).toHaveLength(1); expect(r.references[0].kind).toBe("attachment");
  });
  it.each(["unknown", "missing", "bad-date", "overflow"])("preserves incomplete detail %s", (kind) => {
    const s = source("列表文字", true);
    const d = kind === "unknown" ? { state: "unknown" as const, title: null, description: "", due_raw: null }
      : { state: "active" as const, title: "作业", description: kind === "missing" ? "" : kind === "overflow" ? "x".repeat(100_000) : "请提交报告", due_raw: null, ...(kind === "bad-date" ? { due_unread: true } : {}) };
    const r = record(enrichAssignmentSource(s, detailUrl, d));
    expect(r.body_status).toBe("partial"); expect(r.references[0].read_state).toBe("failed");
    expect(r.references[0].text).toBeNull();
  });
  it("retains failed native detail when the request throws", async () => {
    const [s] = await enrichAssignmentSources({ goto: vi.fn().mockRejectedValue(new Error("offline")), url: vi.fn(), locator: vi.fn(), content: vi.fn() }, [source("列表文字", true)]);
    expect(record(s).body_status).toBe("partial"); expect(record(s).references[0].read_state).toBe("failed");
  });
  it("preserves only the safe submission heading as partial reference text", () => {
    const s = source("列表文字", true);
    const d = parseAssignmentDetailHtml('<h1>Review Submission History: HW</h1><div class="grade">PRIVATE_GRADE</div><div class="attempt">PRIVATE_ANSWER</div>', detailUrl);
    const r = record(enrichAssignmentSource(s, detailUrl, d));
    expect(r.references[0]).toMatchObject({ read_state: "partial", text: "Review Submission History: HW" });
    expect(r.text).not.toContain("提交状态：已提交"); expect(JSON.stringify(r)).not.toContain("PRIVATE");
  });
  it("cleans only the display URL while preserving the source snapshot identity", () => {
    const s = { ...source(), source_url: `${base}/list?course_id=c1&signature=PRIVATE#item` };
    const output = toTeachingRequests([s]);
    expect(output.requests[0].records[0].source.url).toBe(`${base}/list?course_id=c1`);
    expect(output.requests[0].records[0].record.source_item_id).toBe(s.source_item_id);
    expect(output.requests[0].records[0].record.snapshot_id).not.toBe(record(source()).snapshot_id);
  });
  it("isolates absent evidence/identity, unread text inconsistency and oversized evidence", () => {
    const good = source();
    const bad = [ { ...source("old", false, "old"), body_evidence: undefined }, { ...source("id", false, "id"), source_item_id: null },
      { ...source("course", false, "course"), source_url: `${base}/list` },
      { ...source("large", false, "large"), body_evidence: { ...good.body_evidence!, text: "x".repeat(100_001) } },
      { ...source("ref", false, "ref"), references: [{ label: "ref", url: txtUrl, kind: "attachment" as const, content_read: true, read_status: "complete" as const, reason: null }] } ];
    const output = toTeachingRequests([...bad, good]);
    expect(output.skipped).toHaveLength(bad.length); expect(output.requests[0].records).toHaveLength(1);
    expect(output.requests[0].records[0].record.input_id).toBe(good.input_id);
  });
  it("rejects duplicate identities without choosing which snapshot to trust", () => {
    const a = source(); const output = toTeachingRequests([a, { ...a, body: "different" }]);
    expect(output.requests).toEqual([]); expect(output.skipped.map((s) => s.reason)).toEqual(["duplicate_input_id", "duplicate_input_id"]);
  });
  it("batches at twenty records and keeps the two-megabyte UTF-8 envelope bound", () => {
    const many = Array.from({ length: 41 }, (_, i) => source("x", false, `hw${i}`));
    expect(toTeachingRequests(many, "batch").requests.map((r) => r.records.length)).toEqual([20, 20, 1]);
    const large = many.slice(0, 10).map((s) => ({ ...s, body_evidence: { ...s.body_evidence!, text: "中".repeat(99_000) } }));
    const output = toTeachingRequests(large, "bytes");
    expect(output.skipped).toEqual([]); expect(output.requests.length).toBeGreaterThan(1);
    expect(output.requests.every((r) => Buffer.byteLength(JSON.stringify(r)) <= 2_000_000)).toBe(true);
  });
  it("validates the shared producer examples with the actual Python v0.4 schema and semantic checker", async () => {
    const fixture = JSON.parse(await readFile(new URL("../../contracts/source-to-text/teaching-source-snapshot-v02-cases.json", import.meta.url), "utf8"));
    const result = spawnSync(process.env.PYTHON ?? "python3", ["-m", "unittest", "contracts.tests.test_teaching_source_snapshot.TeachingSourceSnapshotTest.test_teaching_v04_exports"], { cwd: new URL("../../", import.meta.url), encoding: "utf8" });
    expect(result.status, result.stderr).toBe(0);
    expect(fixture.cases.every((c: { teaching_export: unknown }) => Boolean(c.teaching_export))).toBe(true);
  });
});
