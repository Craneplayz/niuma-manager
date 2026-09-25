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
