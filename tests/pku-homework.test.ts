import { DateTime } from "luxon";
import { describe, expect, it } from "vitest";
import { currentAcademicTerm, filterUpcomingHomeworks, parseHomeworkDeadline, type HomeworkRecord } from "../src/pku-homework.js";

const asOf = DateTime.fromISO("2026-09-22T13:46:00+08:00");

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
