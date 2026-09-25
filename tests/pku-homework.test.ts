import { DateTime } from "luxon";
import { describe, expect, it } from "vitest";
import { buildHomeworkScrapeResult, currentAcademicTerm, extractHomeworkRecords, filterUpcomingHomeworks, parseHomeworkDeadline, type HomeworkRecord } from "../src/pku-homework.js";

const asOf = DateTime.fromISO("2026-09-22T13:46:00+08:00");
const context = { course: "Synthetic course", section: "课程作业", sourceUrl: "https://example.test/course", asOf };

describe("homework record boundaries", () => {
  it("keeps adjacent assignments and their deadlines separate", () => {
    const records = extractHomeworkRecords("课程作业\n作业一：阅读报告\n截止日期：2026-09-26 23:59\n作业二：实验报告\n截止日期：2026-10-10 23:59", context);
    expect(records.map(({ title, deadline }) => ({ title, deadline }))).toEqual([
      { title: "作业一：阅读报告", deadline: "2026-09-26T23:59:00+08:00" },
      { title: "作业二：实验报告", deadline: "2026-10-10T23:59:00+08:00" },
    ]);
    expect(records[0].description).not.toContain("作业二");
  });

  it("does not borrow the next assignment's deadline for an undated one", () => {
    const records = extractHomeworkRecords("作业一：阅读报告\n时间待定\n作业二：实验报告\n截止日期：2026-10-10 23:59", context);
    expect(records[0]).toMatchObject({ deadline: null, deadline_raw: null, status: "no_deadline" });
    expect(records[1].deadline).toBe("2026-10-10T23:59:00+08:00");
  });

  it("preserves repeated dates and repeated titles in separate records", () => {
    const records = extractHomeworkRecords("作业：练习\n截止日期：2026-09-26\n作业：练习\n截止日期：2026-10-10\n作业三\n截止日期：2026-09-26", context);
    expect(records.map((record) => record.deadline)).toEqual([
      "2026-09-26T23:59:59+08:00", "2026-10-10T23:59:59+08:00", "2026-09-26T23:59:59+08:00",
    ]);
  });

  it("handles a separate deadline field without crossing record boundaries", () => {
    const records = extractHomeworkRecords("Homework A\nDue date:\n2026-09-26 23:59\nHomework B\nDue date:\nHomework C\nDeadline: 2026-10-10", context);
    expect(records.map((record) => record.deadline)).toEqual([
      "2026-09-26T23:59:00+08:00", null, "2026-10-10T23:59:59+08:00",
    ]);
  });

  it("keeps conflicting dates inspectable instead of guessing a deadline", () => {
    const records = extractHomeworkRecords("作业一\n截止日期：2026-09-26\n截止日期：2026-10-10", context);
    expect(records[0]).toMatchObject({ deadline: null, status: "no_deadline" });
    expect(records[0].deadline_raw).toContain("2026-09-26");
    expect(records[0].deadline_raw).toContain("2026-10-10");
  });

  it("retains a separate title after a generic homework heading", () => {
    const records = extractHomeworkRecords("作业\n第一章\n截止日期：2026-09-26\n作业\n第二章\n截止日期：2026-10-10", context);
    expect(records.map(({ title, deadline }) => ({ title, deadline }))).toEqual([
      { title: "第一章", deadline: "2026-09-26T23:59:59+08:00" },
      { title: "第二章", deadline: "2026-10-10T23:59:59+08:00" },
    ]);
  });
});

