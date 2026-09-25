import { describe, expect, it } from "vitest";
import { parseEventTime } from "../src/core/time.js";

describe("deterministic event times", () => {
  it("handles Chinese date and meridiem range", () => {
    expect(parseEventTime("8月17日 上午10:00-12:00", "发布时间：2026-08-03")).toMatchObject({ start: "2026-08-17T10:00:00+08:00", end: "2026-08-17T12:00:00+08:00", resolution: "context_inferred" });
  });
  it("handles dotted English date and am range", () => {
    expect(parseEventTime("2026.08.11 10:00 am – 11:00 am")).toMatchObject({ start: "2026-08-11T10:00:00+08:00", end: "2026-08-11T11:00:00+08:00", resolution: "exact" });
  });
  it("handles weekday month names and explicit date ranges", () => {
    expect(parseEventTime("15:00pm-17:00pm, Monday, August 17, 2026").start).toBe("2026-08-17T15:00:00+08:00");
    expect(parseEventTime("2026.07.23; 10:00-11:30").end).toBe("2026-07-23T11:30:00+08:00");
  });
  it("does not fabricate a year", () => {
    expect(parseEventTime("8月17日 13:00")).toMatchObject({ start: null, resolution: "unresolved" });
  });
  it("keeps date-only events all-day", () => {
    expect(parseEventTime("2026-10-16")).toMatchObject({ start: "2026-10-16T00:00:00+08:00", end: null, allDay: true });
  });
});

describe("reported time parsing regressions", () => {
  it.each(["时间待定", "待通知", "18:00", "地点另行通知"])("does not substitute publication date for %s", (raw) => {
    expect(parseEventTime(raw, "2026-09-25")).toMatchObject({ start: null, resolution: "unresolved" });
  });
  it.each(["18时30分", "18点30分", "18：30"])("preserves minutes in %s", (clock) => {
    expect(parseEventTime(`2026年9月30日${clock}`).start).toBe("2026-09-30T18:30:00+08:00");
  });
  it("does not interpret an assignment number as a clock", () => {
    expect(parseEventTime("2026年9月30日，作业2")).toMatchObject({ start: "2026-09-30T00:00:00+08:00", allDay: true });
  });
  it.each(["25:30", "18:75", "18:3", "18时半"])("keeps unsupported or invalid clock %s unresolved", (clock) => {
    expect(parseEventTime(`2026年9月30日 ${clock}`).start).toBeNull();
  });
  it("infers the nearest year across both year boundaries", () => {
    expect(parseEventTime("1月5日18:00", "2026-12-31")).toMatchObject({ start: "2027-01-05T18:00:00+08:00", resolution: "context_inferred" });
    expect(parseEventTime("12月31日18:00", "2027-01-02").start).toBe("2026-12-31T18:00:00+08:00");
    expect(parseEventTime("2026年1月5日18:00", "2026-12-31").start).toBe("2026-01-05T18:00:00+08:00");
  });
});
