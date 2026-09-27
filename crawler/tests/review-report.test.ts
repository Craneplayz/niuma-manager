import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { writeReviewReport } from "../src/review-report.js";
import { writeJsonl } from "../src/storage/jsonl-store.js";

describe("review report", () => {
  it("writes a standalone escaped inspection page from normalized events", async () => {
    const root = await mkdtemp(join(tmpdir(), "niuma-review-test-"));
    const event = {
      id: "fixture-source:item-1",
      title: "A <checked> event",
      description: null,
      start_time: "2026-09-25T14:00:00+08:00",
      end_time: null,
      timezone: "Asia/Shanghai",
      location: "Room 1",
      organizers: [],
      speakers: ["A Speaker"],
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
      event_type: "lecture",
      event_type_raw: "Lecture",
      publication_time: null,
      source: { source_id: "fixture-source", source_url: "https://fixture.test/event/1", source_item_id: "item-1" },
      raw: { time: "2026-09-25 14:00", location: "Room 1", organizer: null, speaker: "A Speaker" },
      parse: { status: "ok", warnings: [], time_resolution: "exact" },
    };
    try {
      await writeJsonl(join(root, "normalized", "run-1.jsonl"), [event]);
      const report = await writeReviewReport("run-1", { normalizedRoot: join(root, "normalized"), outputRoot: join(root, "review") });
      const html = await readFile(report.outputPath, "utf8");
      expect(report.eventCount).toBe(1);
      expect(html).toContain("A \\u003cchecked> event");
      expect(html).toContain("Show provenance and raw fields");
      expect(html).toContain("not wired into the real app frontend");
      expect(html).not.toContain("<checked> event");
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
});
