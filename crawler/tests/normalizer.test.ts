import { describe, expect, it } from "vitest";
import { normalizedEventSchema } from "../src/core/schemas.js";
import { normalizeCandidate } from "../src/normalizer.js";
import type { EventCandidate } from "../src/core/types.js";

const candidate: EventCandidate = { source_id: "test", source_item_id: "id-1", source_url: "https://example.test/event/1", title_raw: "  A seminar  ", time_raw: "2026年9月25日 14:00", location_raw: " Room 1 ", organizer_raw: "Org A, Org B", discovered_from_url: "https://example.test", fetched_at: "2026-09-22T00:00:00.000Z" };

describe("normalization", () => {
  it("creates stable schema-valid events and preserves provenance", () => {
    const one = normalizeCandidate(candidate).event!;
    const two = normalizeCandidate(candidate).event!;
    expect(one.id).toBe(two.id);
    expect(one.title).toBe("A seminar");
    expect(one.organizers).toEqual(["Org A", "Org B"]);
    expect(one.source.source_url).toBe(candidate.source_url);
    expect(normalizedEventSchema.safeParse(one).success).toBe(true);
  });
  it("keeps ambiguous times inspectable", () => {
    const result = normalizeCandidate({ ...candidate, time_raw: "待定" });
    expect(result.event?.start_time).toBeNull();
    expect(result.event?.parse.time_resolution).toBe("unresolved");
    expect(result.event?.parse.warnings.length).toBeGreaterThan(0);
  });
});


it("preserves publication time without substituting it for a pending event", () => {
  const event = normalizeCandidate({ ...candidate, time_raw: "时间待定", publication_time_raw: "2026-09-25" }).event!;
  expect(event).toMatchObject({ start_time: null, end_time: null, publication_time: "2026-09-25T00:00:00+08:00", raw: { time: "时间待定" }, parse: { time_resolution: "unresolved" } });
});
