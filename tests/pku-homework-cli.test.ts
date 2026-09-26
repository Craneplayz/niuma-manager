import { afterEach, describe, expect, it, vi } from "vitest";
import { extractCourseSourcesFromHtml } from "../src/pku-source.js";

const { scrape } = vi.hoisted(() => ({ scrape: vi.fn() }));
vi.mock("../src/pku-homework.js", () => ({ scrapePkuHomeworks: scrape }));
const argv = process.argv;
const exitCode = process.exitCode;
afterEach(() => {
  process.argv = argv;
  process.exitCode = exitCode;
  vi.restoreAllMocks();
  vi.resetModules();
  scrape.mockReset();
});

const sources = extractCourseSourcesFromHtml('<ul id="content_listContainer"><li id="a"><div class="item"><h3>教材</h3></div><div class="details">请提前阅读</div></li></ul>', { course: "Synthetic", section: "资料", sourceUrl: "https://example.test/course", collectedAt: "2026-09-26T09:00:00+08:00" });
async function run(args: string[]) {
  process.argv = ["node", "cli", ...args];
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  await import("../src/pku-homework-cli.js");
  return { log, error };
}

describe("campus source export CLI", () => {
  it("exports all source records, even when the homework views are empty", async () => {
    scrape.mockResolvedValue({ source_records: sources, records: [], upcoming: [] });
    const { log, error } = await run(["--", "--format=recognition", "--request-id=test:cli", "--term=26-27学年第1学期"]);
    const result = JSON.parse(log.mock.calls[0][0]);
    expect(result.records).toHaveLength(1);
    expect(result.records[0].source_label).toBe("教材");
    expect(result.request_id).toBe("test:cli");
    expect(scrape).toHaveBeenCalledWith({ term: "26-27学年第1学期" });
    expect(error).not.toHaveBeenCalled();
  });
  it("retains raw JSON as the default", async () => {
    const result = { source_records: sources, records: [], upcoming: [] };
    scrape.mockResolvedValue(result);
    const { log } = await run(["--all-terms"]);
    expect(JSON.parse(log.mock.calls[0][0])).toEqual(result);
    expect(scrape).toHaveBeenCalledWith({ term: null });
  });
  it("shows help without logging in", async () => {
    const { log } = await run(["--help"]);
    expect(scrape).not.toHaveBeenCalled();
    expect(log.mock.calls[0][0]).toContain("JSONL");
  });
  it.each([["--format=bad"], ["--term="], ["--all-terms", "--term=term"]])("rejects invalid arguments before logging in: %j", async (...args) => {
    const { error } = await run(args);
    expect(scrape).not.toHaveBeenCalled();
    expect(error).toHaveBeenCalled();
    expect(process.exitCode).toBe(1);
  });
});
