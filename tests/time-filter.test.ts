import { DateTime } from "luxon";
import { describe, expect, it } from "vitest";
import { filterFutureEvents, resolveTimeFilterAsOf } from "../src/core/time-filter.js";
import { normalizedEventSchema, type NormalizedEvent } from "../src/core/schemas.js";

const asOf = DateTime.fromISO("2026-09-22T12:00:00+08:00");

function event(id: string, start_time: string | null): NormalizedEvent {
  return normalizedEventSchema.parse({
    id,
    title: id,
    description: null,
    start_time,
    end_time: null,
    timezone: "Asia/Shanghai",
    location: null,
    organizers: [],
    speakers: [],
    speaker_affiliation: null,
    host: null,
    moderator: null,
    registration_url: null,
    online_url: null,
    contact: null,
    language: null,
    tags: [],
    cancellation_status: null,
    all_day: false,
    event_type: "unknown",
    event_type_raw: null,
    publication_time: null,
    source: { source_id: "test", source_url: "https://example.test", source_item_id: id },
    raw: { time: null, location: null, organizer: null, speaker: null },
    parse: { status: start_time ? "ok" : "partial", warnings: [], time_resolution: start_time ? "exact" : "unresolved" },
  });
}

describe("global future-event filter", () => {
  it("resolves as-of values in the project timezone", () => {
    expect(resolveTimeFilterAsOf("2026-09-22T04:00:00Z").toISO()).toBe("2026-09-22T12:00:00.000+08:00");
  });

  it("keeps only events starting strictly after now", () => {
    const result = filterFutureEvents([
      event("future", "2026-09-25T10:00:00+08:00"),
      event("past", "2026-09-20T10:00:00+08:00"),
      event("unknown", null),
    ], asOf);
    expect(result.events.map((item) => item.id)).toEqual(["future"]);
    expect(result.pastEvents).toBe(1);
    expect(result.unresolvedEvents).toBe(1);
  });
});
