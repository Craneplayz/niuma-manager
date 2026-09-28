import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import type { Logger } from "pino";
import type { EventCandidate, CrawlFailure, CrawlSummary, Fetcher, RawDocument, SourceAdapter } from "./core/types.js";
import { canonicalizeUrl } from "./core/utils.js";
import { FetchError } from "./fetcher/public-http-fetcher.js";
import { RawSnapshotStore } from "./storage/raw-snapshot-store.js";
import { writeJsonl } from "./storage/jsonl-store.js";
import { normalizeCandidate } from "./normalizer.js";
import { filterFutureEvents, resolveTimeFilterAsOf } from "./core/time-filter.js";

export interface CrawlOptions {
  runId?: string;
  rawRoot?: string;
  extractedRoot?: string;
  normalizedRoot?: string;
  snapshotStore?: RawSnapshotStore;
  logPath?: string;
  timeFilterAsOf?: string;
  logger: Logger;
}

export async function crawlSources(adapters: SourceAdapter[], fetcher: Fetcher, options: CrawlOptions): Promise<CrawlSummary> {
  const runId = options.runId ?? makeRunId();
  const rawRoot = options.rawRoot ?? "data/raw";
  const extractedPath = join(options.extractedRoot ?? "data/extracted", `${runId}.jsonl`);
  const normalizedPath = join(options.normalizedRoot ?? "data/normalized", `${runId}.jsonl`);
  const snapshotStore = options.snapshotStore ?? new RawSnapshotStore(rawRoot);
  const timeFilterAsOf = resolveTimeFilterAsOf(options.timeFilterAsOf);
  const candidates: EventCandidate[] = [];
  const failures: CrawlFailure[] = [];
  const seenCandidateKeys = new Set<string>();
  const candidateIndexes = new Map<string, number>();
  const normalized: NonNullable<ReturnType<typeof normalizeCandidate>["event"]>[] = [];
  const normalizedKeys = new Set<string>();
  let pagesRequested = 0;
  let pagesFetched = 0;
  let pagesFailed = 0;
  let duplicatesRemoved = 0;
  let sourcesCompleted = 0;

  for (const adapter of adapters) {
    const queue = adapter.metadata.seedUrls.map((url) => ({ url, discoveredFromUrl: undefined as string | undefined }));
    const enqueued = new Set<string>();
    let sourceFailed = false;
    while (queue.length && enqueued.size < adapter.metadata.maxPages) {
      const item = queue.shift()!;
      let url: string;
      try { url = canonicalizeUrl(item.url); } catch { continue; }
      if (enqueued.has(url) || !adapter.isAllowedUrl(url)) continue;
      enqueued.add(url);
      pagesRequested += 1;
      options.logger.info({ source_id: adapter.metadata.id, url }, "page_requested");
      let result;
      try {
        result = await fetcher.fetch({ url, sourceId: adapter.metadata.id });
      } catch (error) {
        sourceFailed = true;
        pagesFailed += 1;
        const failure = toFailure(error, adapter.metadata.id, url, runId);
        failures.push(failure);
        options.logger.error(failure, "page_failed");
        continue;
      }
      const document: RawDocument = { ...result, sourceId: adapter.metadata.id, crawlRunId: runId, discoveredFromUrl: item.discoveredFromUrl };
      try {
        await snapshotStore.persist(document);
      } catch (error) {
        const failure = failureFor("persistence_error", adapter.metadata.id, url, runId, error);
        failures.push(failure); options.logger.error(failure, "snapshot_failed");
      }
      if (result.status < 200 || result.status >= 400) {
        sourceFailed = true; pagesFailed += 1;
        const failure = failureFor("http_error", adapter.metadata.id, url, runId, `${result.status} ${result.statusText}`);
        failures.push(failure); options.logger.error(failure, "page_failed");
        continue;
      }
      pagesFetched += 1;
      let discovered;
      try { discovered = adapter.discover(document); }
      catch (error) {
        sourceFailed = true;
        const failure = failureFor("discovery_error", adapter.metadata.id, url, runId, error);
        failures.push(failure); options.logger.error(failure, "discovery_failed");
        discovered = { urls: [] };
      }
      for (const discoveredUrl of discovered.urls) if (adapter.isAllowedUrl(discoveredUrl.url)) queue.push({ url: discoveredUrl.url, discoveredFromUrl: discoveredUrl.discoveredFromUrl });
      try {
        const pageCandidates = adapter.parse(document);
        for (const candidate of pageCandidates) {
          const key = `${candidate.source_id}|${candidate.source_item_id ?? candidate.source_url}`;
          if (seenCandidateKeys.has(key)) {
            const previousIndex = candidateIndexes.get(key);
            if (previousIndex !== undefined && candidateRichness(candidate) > candidateRichness(candidates[previousIndex])) candidates[previousIndex] = candidate;
            duplicatesRemoved += 1;
            continue;
          }
          seenCandidateKeys.add(key);
          candidateIndexes.set(key, candidates.length);
          candidates.push(candidate);
        }
      } catch (error) {
        sourceFailed = true;
        const failure = failureFor("parse_error", adapter.metadata.id, url, runId, error);
        failures.push(failure); options.logger.error(failure, "parse_failed");
      }
    }
    if (!sourceFailed) sourcesCompleted += 1;
  }

  for (const candidate of candidates) {
    const result = normalizeCandidate(candidate);
    if (!result.event) {
      const failure = failureFor("validation_error", candidate.source_id, candidate.source_url, runId, result.warnings.join("; "));
      failures.push(failure); options.logger.warn(failure, "candidate_validation_failed");
      continue;
    }
    const key = `${result.event.source.source_id}|${result.event.title.toLocaleLowerCase()}|${result.event.start_time ?? ""}|${result.event.location ?? ""}`;
    if (normalizedKeys.has(key)) { duplicatesRemoved += 1; continue; }
    normalizedKeys.add(key);
    normalized.push(result.event);
  }
  const timeFiltered = filterFutureEvents(normalized, timeFilterAsOf);
  await writeJsonl(extractedPath, candidates);
  await writeJsonl(normalizedPath, timeFiltered.events);
  options.logger.info({ candidates: candidates.length, normalized: timeFiltered.events.length, past_events_filtered: timeFiltered.pastEvents, unresolved_events_filtered: timeFiltered.unresolvedEvents, failures: failures.length }, "crawl_completed");
  return {
    runId,
    sourcesAttempted: adapters.length,
    sourcesCompleted,
    pagesRequested,
    pagesFetched,
    pagesFailed,
    candidatesFound: candidates.length,
    normalizedEventsProduced: timeFiltered.events.length,
    timeFilterAsOf: timeFilterAsOf.toISO({ suppressMilliseconds: true }) ?? timeFilterAsOf.toISO()!,
    pastEventsFiltered: timeFiltered.pastEvents,
    unresolvedEventsFiltered: timeFiltered.unresolvedEvents,
    validationFailures: failures.filter((failure) => failure.category === "validation_error").length,
    duplicatesRemoved,
    rawSnapshotLocation: join(rawRoot, "<source>", runId),
    extractedOutputLocation: extractedPath,
    normalizedOutputLocation: normalizedPath,
    logLocation: options.logPath ?? `logs/${runId}.jsonl`,
    failures,
  };
}

