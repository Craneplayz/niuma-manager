import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import pino from "pino";
import { describe, expect, it } from "vitest";
import { crawlSources } from "../src/crawler.js";
import type { EventCandidate, FetchResult, SourceAdapter } from "../src/core/types.js";

const candidate = (id: string, url: string): EventCandidate => ({
  source_id: "fixture-source",
  source_item_id: id,
  source_url: url,
  title_raw: "Same event",
  time_raw: "2026-09-25 14:00",
  location_raw: "Room 1",
  discovered_from_url: url,
  fetched_at: "2026-09-22T00:00:00.000Z",
});

describe("crawl orchestration", () => {
  it("isolates page failures and removes strong normalized duplicates", async () => {
    const root = await mkdtemp(join(tmpdir(), "niuma-crawler-test-"));
    const fetcher = {
      async fetch(request: { url: string }): Promise<FetchResult> {
        if (request.url.endsWith("/bad")) return { requestedUrl: request.url, finalUrl: request.url, status: 503, statusText: "Unavailable", headers: {}, contentType: "text/html", body: "temporarily unavailable", contentHash: "bad", fetchedAt: "2026-09-22T00:00:00.000Z", durationMs: 1 };
        return { requestedUrl: request.url, finalUrl: request.url, status: 200, statusText: "OK", headers: {}, contentType: "text/html", body: "<html>event</html>", contentHash: "good", fetchedAt: "2026-09-22T00:00:00.000Z", durationMs: 1 };
      },
    };
    const adapter: SourceAdapter = {
      metadata: { id: "fixture-source", name: "fixture", allowedOrigins: ["https://fixture.test"], seedUrls: ["https://fixture.test/a", "https://fixture.test/b", "https://fixture.test/bad"], maxPages: 5 },
      isAllowedUrl: (url) => url.startsWith("https://fixture.test/"),
      discover: () => ({ urls: [] }),
      parse: (document) => [candidate(document.requestedUrl.endsWith("/a") ? "one" : "two", document.requestedUrl)],
    };
    try {
      const summary = await crawlSources([adapter], fetcher, { logger: pino({ enabled: false }), rawRoot: join(root, "raw"), extractedRoot: join(root, "extracted"), normalizedRoot: join(root, "normalized") });
      expect(summary.pagesFetched).toBe(2);
      expect(summary.pagesFailed).toBe(1);
      expect(summary.normalizedEventsProduced).toBe(1);
      expect(summary.duplicatesRemoved).toBe(1);
      expect(summary.failures.some((failure) => failure.category === "http_error")).toBe(true);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });

  it("applies the future-only time filter to normalized output", async () => {
    const root = await mkdtemp(join(tmpdir(), "niuma-crawler-time-filter-test-"));
    const url = "https://fixture.test/past";
    const fetcher = {
      async fetch(request: { url: string }): Promise<FetchResult> {
        return { requestedUrl: request.url, finalUrl: request.url, status: 200, statusText: "OK", headers: {}, contentType: "text/html", body: "<html>event</html>", contentHash: "past", fetchedAt: "2026-09-22T00:00:00.000Z", durationMs: 1 };
      },
    };
    const adapter: SourceAdapter = {
      metadata: { id: "fixture-time-filter", name: "fixture", allowedOrigins: ["https://fixture.test"], seedUrls: [url], maxPages: 1 },
      isAllowedUrl: (candidateUrl) => candidateUrl.startsWith("https://fixture.test/"),
      discover: () => ({ urls: [] }),
      parse: (document) => [{ ...candidate("past", document.requestedUrl), time_raw: "2026-09-20 14:00" }],
    };
    try {
      const summary = await crawlSources([adapter], fetcher, { logger: pino({ enabled: false }), rawRoot: join(root, "raw"), extractedRoot: join(root, "extracted"), normalizedRoot: join(root, "normalized"), timeFilterAsOf: "2026-09-22T12:00:00+08:00" });
      expect(summary.normalizedEventsProduced).toBe(0);
      expect(summary.pastEventsFiltered).toBe(1);
      expect(summary.unresolvedEventsFiltered).toBe(0);
      expect((await readFile(summary.extractedOutputLocation, "utf8")).trim()).not.toBe("");
      expect((await readFile(summary.normalizedOutputLocation, "utf8")).trim()).toBe("");
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
});
