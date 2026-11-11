import { performance } from "node:perf_hooks";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { describe, expect, it, vi } from "vitest";
import { enrichSourceAttachments } from "../src/pku-attachments.js";
import { extractCourseSourcesFromHtml } from "../src/pku-source.js";
import { observeDownload, type DownloadObservation } from "../src/producer-observation.js";

const privateText = "PRIVATE_KEY_ACCOUNT_COOKIE_BODY";
const url = `https://course.pku.edu.cn/bbcswebdav/pid-1-dt-content-rid-2/${privateText}.txt`;
function source() {
  return extractCourseSourcesFromHtml(`<ul id="content_listContainer"><li><div class="item" id="1"><h3>${privateText}</h3></div><div class="details"><a href="${url}">HW.txt</a></div></li></ul>`,
    { course: privateText, section: "作业", sourceUrl: "https://course.pku.edu.cn/list?course_id=c1", collectedAt: "2026-10-20T09:00:00+08:00" })[0];
}
describe("download producer observations", () => {
  it("times actual reads once, excluding duplicate cache hits and parsing", async () => {
    const events: DownloadObservation[] = [];
    const clock = vi.spyOn(performance, "now").mockReturnValueOnce(100).mockReturnValueOnce(225.5);
    try {
      const read = vi.fn(async () => ({ bytes: new TextEncoder().encode(privateText), contentType: "text/plain" }));
      const result = await enrichSourceAttachments([source(), source()], read, undefined, e => { events.push(e); });
      expect(read).toHaveBeenCalledTimes(1);
      expect(events).toEqual([{ contract_version: "producer-observation/0.1", stage: "attachment_download", duration_ms: 125.5, result_code: "ok" }]);
      expect(result[0].references[0].text).toBe(privateText);
      expect(JSON.stringify(events)).not.toContain(privateText);
    } finally { clock.mockRestore(); }
    // Validate the actual TypeScript producer output against the shared schema.
    const schema = JSON.parse(readFileSync("../contracts/schemas/producer-observation-v0.1.schema.json", "utf8"));
    const checked = spawnSync(process.env.CONTRACT_PYTHON ?? "python", ["-c",
      "import json,sys; from jsonschema import Draft202012Validator; data=json.load(sys.stdin); [Draft202012Validator(data['schema']).validate(e) for e in data['events']]"],
    { input: JSON.stringify({ schema, events }), encoding: "utf8", timeout: 15_000, windowsHide: true });
    expect(checked.status, checked.stderr).toBe(0);
  });
  it("reports a fixed failure and preserves the original failure", async () => {
    const events: DownloadObservation[] = [];
    const error = new Error(privateText);
    await expect(observeDownload(async () => { throw error; }, e => { events.push(e); })).rejects.toBe(error);
    expect(events[0].result_code).toBe("download_failed");
    expect(JSON.stringify(events)).not.toContain(privateText);
  });
  it("ignores synchronous and asynchronous observer failures", async () => {
    expect(await observeDownload(async () => 42, () => { throw Error(privateText); })).toBe(42);
    expect(await observeDownload(async () => 42, async () => { throw Error(privateText); })).toBe(42);
    expect(await observeDownload(async () => 42)).toBe(42);
    const result = await enrichSourceAttachments([source()], async () => { throw Error(privateText); }, undefined, () => { throw Error(privateText); });
    expect(result[0].references[0].reason).toBe("download_failed");
  });
});
