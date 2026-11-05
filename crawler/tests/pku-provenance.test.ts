import { describe, expect, it } from "vitest";
import { collectCourseProvenance, extractCourseSourcesFromHtml } from "../src/pku-source.js";
import { sourceSnapshotId, toRecognitionRequests } from "../src/pku-recognition.js";
const context = { course: "Editable 26-27学年第1学期", section: "作业", sourceUrl: "https://course.pku.edu.cn/list?course_id=_1_1", collectedAt: "2026-10-09T12:00:00+08:00" };
const entry = { text: "26-27学年第1学期：测试课程", url: "https://course.pku.edu.cn/webapps/blackboard/execute/launcher?id=_1_1&url=x&token=private" };
const record = () => extractCourseSourcesFromHtml('<ul id="content_listContainer"><li><div class="item" id="hw1"><h3>作业</h3></div><div class="details">10月23日交</div></li></ul>', { ...context, courseEntry: entry })[0];
describe("course evidence and snapshot handoff", () => {
  it("preserves observed course identity without granting current-assignment scope", () => {
    const p = collectCourseProvenance({ ...context, courseEntry: entry });
    expect(p.course_id).toBe("_1_1");
    expect(p.source_metadata_text).toBe(entry.text);
    expect(p.term_label).toBe("26-27学年第1学期");
    expect(p.course_entry_url).not.toContain("private");
    expect(p.applies_to).toBe("unknown");
  });
  it.each([undefined, { ...entry, url: entry.url.replace("_1_1", "_2_1") }, { ...entry, url: entry.url.replace("course.pku.edu.cn", "other.test") }])("rejects absent or unrelated course-entry evidence", (courseEntry) => {
    expect(collectCourseProvenance({ ...context, courseEntry }).term_label).toBeNull();
    expect(collectCourseProvenance({ ...context, courseEntry }).source_metadata_text).toBe("");
  });
  it("retains conflicting labels without inventing semester boundaries", () => {
    const p = collectCourseProvenance({ ...context, courseEntry: { ...entry, text: entry.text + " 25-26学年第2学期" } });
    expect(p.term_label).toBeNull();
    expect(p.applies_to).toBe("unknown");
    expect(p).not.toHaveProperty("starts_on");
  });
  it("exports evidence and final content version without trust flags", () => {
    const s = record(); const output = toRecognitionRequests([s])[0].records[0];
    expect(output.upstream_hints.provenance).toEqual(s.provenance);
    expect(output.upstream_hints.snapshot_id).toBe(sourceSnapshotId(s));
    expect(output.published_at.value).toBeNull();
    expect(JSON.stringify(output)).not.toContain("source_verified");
  });
  it("binds body, metadata, publication, collection and reference read state", () => {
    const s = record(); const id = sourceSnapshotId(s);
    for (const change of [ { body: "changed" }, { collected_at: "2026-10-10T12:00:00+08:00" }, { provenance: { ...s.provenance!, source_metadata_text: "changed" } }, { references: [{ label: "PDF", url: "https://course.pku.edu.cn/a.pdf", kind: "attachment" as const, content_read: false, read_status: "failed" as const, reason: "parse_failed" }] }, { published_at: { value: "2026-10-01", precision: "date" as const, basis: "source" as const } } ]) {
      expect(sourceSnapshotId({ ...s, ...change })).not.toBe(id);
    }
    expect(sourceSnapshotId(Object.fromEntries(Object.entries(s).reverse()) as typeof s)).toBe(id);
  });
});