export async function reprocessRun(runId: string, adapters: SourceAdapter[], options: { rawRoot?: string; extractedRoot?: string; normalizedRoot?: string; logPath?: string; timeFilterAsOf?: string; logger: Logger }): Promise<CrawlSummary> {
  const rawRoot = options.rawRoot ?? "data/raw";
  const timeFilterAsOf = resolveTimeFilterAsOf(options.timeFilterAsOf);
  const candidates: EventCandidate[] = [];
  const failures: CrawlFailure[] = [];
  const seen = new Set<string>();
  const candidateIndexes = new Map<string, number>();
  const normalizedKeys = new Set<string>();
  let duplicatesRemoved = 0;
  let validationFailures = 0;
  let sourcesCompleted = 0;
  let pagesFetched = 0;
  for (const adapter of adapters) {
    const directory = join(rawRoot, adapter.metadata.id, runId);
    let files: string[];
    try { files = await readdir(directory); } catch { continue; }
    if (files.some((file) => file.endsWith(".json"))) sourcesCompleted += 1;
    for (const metadataFile of files.filter((file) => file.endsWith(".json"))) {
      try {
        const metadata = JSON.parse(await readFile(join(directory, metadataFile), "utf8")) as Record<string, unknown>;
        const htmlFile = String(metadata.raw_html_file ?? join(directory, metadataFile.replace(/\.json$/, ".html")));
        const body = await readFile(htmlFile, "utf8");
        const document = { ...metadataToDocument(metadata, body, runId, adapter.metadata.id), discoveredFromUrl: typeof metadata.discovered_from_url === "string" ? metadata.discovered_from_url : undefined };
        pagesFetched += 1;
        for (const candidate of adapter.parse(document)) {
          const key = `${candidate.source_id}|${candidate.source_item_id ?? candidate.source_url}`;
          if (seen.has(key)) {
            const previousIndex = candidateIndexes.get(key);
            if (previousIndex !== undefined && candidateRichness(candidate) > candidateRichness(candidates[previousIndex])) candidates[previousIndex] = candidate;
            duplicatesRemoved += 1;
          } else {
            seen.add(key); candidateIndexes.set(key, candidates.length); candidates.push(candidate);
          }
        }
      } catch (error) {
        failures.push(failureFor("parse_error", adapter.metadata.id, metadataFile, runId, error));
      }
    }
  }
  const normalized: NonNullable<ReturnType<typeof normalizeCandidate>["event"]>[] = [];
  for (const candidate of candidates) {
    const result = normalizeCandidate(candidate);
    if (!result.event) { validationFailures += 1; continue; }
    const key = `${result.event.source.source_id}|${result.event.title.toLocaleLowerCase()}|${result.event.start_time ?? ""}|${result.event.location ?? ""}`;
    if (normalizedKeys.has(key)) { duplicatesRemoved += 1; continue; }
    normalizedKeys.add(key);
    normalized.push(result.event);
  }
  const extractedPath = join(options.extractedRoot ?? "data/extracted", `${runId}-reprocessed.jsonl`);
  const normalizedPath = join(options.normalizedRoot ?? "data/normalized", `${runId}-reprocessed.jsonl`);
  const timeFiltered = filterFutureEvents(normalized, timeFilterAsOf);
  await writeJsonl(extractedPath, candidates);
  await writeJsonl(normalizedPath, timeFiltered.events);
  return { runId, sourcesAttempted: adapters.length, sourcesCompleted, pagesRequested: pagesFetched, pagesFetched, pagesFailed: failures.length, candidatesFound: candidates.length, normalizedEventsProduced: timeFiltered.events.length, timeFilterAsOf: timeFilterAsOf.toISO({ suppressMilliseconds: true }) ?? timeFilterAsOf.toISO()!, pastEventsFiltered: timeFiltered.pastEvents, unresolvedEventsFiltered: timeFiltered.unresolvedEvents, validationFailures, duplicatesRemoved, rawSnapshotLocation: join(rawRoot, "<source>", runId), extractedOutputLocation: extractedPath, normalizedOutputLocation: normalizedPath, logLocation: options.logPath ?? `logs/${runId}-reprocessed.jsonl`, failures };
}

