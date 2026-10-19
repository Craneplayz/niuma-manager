# Email test index

135 backend tests in 14 files. Each line below describes one test and links to its definition, in source order. All use synthetic inputs; passing them does not establish real mailbox compatibility or model/deadline accuracy.

## Run

From the repository root, using the project Python environment:

```sh
.venv/bin/python -m unittest discover -s server -p 'test_email*.py' -q
.venv/bin/python -m unittest ddl_text.tests.test_email_content_types ddl_text.tests.test_email_response_diagnostic ddl_text.tests.test_truncation_retry -q
cd frontend
npm test
```

The second command runs 14 email extraction/diagnostic tests plus 5 shared fallback tests. The frontend command runs all features, not just email.

## Backend tests

### [test_email_source.py](test_email_source.py) — Read-only mailbox access (16)

1. [Accept and normalize only supported PKU domains.](test_email_source.py#L14)
2. [Use fixed TLS hosts and always log out.](test_email_source.py#L21)
3. [Preview headers without reading bodies.](test_email_source.py#L35)
4. [Default to 50 messages; paginate larger ranges.](test_email_source.py#L58)
5. [Filter before pagination; fetch only visible headers.](test_email_source.py#L75)
6. [Include the selected start date in Shanghai time.](test_email_source.py#L89)
7. [Read selected plain-text bodies with BODY.PEEK.](test_email_source.py#L108)
8. [Mark omitted attachments as incomplete content.](test_email_source.py#L127)
9. [Reject foreign or duplicate message IDs.](test_email_source.py#L150)
10. [Reuse short-lived connections in small batches.](test_email_source.py#L158)
11. [Skip an excluded message without skipping its siblings.](test_email_source.py#L178)
12. [Reject oversized body metadata before fetching content.](test_email_source.py#L198)
13. [Reject content without a valid IMAP literal.](test_email_source.py#L205)
14. [Strip scripts and styles from HTML.](test_email_source.py#L211)
15. [Match headers by UID despite response reordering.](test_email_source.py#L223)
16. [Reject unexpected, duplicate, missing or oversized header literals.](test_email_source.py#L234)

### [test_email_source_routes.py](test_email_source_routes.py) — Credentials and legacy API (3)

1. [Encrypt credentials, isolate accounts and support removal.](test_email_source_routes.py#L45)
2. [Decrypt only the requesting account’s saved credentials.](test_email_source_routes.py#L63)
3. [Reject retired sync calls without reading or persisting bodies.](test_email_source_routes.py#L75)

### [test_email_header_cache.py](test_email_header_cache.py) — Header snapshot storage (14)

1. [Restore cached headers after module reload without IMAP.](test_email_header_cache.py#L57)
2. [Fetch only new or missing headers during refresh/paging.](test_email_header_cache.py#L66)
3. [Handle empty ranges without crossing account boundaries.](test_email_header_cache.py#L75)
4. [Reject old snapshots after UIDVALIDITY changes.](test_email_header_cache.py#L82)
5. [Keep the previous snapshot when refresh fails.](test_email_header_cache.py#L91)
6. [Avoid mailbox searches for cached reads and lazy pages.](test_email_header_cache.py#L99)
7. [Reject a changed UID epoch without searches or writes.](test_email_header_cache.py#L117)
8. [Invalidate other ranges when the mailbox UID epoch changes.](test_email_header_cache.py#L131)
9. [Report missing headers as an incomplete cache.](test_email_header_cache.py#L141)
10. [Prevent a slower refresh overwriting a newer snapshot.](test_email_header_cache.py#L148)
11. [Prevent old cross-range refreshes erasing a new UID epoch.](test_email_header_cache.py#L189)
12. [Reject publication when connection guards become stale.](test_email_header_cache.py#L232)
13. [Coalesce matching simultaneous refreshes into one mailbox sync.](test_email_header_cache.py#L240)
14. [Allow retry after a coalesced refresh fails.](test_email_header_cache.py#L266)

### [test_email_cache_routes.py](test_email_cache_routes.py) — Cached header API (6)

1. [Restore and revisit cached pages without decryption or refetching.](test_email_cache_routes.py#L13)
2. [Refresh exclusions and recognition flags on cached headers.](test_email_cache_routes.py#L42)
3. [Clear cached headers and reject old tokens after disconnect/change.](test_email_cache_routes.py#L63)
4. [Reject invalid cache controls before mailbox access.](test_email_cache_routes.py#L76)
5. [Load full-range headers in bounded batches; restore offline.](test_email_cache_routes.py#L85)
6. [Return partial cached ranges without fetching missing headers.](test_email_cache_routes.py#L100)

### [test_email_list_filters.py](test_email_list_filters.py) — List filtering and sorting (5)

1. [Prioritize pending review across the full range; isolate candidate counts.](test_email_list_filters.py#L38)
2. [Prefer exclusions/successful caches over stale job progress.](test_email_list_filters.py#L66)
3. [Sort the fetched range newest or oldest first.](test_email_list_filters.py#L88)
4. [Reject invalid filters before mailbox access.](test_email_list_filters.py#L98)
5. [Chunk state queries below SQLite’s parameter limit.](test_email_list_filters.py#L108)

### [test_email_import_integration.py](test_email_import_integration.py) — HTTP import lifecycle (11)

1. [Handle encoded job IDs and isolate read/stop/retry operations.](test_email_import_integration.py#L36)
2. [Preview, exclude, recognize and reuse a persistent no-task cache.](test_email_import_integration.py#L62)
3. [Reuse legacy successful results without IMAP or model calls.](test_email_import_integration.py#L103)
4. [Process over 50 messages; retry only failed items.](test_email_import_integration.py#L135)
5. [Prevent operation-ID replay with a different selection.](test_email_import_integration.py#L165)
6. [Keep task classification after a candidate is ignored.](test_email_import_integration.py#L176)
7. [Review, save and cache both tasks and notices.](test_email_import_integration.py#L218)
8. [Reject late results after stop; record job latency.](test_email_import_integration.py#L247)
9. [Interrupt unfinished work on restart; reuse completed results.](test_email_import_integration.py#L276)
10. [Bind queued ranges to credentials; disconnect revokes consent.](test_email_import_integration.py#L309)
11. [Discard results when consent is revoked during generation.](test_email_import_integration.py#L328)

### [test_email_import_store.py](test_email_import_store.py) — Durable storage (3)

1. [Migrate legacy snapshots once; preserve account scope and item order.](test_email_import_store.py#L14)
2. [Roll back jobs, items and revisions atomically.](test_email_import_store.py#L33)
3. [Keep incremental counters consistent with full snapshot counts.](test_email_import_store.py#L47)

### [test_email_import_safety.py](test_email_import_safety.py) — Shared-model admission guards (5)

1. [Cancel model admission and purge staged bodies after consent revocation.](test_email_import_safety.py#L201)
2. [Cancel model admission and purge staged bodies after exclusion.](test_email_import_safety.py#L204)
3. [Cancel model admission and purge staged bodies after credential rotation.](test_email_import_safety.py#L207)
4. [Resume after capacity becomes available without rereading bodies.](test_email_import_safety.py#L210)
5. [Enforce consent and ownership through generic recognition endpoints.](test_email_import_safety.py#L231)

### [test_email_import_parallel.py](test_email_import_parallel.py) — Concurrent import behavior (12)

1. [Handle out-of-order completion with correct progress counts.](test_email_import_parallel.py#L29)
2. [Enforce eight concurrent email workers across accounts.](test_email_import_parallel.py#L58)
3. [Stop replenishment while draining already-started calls.](test_email_import_parallel.py#L102)
4. [Prevent replenishment and late saves for newly excluded messages.](test_email_import_parallel.py#L120)
5. [Drain without replenishment after revocation or credential rotation.](test_email_import_parallel.py#L143)
6. [Allow explicit retry of failed items in a new job.](test_email_import_parallel.py#L176)
7. [Persist in-flight successes when another dispatch submission fails.](test_email_import_parallel.py#L191)
8. [Release capacity during provider backoff; reuse captured bodies.](test_email_import_parallel.py#L230)
9. [Bound repeated HTTP 429 retries without rereading IMAP.](test_email_import_parallel.py#L259)
10. [Never automatically replay an ambiguous timeout.](test_email_import_parallel.py#L268)
11. [Keep sibling messages when one is excluded during preparation.](test_email_import_parallel.py#L274)
12. [Prevent another model call after stopping during backoff.](test_email_import_parallel.py#L293)

### [test_email_scheduler.py](test_email_scheduler.py) — Fair scheduling and ownership (8)

1. [Serve 30 accounts round-robin with at most two slots each.](test_email_scheduler.py#L15)
2. [Release capacity for canceled queued work.](test_email_scheduler.py#L52)
3. [Wake eligible work when a blocked queue head is canceled.](test_email_scheduler.py#L70)
4. [Release weighted slots after executor submission failure.](test_email_scheduler.py#L88)
5. [Fence fresh foreign leases; reclaim stale leases.](test_email_scheduler.py#L111)
6. [Reclaim dead local owners while respecting live owners.](test_email_scheduler.py#L126)
7. [Avoid creating a database during ownership checks.](test_email_scheduler.py#L141)
8. [Drop ownership when the database disappears.](test_email_scheduler.py#L147)

### [test_email_capacity.py](test_email_capacity.py) — Resource bounds and provider quotas (16)

1. [Bound simultaneous IMAP sessions across mailboxes.](test_email_capacity.py#L23)
2. [Serialize one mailbox while allowing other mailboxes through.](test_email_capacity.py#L47)
3. [Cancel mailbox-capacity waits promptly.](test_email_capacity.py#L75)
4. [Enforce the body-buffer byte budget.](test_email_capacity.py#L103)
5. [Count every message in a batch against body-buffer limits.](test_email_capacity.py#L132)
6. [Cancel body-buffer waits and release their accounting.](test_email_capacity.py#L153)
7. [Prioritize user controls without losing progress writes.](test_email_capacity.py#L180)
8. [Release database admission after exceptions.](test_email_capacity.py#L197)
9. [Refund quota reservations and isolate provider budgets.](test_email_capacity.py#L208)
10. [Honor HTTP 429 cooldowns even without configured quotas.](test_email_capacity.py#L223)
11. [Default capacity settings without inheriting shell overrides.](test_email_capacity.py#L232)
12. [Retain safe HTTP 429 metadata without provider content.](test_email_capacity.py#L250)
13. [Defer email when model slots are full; do not call the provider.](test_email_capacity.py#L261)
14. [Release the shared model slot after an explicit HTTP 429.](test_email_capacity.py#L276)
15. [Share provider cooldowns with other business features.](test_email_capacity.py#L293)
16. [Bound status-metric samples and exclude private fields.](test_email_capacity.py#L301)

### [test_email_recognition.py](test_email_recognition.py) — Model configuration and telemetry transport (9)

1. [Disable thinking only for the supported email provider/model.](test_email_recognition.py#L25)
2. [Include execution settings and fallback rules in cache identity.](test_email_recognition.py#L30)
3. [Apply email-only thinking/270-second/fallback settings; preserve manual defaults.](test_email_recognition.py#L39)
4. [Allow harness transport time without changing manual timeouts.](test_email_recognition.py#L51)
5. [Measure queue wait separately from generation and validation.](test_email_recognition.py#L103)
6. [Classify invalid output without storing raw responses.](test_email_recognition.py#L115)
7. [Record failed generation without inventing validation timing.](test_email_recognition.py#L123)
8. [Record tokens once per successful or failed model attempt.](test_email_recognition.py#L132)
9. [Record both attempts’ token usage after truncation fallback.](test_email_recognition.py#L140)

### [test_email_reprocess.py](test_email_reprocess.py) — Explicit cache updates and reviewed decisions (16)

1. [Reuse stored bodies and publish a new successful result version.](test_email_reprocess.py#L39)
2. [Keep the previous successful cache after a failed rerun.](test_email_reprocess.py#L65)
3. [Reuse legacy body snapshots without IMAP.](test_email_reprocess.py#L83)
4. [Project all cache-policy states without recognition.](test_email_reprocess.py#L107)
5. [Preserve ignored, accepted and edited candidates.](test_email_reprocess.py#L134)
6. [Recheck review decisions made while generation is running.](test_email_reprocess.py#L146)
7. [Mark new candidates uncertain when protected cards exist.](test_email_reprocess.py#L165)
8. [Reject late rerun results after stop, revocation or exclusion.](test_email_reprocess.py#L182)
9. [Preserve pending cards and successful results after failure.](test_email_reprocess.py#L204)
10. [Preserve saved workspace tasks after successful reprocessing.](test_email_reprocess.py#L227)
11. [Update outdated results across the full range; skip excluded/current/unknown items.](test_email_reprocess.py#L276)
12. [Include unknown policy versions only by explicit choice.](test_email_reprocess.py#L296)
13. [Reject stale confirmation after rules or selection change.](test_email_reprocess.py#L305)
14. [Enforce snapshot ownership and explicit processing consent.](test_email_reprocess.py#L315)
15. [Keep successful results available after a failed bulk update.](test_email_reprocess.py#L326)
16. [Support ranges above 1,000 items without hydrating headers.](test_email_reprocess.py#L342)

### [test_email_telemetry.py](test_email_telemetry.py) — Content-free monitoring (11)

1. [Allow only known event stages and content-free fields.](test_email_telemetry.py#L32)
2. [Measure success/failure duration and rethrow the original exception.](test_email_telemetry.py#L45)
3. [Keep telemetry write failures from breaking imports.](test_email_telemetry.py#L62)
4. [Preserve aggregate statistics and 90-day retention.](test_email_telemetry.py#L65)
5. [Allowlist diagnostic reasons; hash actors and enforce retention.](test_email_telemetry.py#L82)
6. [Distinguish missing token usage, zero usage, failures and truncation.](test_email_telemetry.py#L113)
7. [Exclude configured/admin accounts and reject private token fields.](test_email_telemetry.py#L123)
8. [Reject invalid token values; keep missing-table reads non-mutating.](test_email_telemetry.py#L135)
9. [Apply token retention; tolerate locked telemetry storage.](test_email_telemetry.py#L143)
10. [Read old databases without creating diagnostics tables.](test_email_telemetry.py#L154)
11. [Expose only aggregate diagnostic categories with account exclusions.](test_email_telemetry.py#L159)

## Extraction and response diagnostics

### [test_email_content_types.py](../ddl_text/tests/test_email_content_types.py) — Email task/notice classification (4)

1. [Require content types and keep prompt definitions consistent.](../ddl_text/tests/test_email_content_types.py#L21)
2. [Compile notices and undated actions through the public contract.](../ddl_text/tests/test_email_content_types.py#L31)
3. [Reject invalid types; default legacy email drafts to tasks.](../ddl_text/tests/test_email_content_types.py#L44)
4. [Require notice evidence to appear in the source.](../ddl_text/tests/test_email_content_types.py#L50)

### [test_email_response_diagnostic.py](../ddl_text/tests/test_email_response_diagnostic.py) — Diagnostic replay safety (10)

1. [Identify reasoning-only truncation before task validation.](../ddl_text/tests/test_email_response_diagnostic.py#L28)
2. [Classify an empty answer as a provider-response failure.](../ddl_text/tests/test_email_response_diagnostic.py#L39)
3. [Classify malformed task JSON at validation.](../ddl_text/tests/test_email_response_diagnostic.py#L44)
4. [Keep valid no-task results despite a broken diagnostic observer.](../ddl_text/tests/test_email_response_diagnostic.py#L50)
5. [Never echo untrusted provider metadata.](../ddl_text/tests/test_email_response_diagnostic.py#L56)
6. [Load only isolated configuration; ignore shell credentials/endpoints.](../ddl_text/tests/test_email_response_diagnostic.py#L65)
7. [Read one saved email snapshot without modifying storage.](../ddl_text/tests/test_email_response_diagnostic.py#L75)
8. [Report evidence-reference failure types without private quotes or IDs.](../ddl_text/tests/test_email_response_diagnostic.py#L88)
9. [Diagnose whitespace differences without weakening evidence validation.](../ddl_text/tests/test_email_response_diagnostic.py#L102)
10. [Keep diagnostic timeout overrides separate from normal email settings.](../ddl_text/tests/test_email_response_diagnostic.py#L114)

### [test_truncation_retry.py](../ddl_text/tests/test_truncation_retry.py) — Shared pipeline fallback used by email (5)

1. [Retry confirmed truncation at 16K output tokens within the remaining 270-second budget.](../ddl_text/tests/test_truncation_retry.py#L20)
2. [Stop after a second truncation; at most two model attempts.](../ddl_text/tests/test_truncation_retry.py#L32)
3. [Do not escalate timeouts, evidence errors or unconfirmed truncation.](../ddl_text/tests/test_truncation_retry.py#L37)
4. [Do not retry after cancellation or budget expiry.](../ddl_text/tests/test_truncation_retry.py#L43)
5. [Require explicit fallback configuration and bounded token limits.](../ddl_text/tests/test_truncation_retry.py#L53)

The 8K → 16K setting enlarges the **output-token allowance**, not the context window. Only confirmed truncation enables that fallback; a timeout alone does not. These tests simulate responses and elapsed time; they do not replay private email or demonstrate provider-side success.

## Frontend coverage

| File | What it checks |
| --- | --- |
| [import-flow.test.js](../frontend/tests/import-flow.test.js) | Consent/settings, local full-range sort/filter/pagination, selection, stale responses, job progress, stop/retry/update and notice review. Shared with other import sources. |
| [account-email-settings.test.js](../frontend/tests/account-email-settings.test.js) — 6 | Open/focus PKU settings; prevent late reopening or cross-account updates. |
| [connection-return-hint.test.js](../frontend/tests/connection-return-hint.test.js) — 7 | Safe return-to-settings hints, session changes and notification precedence. Includes teaching connections. |
| [email-diagnostics.test.js](../frontend/tests/email-diagnostics.test.js) — 3 | Safe diagnostic rendering, token coverage and overlapping-timing explanations. |

## Fixtures, load checks and maintenance

- [email_test_support.py](email_test_support.py): shared fake IMAP, no-task model result and fresh authenticated HTTP fixture; not a test suite. Workers drain before fixture cleanup.
- [benchmark_email_concurrency.py](benchmark_email_concurrency.py): separate synthetic load benchmark; not counted among the 135 tests. Workload, limits and recorded results: [capacity guide](docs/email-capacity.md).
- Add/rename/remove a test: update its one-line entry, count and source link here. Keep race scenarios separate when they guard different pipeline stages. Add a second-layer test only when it checks transport, persistence or another distinct contract.
- Historical execution results stay in the capacity guide. This index describes coverage, not the latest run status.
