export type FailureCategory =
  | "network_error"
  | "http_error"
  | "robots_disallowed"
  | "discovery_error"
  | "parse_error"
  | "validation_error"
  | "persistence_error";

export interface FetchRequest {
  url: string;
  sourceId?: string;
  timeoutMs?: number;
  maxResponseBytes?: number;
}

export interface FetchResult {
  requestedUrl: string;
  finalUrl: string;
  status: number;
  statusText: string;
  headers: Record<string, string>;
  contentType: string | null;
  body: string;
  contentHash: string;
  fetchedAt: string;
  durationMs: number;
}

export interface Fetcher {
  fetch(request: FetchRequest): Promise<FetchResult>;
}

export interface RawDocument extends FetchResult {
  sourceId: string;
  crawlRunId: string;
  discoveredFromUrl?: string;
}

export interface EventCandidate {
  source_id: string;
  source_item_id?: string;
  source_url: string;
  title_raw: string;
  description_raw?: string;
  time_raw?: string;
  location_raw?: string;
  organizer_raw?: string;
  speaker_raw?: string;
  speaker_affiliation_raw?: string;
  host_raw?: string;
  moderator_raw?: string;
  publication_time_raw?: string;
  registration_url?: string;
  online_url?: string;
  contact_raw?: string;
  language_raw?: string;
  tags_raw?: string[];
  cancellation_status_raw?: string;
  event_type_raw?: string;
  discovered_from_url: string;
  fetched_at: string;
}

export interface DiscoveredUrl {
  url: string;
  kind: "pagination" | "detail" | "seed";
  discoveredFromUrl: string;
}

export interface AdapterDiscovery {
  urls: DiscoveredUrl[];
}

export interface SourceMetadata {
  id: string;
  name: string;
  allowedOrigins: string[];
  seedUrls: string[];
  maxPages: number;
}

export interface SourceAdapter {
  metadata: SourceMetadata;
  isAllowedUrl(url: string): boolean;
  discover(document: RawDocument): AdapterDiscovery;
  parse(document: RawDocument): EventCandidate[];
}

export interface CrawlFailure {
  category: FailureCategory;
  sourceId: string;
  url: string;
  message: string;
  crawlRunId: string;
  occurredAt: string;
}

export interface CrawlSummary {
  runId: string;
  sourcesAttempted: number;
  sourcesCompleted: number;
  pagesRequested: number;
  pagesFetched: number;
  pagesFailed: number;
  candidatesFound: number;
  normalizedEventsProduced: number;
  timeFilterAsOf: string;
  pastEventsFiltered: number;
  unresolvedEventsFiltered: number;
  validationFailures: number;
  duplicatesRemoved: number;
  rawSnapshotLocation: string;
  extractedOutputLocation: string;
  normalizedOutputLocation: string;
  logLocation: string;
  failures: CrawlFailure[];
}