function makeRunId(): string { return `${new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}-${Math.random().toString(36).slice(2, 8)}`; }

function toFailure(error: unknown, sourceId: string, url: string, runId: string): CrawlFailure {
  const category = error instanceof FetchError ? error.category : "network_error";
  return failureFor(category, sourceId, url, runId, error);
}

function failureFor(category: CrawlFailure["category"], sourceId: string, url: string, runId: string, error: unknown): CrawlFailure {
  return { category, sourceId, url, message: error instanceof Error ? error.message : String(error), crawlRunId: runId, occurredAt: new Date().toISOString() };
}

function metadataToDocument(metadata: Record<string, unknown>, body: string, runId: string, sourceId: string): RawDocument {
  return {
    sourceId,
    crawlRunId: runId,
    requestedUrl: String(metadata.requested_url),
    finalUrl: String(metadata.final_url),
    status: Number(metadata.status),
    statusText: String(metadata.status_text ?? ""),
    headers: (metadata.headers ?? {}) as Record<string, string>,
    contentType: typeof metadata.content_type === "string" ? metadata.content_type : null,
    body,
    contentHash: String(metadata.content_hash),
    fetchedAt: String(metadata.fetched_at),
    durationMs: Number(metadata.duration_ms ?? 0),
  };
}

function candidateRichness(candidate: EventCandidate): number {
  return [candidate.description_raw, candidate.time_raw, candidate.location_raw, candidate.organizer_raw, candidate.speaker_raw, candidate.publication_time_raw, candidate.host_raw, candidate.moderator_raw].filter(Boolean).length;
}
