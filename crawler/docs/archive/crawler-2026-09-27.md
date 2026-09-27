# Niuma Manager crawler

Deterministic public-event crawling for three PKU sources: the School of Computer Science, the School of Life Sciences, and the Graduate School of Education. This document covers the standalone crawler module; the application frontend and text recognition service are documented in the repository README. A separate read-only Playwright command collects authenticated PKU course sources and optionally exports text-recognition requests.

## Quick start

Run these commands from the repository root. Requires Node.js 20.19+ (20.x), 22.13+ (22.x), or 24+, and pnpm 10.17.1. The frontend retains its separate npm setup in `frontend/`. Contract interoperability tests also require Python 3.9+ with IANA timezone data; `python3` is used by default, or set `PYTHON` to its executable path.

```bash
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm crawler crawl all
pnpm crawler crawl all --as-of=2026-09-22T12:00:00+08:00
pnpm crawler crawl cs
pnpm crawler crawl bio
pnpm crawler crawl gse
pnpm crawler reprocess <run-id>
pnpm crawler review <run-id>
pnpm pku:homework
```

`crawl` writes a run directory under `data/raw/<source>/<run-id>/`, candidate JSONL to `data/extracted/<run-id>.jsonl`, normalized JSONL to `data/normalized/<run-id>.jsonl`, and structured logs under `logs/<run-id>.jsonl`. These generated artifacts are intentionally ignored by Git.

The public-event crawler applies the same global future-only filter to every registered PKU adapter. Normalized output contains only events whose start time is strictly later than the filter instant; past events and events without a resolvable start time are counted in the crawl summary and excluded from normalized output. Raw snapshots and extracted candidates remain unfiltered for auditability. The default filter instant is the current time in `Asia/Shanghai`; pass `--as-of=<ISO timestamp>` to make a run reproducible.

## Read-only PKU course collection

The authenticated course check is separate from the public-event pipeline. It uses a one-off headless Playwright browser, reads the first two lines of a local `secret` file as the campus-card username and password, and does not save cookies, storage state, credentials, or page snapshots. After submitting the login form, it only navigates course pages and never submits coursework.

```bash
pnpm pku:homework
pnpm pku:homework -- --term=26-27学年第1学期
pnpm pku:homework -- --all-terms
```

The primary output is `source_records`: every collected announcement, assignment, and material item, without homework-keyword or deadline filtering. `source_records_found` counts these source records. Each contains the original title/body with paragraph boundaries, source URL and platform ID, course/section, publication metadata, collection time, text quality, unread reference links, and coverage warnings. Navigation and folder links are excluded. `coverage_warnings` on the run describes discovery limits. Navigation failures, HTTP error responses, and redirects out of the authenticated area abort the run instead of extracting a stale or login page as course content.

For compatibility, `records` and its `records_found`/`past_records`/`undated_records` counts still provide a keyword-based homework view, including past, pending, conflicting, and unparseable deadlines with their source text. `upcoming` is a separate view containing only records whose parsed deadline is strictly later than the run time in `Asia/Shanghai`. Date-only deadlines are treated as 23:59:59 on that date; `24:00` is treated as the following day's 00:00. Records without an explicit deadline are counted as `undated_records` and are not included in `upcoming`. The default term is derived from the run date. Yearless month/day values use the closest valid date in the scrape year or its adjacent years; explicit years are never shifted. This is a heuristic, so retain the raw deadline for downstream review. Unparseable deadline fields remain in `deadline_raw`.

`review` reads a normalized JSONL file and creates a standalone inspection page at `data/review/<run-id>.html`. Open that file directly in a browser to check titles, times, locations, provenance, warnings, and raw fields. It is intentionally separate from the application frontend and is also ignored by Git.

## Architecture

The pipeline is deliberately composed of small interfaces:

```text
Fetcher -> RawDocument -> SourceAdapter discovery/parser -> EventCandidate
                                                    -> deterministic normalizer
                                                    -> NormalizedEvent -> JSONL
```

`Fetcher` has no source-specific knowledge. The current `PublicHttpFetcher` performs ordinary HTTP requests with timeouts, limited retries, per-origin spacing, a descriptive user agent, and cached robots.txt checks. A future local authenticated fetcher can implement the same interface and pass its documents to the same adapters.

`RawSnapshotStore` persists the response body and provenance metadata separately from application logs. `StoragePolicy` is a boundary for the future privacy rule where authenticated/private response bodies may remain local or not be persisted.

Adapters explicitly own their seeds, allowed origins, detail URL rules, and pagination discovery. They use Cheerio for deterministic HTML parsing and never call an LLM. A page-level failure is recorded and does not stop other pages or sources.

## Data model

`EventCandidate` preserves source strings (`title_raw`, `time_raw`, `location_raw`, `organizer_raw`, `speaker_raw`, publication time, and source URL). `NormalizedEvent` is Zod-validated and contains a stable source-derived `id`, title/description, Asia/Shanghai timestamps when deterministically resolvable, optional location/organizers/speakers, event type, provenance, raw values, parse status, warnings, and `time_resolution` (`exact`, `context_inferred`, or `unresolved`). Unresolved times remain null; the raw string and warning are retained.

