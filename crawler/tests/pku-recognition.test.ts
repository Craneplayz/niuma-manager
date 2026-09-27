import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { extractCourseSourcesFromHtml } from "../src/pku-source.js";
import { toRecognitionRequests } from "../src/pku-recognition.js";

function source() {
  return extractCourseSourcesFromHtml('<ul id="content_listContainer"><li id="item1"><div class="item"><h3>阅读材料</h3></div><div class="details"><p>请阅读第一章。</p><p>时间待定</p><a href="/book.pdf">教材</a></div></li></ul>', { course: "Synthetic course", section: "资料", sourceUrl: "https://example.test/content", collectedAt: "2026-09-26T09:00:00+08:00" })[0];
}

describe("v0.2 source conversion", () => {
  it("maps full source text and metadata without inventing publication dates or deadlines", () => {
    const original = source();
    const snapshot = structuredClone(original);
    const [request] = toRecognitionRequests([original], "test:request");
    const record = request.records[0];
    expect(request).toMatchObject({ contract_version: "0.2", request_id: "test:request" });
    expect(record).toMatchObject({ input_id: original.input_id, text: `${original.title}\n\n${original.body}`, text_quality: "partial", platform: "teaching_web", published_at: { value: null, precision: "unknown", basis: "unknown" }, timezone_basis: "caller_default", course_or_project: "Synthetic course", context: [] });
    expect(record).not.toHaveProperty("deadline");
    record.upstream_hints.references[0].label = "changed";
    expect(original).toEqual(snapshot);
  });
  it("batches more than 100 records without dropping any, retaining stable IDs", () => {
    const sources = Array.from({ length: 201 }, (_, i) => ({ ...source(), input_id: `test:${i}` }));
    const requests = toRecognitionRequests(sources, "run");
    expect(requests.map((r) => r.records.length)).toEqual([100, 100, 1]);
    expect(requests.map((r) => r.request_id)).toEqual(["run:1", "run:2", "run:3"]);
    expect(requests.flatMap((r) => r.records.map((x) => x.input_id))).toEqual(sources.map((r) => r.input_id));
  });
  it("rejects duplicate IDs and oversized text instead of silently changing evidence", () => {
    expect(() => toRecognitionRequests([source(), source()])).toThrow("unique");
    expect(() => toRecognitionRequests([{ ...source(), body: "x".repeat(100_001) }])).toThrow("not truncated");
    expect(() => toRecognitionRequests([], " ")).toThrow("empty");
  });
  it("splits long Chinese sources by serialized UTF-8 bytes without losing evidence", () => {
    const original = source();
    const sources = Array.from({ length: 100 }, (_, i) => ({ ...original, input_id: `test:${i}`, body: "中".repeat(4000) }));
    const requests = toRecognitionRequests(sources, '中文"请求');
    expect(requests.length).toBeGreaterThan(1);
    expect(requests.map((r) => r.request_id)).toEqual(requests.map((_r, i) => `中文"请求:${i + 1}`));
    for (const request of requests) {
      expect(request.records.length).toBeLessThanOrEqual(100);
      expect(Buffer.byteLength(JSON.stringify(request), "utf8")).toBeLessThanOrEqual(2_000_000);
    }
    const records = requests.flatMap((r) => r.records);
    expect(records.map((r) => r.input_id)).toEqual(sources.map((r) => r.input_id));
    expect(records.every((r) => r.text === `${original.title}\n\n${sources[0].body}` && r.upstream_hints.raw.body === sources[0].body)).toBe(true);
  });
  it("counts escaped metadata and the envelope at the exact byte boundary", () => {
    const original = { ...source(), warnings: ['引号"\\\n'] };
    const requestId = '中文"请求';
    const [initial] = toRecognitionRequests([original], requestId);
    const padding = 2_000_000 - Buffer.byteLength(JSON.stringify(initial), "utf8");
    original.warnings[0] += "x".repeat(padding);
    const [request] = toRecognitionRequests([original], requestId);
    expect(request.request_id).toBe(requestId);
    expect(Buffer.byteLength(JSON.stringify(request), "utf8")).toBe(2_000_000);
    original.warnings[0] += "x";
    expect(() => toRecognitionRequests([original], requestId)).toThrow("not truncated");
  });
  it("includes batch suffixes in the size check and fails if one record cannot fit", () => {
    const original = { ...source(), warnings: [""] };
    const [initial] = toRecognitionRequests([original], "run");
    original.warnings[0] = "x".repeat(2_000_000 - Buffer.byteLength(JSON.stringify(initial), "utf8"));
    expect(() => toRecognitionRequests([original, { ...source(), input_id: "second" }], "run")).toThrow("not truncated");
  });
  it("rejects an oversized empty request envelope", () => {
    expect(() => toRecognitionRequests([], "x".repeat(2_000_000))).toThrow("byte limit");
  });
  it("exports an empty request and creates a fresh default request ID per run", () => {
    expect(toRecognitionRequests([], "empty")).toEqual([{ contract_version: "0.2", request_id: "empty", records: [] }]);
    expect(toRecognitionRequests([])[0].request_id).not.toBe(toRecognitionRequests([])[0].request_id);
  });
  it("passes the existing Python v0.2 validator without changing downstream code", () => {
    const requests = [...toRecognitionRequests([source()], "normal"), ...toRecognitionRequests([], "empty"), ...toRecognitionRequests([{ ...source(), text_quality: "missing", title: "", body: "" }], "missing")];
    const result = spawnSync(process.env.PYTHON ?? "python3", ["-c", "import json,sys; from ddl_text.contract_v02 import validate_request; [validate_request(r) for r in json.load(sys.stdin)]"], { cwd: fileURLToPath(new URL("../../", import.meta.url)), input: JSON.stringify(requests), encoding: "utf8" });
    expect(result.error).toBeUndefined();
    expect(result.stderr).toBe("");
    expect(result.status).toBe(0);
  });
});
