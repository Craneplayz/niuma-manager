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
  it("targeted diagnosis never enables a course/section scan", async () => {
    scrape.mockResolvedValue({ source_records: sources });
    const args = ['--source-page=https://course.pku.edu.cn/webapps/blackboard/execute/announcement?course_id=_1_1&method=search',
      '--source-item=_2_1', '--course-label=Synthetic', '--section-label=课程通知'];
    await run(args);
    expect(scrape).toHaveBeenCalledWith({term:undefined, allSections:false,
      targetSource:{url:args[0].slice('--source-page='.length),itemId:'_2_1',course:'Synthetic',section:'课程通知'}});
    scrape.mockReset();
    vi.resetModules();
    await run([...args, '--all-sections']);
    expect(scrape).not.toHaveBeenCalled();
  });
  it("explicit teaching format selects the integration collection scope", async () => {
    const teachingSources = extractCourseSourcesFromHtml('<ul id="content_listContainer"><li id="a"><div class="item"><h3>教材</h3></div><div class="details">请提前阅读</div></li></ul>', { course: "Synthetic", section: "资料", sourceUrl: "https://course.pku.edu.cn/list?course_id=c1", collectedAt: "2026-09-26T09:00:00+08:00" });
    scrape.mockResolvedValue({source_records:teachingSources,records:[],upcoming:[]});
    const {log,error}=await run(['--format=teaching','--request-id=teaching-cli']);
    const result=JSON.parse(log.mock.calls[0][0]);
    expect(result.contract_version).toBe('0.4');
    expect(result.records[0].record.text).toContain('请提前阅读');
    expect(scrape).toHaveBeenCalledWith({term:undefined,allSections:false,teachingSections:true});
    expect(error).not.toHaveBeenCalled();
  });
  it("exports all source records, even when the homework views are empty", async () => {
    scrape.mockResolvedValue({ source_records: sources, records: [], upcoming: [] });
    const { log, error } = await run(["--", "--format=recognition", "--request-id=test:cli", "--term=26-27学年第1学期"]);
    const result = JSON.parse(log.mock.calls[0][0]);
    expect(result.records).toHaveLength(1);
    expect(result.records[0].source_label).toBe("教材");
    expect(result.request_id).toBe("test:cli");
    expect(scrape).toHaveBeenCalledWith({ term: "26-27学年第1学期", allSections: false });
    expect(error).not.toHaveBeenCalled();
  });
  it("exports v0.4 and reports unrepresentable sources without dropping valid records", async () => {
    const valid = extractCourseSourcesFromHtml('<ul id="content_listContainer"><li><div class="item" id="v"><h3>作业</h3></div><div class="details">请交报告</div></li></ul>', { course: "Synthetic", section: "作业", sourceUrl: "https://course.pku.edu.cn/list?course_id=c1", collectedAt: "2026-10-09T09:00:00+08:00" });
    scrape.mockResolvedValue({ source_records: [...sources, ...valid] });
    const { log, error } = await run(["--format=teaching-v04", "--request-id=v04"]);
    expect(JSON.parse(log.mock.calls[0][0])).toMatchObject({ contract_version: "0.4", request_id: "v04:1" });
    expect(JSON.parse(log.mock.calls[0][0]).records).toHaveLength(1);
    expect(JSON.parse(error.mock.calls[0][0])).toMatchObject({ type: "teaching_export_skipped", reason: "stable_course_identity_missing" });
    expect(process.exitCode).toBe(2);
  });
  it("does not emit a successful empty batch when all source identities are missing", async () => {
    scrape.mockResolvedValue({ source_records: sources });
    const { log, error } = await run(["--format=teaching-v04"]);
    expect(log).not.toHaveBeenCalled(); expect(error).toHaveBeenCalled(); expect(process.exitCode).toBe(2);
  });
  it("retains raw JSON as the default", async () => {
    const result = { source_records: sources, records: [], upcoming: [] };
    scrape.mockResolvedValue(result);
    const { log } = await run(["--all-terms"]);
    expect(JSON.parse(log.mock.calls[0][0])).toEqual(result);
    expect(scrape).toHaveBeenCalledWith({ term: null, allSections: false });
  });
  it("passes the full-section compatibility flag to the crawler", async () => {
    const result = { source_records: sources, records: [], upcoming: [] };
    scrape.mockResolvedValue(result);
    await run(["--all-sections"]);
    expect(scrape).toHaveBeenCalledWith({ term: undefined, allSections: true });
  });
  it("shows help without logging in", async () => {
    const { log } = await run(["--help"]);
    expect(scrape).not.toHaveBeenCalled();
    const help = log.mock.calls.map(call => call[0]).join('\n');
    expect(help).toContain("JSONL");
    expect(help).toContain("--all-sections");
    expect(help).toContain("--source-item");
  });
  it.each([["--format=bad"], ["--term="], ["--all-terms", "--term=term"]])("rejects invalid arguments before logging in: %j", async (...args) => {
    const { error } = await run(args);
    expect(scrape).not.toHaveBeenCalled();
    expect(error).toHaveBeenCalled();
    expect(process.exitCode).toBe(1);
  });
});