Cleaning is conservative: Unicode/HTML decoding, whitespace and label cleanup, URL canonicalization, explicit event-type mapping, and deterministic date rules only. Publication dates provide year context only and never substitute for missing event dates. Year inference chooses the nearest valid date in the context year or its adjacent years and remains marked `context_inferred`. Clock parsing requires a colon, Chinese hour marker, or AM/PM; malformed clocks stay unresolved. Exact source identities are deduplicated first; a stronger duplicate key (`source + title + start time + location`) is applied only within one source. Cross-source fuzzy matching is intentionally not implemented.

## Adding a source

1. Add source metadata and a `SourceAdapter` in `src/adapters/`.
2. Keep `seedUrls`, `allowedOrigins`, `isAllowedUrl`, discovery, and parsing together in that adapter.
3. Register the adapter in `src/adapters/index.ts` and add its CLI alias.
4. Add stable sanitized HTML under `tests/fixtures/<source>/` and parser tests under `tests/`.

An adapter should return candidates with raw source strings and a stable `source_item_id` when the site exposes one. It should not normalize times or write files.

## Privacy boundary and limitations

Public server fetching and public raw HTML persistence are implemented now. The PKU homework command is a separate local authenticated flow: credentials are read from `secret`, used only for the PKU login form, and never persisted. It does not bypass CAPTCHA, submit coursework, save browser state, or persist private page snapshots. No database, scheduler, or encryption is included.

The target sites are traditional CMS sites and change their markup periodically. The adapters intentionally use multiple semantic fallbacks, but a changed detail template may require a fixture update. The Life Sciences site currently exposes lecture summaries prominently on its list page; detail pages are followed only when they match the adapter's same-origin lecture URL rules. The optional aggregate platform is not enabled by default because it is a separate legacy application with different query/rendering behavior.

## Text recognition integration

The campus-course collector now provides a pure v0.2 conversion layer in `src/pku-recognition.ts`, using the existing contract in `docs/backend-harness-alignment.md`. The skill, recognition engine, and frontend/backend interfaces are unchanged. Exporting does not call an LLM, submit a service request, create tasks, or update a database. The public-event pipeline is separate and does not use this new converter.

```bash
# Full source records and optional legacy homework views (JSON)
pnpm pku:homework -- --format=raw

# v0.2 requests: one complete JSON request per line (JSONL)
pnpm pku:homework -- --format=recognition

# When credentials live outside this checkout
PKU_SECRET_PATH=/path/to/secret pnpm pku:homework -- --format=recognition
```

Recognition export uses **all `source_records`**, never `records` or `upcoming`. Requests contain at most 100 records and 2,000,000 UTF-8 bytes of serialized JSON each, including metadata, raw evidence and the request ID. A larger run produces multiple JSONL lines with distinct request IDs; each line can be passed as-is to the existing recognition API by the caller. An empty collection produces one valid empty request. Text exceeding the contract's 100,000-character limit, or a single record/envelope that cannot fit the request byte limit, fails explicitly without truncating evidence or emitting partial output. `--request-id=ID` supplies the request ID (with batch suffixes when necessary); reuse it only when retrying the same saved request, not for a fresh scrape whose collection time/content may differ. Default IDs are fresh for every export.

| Collected field | v0.2 mapping |
| --- | --- |
| title + body, retaining paragraphs | text; title is also source_label |
| original platform ID and source scope | namespaced input_id; unchanged source_item_id |
| course | course_or_project |
| source_url | source_url, with URL credentials and sensitive query parameters removed |
| published_at | source value and original date/datetime precision; unknown stays null |
| collected_at | collected_at; never substitutes for publication time |
| text_quality | complete/partial/summary/missing, without upgrading completeness |
| references, warnings, original publication string | upstream_hints; original strings under raw |

Publication parsing is conservative. A source date remains date-only; no midnight or collection year is added. Unsupported formats remain unknown with the original string retained. `Asia/Shanghai` is explicitly declared as `caller_default` in the recognition request. The converter does not infer a deadline or transfer the legacy parsed deadline into an authoritative field.

Live collection uses Blackboard content-list and announcement items as record boundaries. Item headings supply titles; body paragraphs and attachment names remain in the same source. Platform IDs are scoped by course and source kind for stable input IDs and deduplication. When no platform ID exists, the fallback ID includes the source text and can change when that text changes. These IDs are not database task IDs or an authorization mechanism.

Text quality describes the collected item: title-only items are `summary`; records with unread linked pages, attachments, or images are `partial` when body text is available; absent text is `missing`. A `complete` listing body does not claim complete discovery of the whole course. Unsupported source publication metadata remains separately inspectable.

The text-only homework helper remains available, but live collection does not split the full page body. Attachment contents, nested folders, and additional assignment-detail pages are not inspected. References are exported as unread; this scope is reported rather than treating missing detail as proof that no task or deadline exists. Submission to the recognition service remains the caller's responsibility.