describe("PKU homework time filtering", () => {
  it("uses the current PKU academic term", () => {
    expect(currentAcademicTerm(asOf)).toBe("26-27学年第1学期");
  });

  it("parses date-only deadlines as the end of the deadline day", () => {
    expect(parseHomeworkDeadline("截止日期：2026-09-22", asOf)?.toISO({ suppressMilliseconds: true })).toBe("2026-09-22T23:59:59+08:00");
  });

  it("does not turn ordinary text into a deadline through publication-date context", () => {
    expect(parseHomeworkDeadline("确定", asOf)).toBeNull();
    expect(parseHomeworkDeadline("发布时间：2026年9月8日", asOf)).not.toBeNull();
  });

  it("treats 24:00 as the following day's midnight", () => {
    expect(parseHomeworkDeadline("9月21日 24:00前", asOf)?.toISO({ suppressMilliseconds: true })).toBe("2026-09-22T00:00:00+08:00");
  });

  it("keeps only records strictly later than the scrape time", () => {
    const records: HomeworkRecord[] = [
      { course: "A", section: "课程作业", title: "future", description: null, source_url: "https://example.test/a", deadline_raw: "2026-09-25 23:59", deadline: "2026-09-25T23:59:00+08:00", status: "upcoming" },
      { course: "A", section: "课程作业", title: "past", description: null, source_url: "https://example.test/a", deadline_raw: "2026-09-20 23:59", deadline: "2026-09-20T23:59:00+08:00", status: "past" },
      { course: "A", section: "课程作业", title: "undated", description: null, source_url: "https://example.test/a", deadline_raw: null, deadline: null, status: "no_deadline" },
    ];
    expect(filterUpcomingHomeworks(records, asOf).map((record) => record.title)).toEqual(["future"]);
  });
});

describe("reported homework regressions", () => {
  it("keeps a yearless January deadline upcoming in late December", () => {
    const records = extractHomeworkRecords("作业一\n截止：1月5日18:00", { ...context, asOf: DateTime.fromISO("2026-12-31T12:00:00+08:00") });
    expect(records[0]).toMatchObject({ deadline: "2027-01-05T18:00:00+08:00", status: "upcoming" });
  });
  it("retains raw deadline text when parsing fails", () => {
    const records = extractHomeworkRecords("作业一\n截止：时间待定\n作业二\n截止：下周某天", context);
    expect(records.map((record) => record.deadline_raw)).toEqual(["截止：时间待定", "截止：下周某天"]);
    expect(records.every((record) => record.deadline === null)).toBe(true);
  });
});


describe("complete homework output", () => {
  it("serializes all records, with upcoming as a separate view", () => {
    const records = extractHomeworkRecords([
      "作业一", "时间待定",
      "作业二", "截止：2026-09-26", "截止：2026-10-10",
      "作业三", "截止：下周某天",
      "作业四", "截止：2026-09-01",
      "作业五", "截止：2026-09-30 18时30分",
    ].join("\n"), context);
    const result = JSON.parse(JSON.stringify(buildHomeworkScrapeResult(records, { asOf, term: null, coursesChecked: 1, sectionsChecked: 1 })));
    expect(result.records).toEqual(records);
    expect(result).toMatchObject({ records_found: 5, past_records: 1, undated_records: 3 });
    expect(result.upcoming).toEqual([records[4]]);
    expect(result.records[0].description).toContain("时间待定");
    expect(result.records[1].deadline_raw).toContain("2026-10-10");
    expect(result.records[2].deadline_raw).toBe("截止：下周某天");
  });
  it("does not collapse distinct undated assignments with the same title", () => {
    const records = extractHomeworkRecords("作业一\n截止：待定\n作业一\n截止：下周某天", context);
    const result = buildHomeworkScrapeResult([...records, records[0]], { asOf, term: null, coursesChecked: 1, sectionsChecked: 1 });
    expect(result.records).toEqual(records);
  });
  it("does not choose a parsed date when another deadline remains unresolved", () => {
    const records = extractHomeworkRecords("作业一\n截止：2026-09-30\n截止：另行通知", context);
    expect(records[0]).toMatchObject({ deadline: null, status: "no_deadline" });
    expect(records[0].deadline_raw).toContain("另行通知");
  });
  it("keeps recent past dates past across the year boundary", () => {
    expect(parseHomeworkDeadline("12月31日18:00", DateTime.fromISO("2027-01-02T12:00:00+08:00"))?.toISO({ suppressMilliseconds: true })).toBe("2026-12-31T18:00:00+08:00");
    expect(parseHomeworkDeadline("2026年1月5日18:00", DateTime.fromISO("2026-12-31T12:00:00+08:00"))?.year).toBe(2026);
  });
});
