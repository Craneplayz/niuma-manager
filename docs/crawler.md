# Niuma Manager crawler

Deterministic public-event crawling for three PKU sources: the School of Computer Science, the School of Life Sciences, and the Graduate School of Education. This document covers the standalone crawler module; the application frontend and text recognition service are documented in the repository README. A separate read-only Playwright command checks authenticated PKU course homework deadlines.

## Quick start

Run these commands from the repository root. Requires Node.js 20.19+ (20.x), 22.13+ (22.x), or 24+, and pnpm 10.17.1. The frontend retains its separate npm setup in `frontend/`.

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

## Read-only PKU homework check

The authenticated course check is separate from the public-event pipeline. It uses a one-off headless Playwright browser, reads the first two lines of a local `secret` file as the campus-card username and password, and does not save cookies, storage state, credentials, or page snapshots. After submitting the login form, it only navigates course pages and never submits coursework.

```bash
pnpm pku:homework
pnpm pku:homework -- --term=26-27学年第1学期
pnpm pku:homework -- --all-terms
```

The command reports only records whose explicitly parsed deadline is strictly later than the run time in `Asia/Shanghai`. Date-only deadlines are treated as 23:59:59 on that date; `24:00` is treated as the following day's 00:00. Records without an explicit deadline are counted as `undated_records` and are not included in `upcoming`. The default term is derived from the run date.

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

Cleaning is conservative: Unicode/HTML decoding, whitespace and label cleanup, URL canonicalization, explicit event-type mapping, and deterministic date rules only. Exact source identities are deduplicated first; a stronger duplicate key (`source + title + start time + location`) is applied only within one source. Cross-source fuzzy matching is intentionally not implemented.

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

Conversion of crawler output into v0.2 text recognition requests and submission to the recognition service are deferred to a separate issue. This module does not create application tasks.

Homework extraction uses text boundaries and remains heuristic: records stop at the next homework heading, repeated lines are preserved, and conflicting deadlines within one record remain undated. Check the original course page when its layout is ambiguous.
