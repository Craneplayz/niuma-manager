# Email test index

141 backend tests in 14 files. Each line below describes one test and links to its definition, in source order. Account email-verification tests belong to authentication and are excluded. Account email-verification tests belong to authentication and are excluded. Account email-verification tests belong to authentication and are excluded. All use synthetic inputs; passing them does not establish real mailbox compatibility or model/deadline accuracy.

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

1. [Encrypt credentials, isolate accounts and support removal.](test_email_source_routes.py#L46)
2. [Decrypt only the requesting account’s saved credentials.](test_email_source_routes.py#L65)
3. [Reject retired sync calls without reading or persisting bodies.](test_email_source_routes.py#L77)

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
5. [Prevent operation-ID replay with a different selection.](test_email_import_integration.py#L166)
6. [Keep task classification after a candidate is ignored.](test_email_import_integration.py#L177)
7. [Review, save and cache both tasks and notices.](test_email_import_integration.py#L219)
8. [Reject late results after stop; record job latency.](test_email_import_integration.py#L248)
9. [Interrupt unfinished work on restart; reuse completed results.](test_email_import_integration.py#L277)
10. [Bind queued ranges to credentials; disconnect revokes consent.](test_email_import_integration.py#L310)
11. [Discard results when consent is revoked during generation.](test_email_import_integration.py#L329)

### [test_email_import_store.py](test_email_import_store.py) — Durable storage (3)

1. [Migrate legacy snapshots once; preserve account scope and item order.](test_email_import_store.py#L15)
2. [Roll back jobs, items and revisions atomically.](test_email_import_store.py#L34)
3. [Keep incremental counters consistent with full snapshot counts.](test_email_import_store.py#L48)

### [test_email_import_safety.py](test_email_import_safety.py) — Shared-model admission guards (5)

1. [Consent revocation cancels waiting email model.](test_email_import_safety.py#L206)
2. [Email exclusion cancels waiting email model.](test_email_import_safety.py#L209)
3. [Consent revocation cancels waiting email model.](test_email_import_safety.py#L212)
4. [Resume after capacity becomes available without rereading bodies.](test_email_import_safety.py#L215)
5. [Enforce consent and ownership through generic recognition endpoints.](test_email_import_safety.py#L236)

### [test_email_import_parallel.py](test_email_import_parallel.py) — Concurrent import behavior (12)

1. [Handle out-of-order completion with correct progress counts.](test_email_import_parallel.py#L30)
2. [Enforce eight concurrent email workers across accounts.](test_email_import_parallel.py#L59)
3. [Stop replenishment while draining already-started calls.](test_email_import_parallel.py#L102)
4. [Prevent replenishment and late saves for newly excluded messages.](test_email_import_parallel.py#L120)
5. [Drain without replenishment after revocation or credential rotation.](test_email_import_parallel.py#L143)
6. [Allow explicit retry of failed items in a new job.](test_email_import_parallel.py#L176)
7. [Persist in-flight successes when another dispatch submission fails.](test_email_import_parallel.py#L191)
8. [Release capacity during provider backoff; reuse captured bodies.](test_email_import_parallel.py#L230)
9. [Bound repeated HTTP 429 retries without rereading IMAP.](test_email_import_parallel.py#L267)
10. [Never automatically replay an ambiguous timeout.](test_email_import_parallel.py#L276)
11. [Keep sibling messages when one is excluded during preparation.](test_email_import_parallel.py#L282)
12. [Prevent another model call after stopping during backoff.](test_email_import_parallel.py#L301)

### [test_email_scheduler.py](test_email_scheduler.py) — Fair scheduling and ownership (11)

1. [Shutdown cancels all waiters without dispatching them.](test_email_scheduler.py#L18)
2. [Shutdown cancels all waiters without dispatching them.](test_email_scheduler.py#L41)
3. [Shutdown cancels all waiters without dispatching them.](test_email_scheduler.py#L78)
4. [Serve 30 accounts round-robin with at most two slots each.](test_email_scheduler.py#L96)
5. [Release capacity for canceled queued work.](test_email_scheduler.py#L114)
6. [Recovery retries after real sqlite write lock.](test_email_scheduler.py#L127)
7. [Real live owner survives probe and second instance.](test_email_scheduler.py#L158)
8. [Wake eligible work when a blocked queue head is canceled.](test_email_scheduler.py#L189)
9. [Recovery retries after real sqlite write lock.](test_email_scheduler.py#L220)
10. [Claim does not create missing database.](test_email_scheduler.py#L235)
11. [Drop ownership when the database disappears.](test_email_scheduler.py#L241)

### [test_email_capacity.py](test_email_capacity.py) — Resource bounds and provider quotas (17)

1. [Bound simultaneous IMAP sessions across mailboxes.](test_email_capacity.py#L23)
2. [Serialize one mailbox while allowing other mailboxes through.](test_email_capacity.py#L47)
3. [Cancel mailbox-capacity waits promptly.](test_email_capacity.py#L75)
4. [Enforce the body-buffer byte budget.](test_email_capacity.py#L103)
5. [Count every message in a batch against body-buffer limits.](test_email_capacity.py#L132)
6. [Cancel body-buffer waits and release their accounting.](test_email_capacity.py#L153)
7. [Prioritize user controls without losing progress writes.](test_email_capacity.py#L180)
8. [Release database admission after exceptions.](test_email_capacity.py#L197)
9. [Missing fallback usage does not refund its quota.](test_email_capacity.py#L208)
10. [Missing fallback usage does not refund its quota.](test_email_capacity.py#L239)
11. [429 cooldown applies even with unconfigured quota.](test_email_capacity.py#L254)
12. [Missing fallback usage does not refund its quota.](test_email_capacity.py#L263)
13. [429 cooldown applies even with unconfigured quota.](test_email_capacity.py#L281)
14. [Refund quota reservations and isolate provider budgets.](test_email_capacity.py#L292)
15. [Honor HTTP 429 cooldowns even without configured quotas.](test_email_capacity.py#L307)
16. [Default capacity settings without inheriting shell overrides.](test_email_capacity.py#L324)
17. [Bound status-metric samples and exclude private fields.](test_email_capacity.py#L332)

### [test_email_recognition.py](test_email_recognition.py) — Model configuration and telemetry transport (10)

1. [Disable thinking only for the supported email provider/model.](test_email_recognition.py#L26)
2. [Include execution settings and fallback rules in cache identity.](test_email_recognition.py#L31)
3. [Runtime keeps email budget and fallback separate from manual text.](test_email_recognition.py#L40)
4. [Allow harness transport time without changing manual timeouts.](test_email_recognition.py#L53)
5. [Queue wait is separate from provider and validation.](test_email_recognition.py#L113)
6. [Queue wait is separate from provider and validation.](test_email_recognition.py#L125)
7. [Measure queue wait separately from generation and validation.](test_email_recognition.py#L133)
8. [Classify invalid output without storing raw responses.](test_email_recognition.py#L142)
9. [Corrective text call settles both provider receipts.](test_email_recognition.py#L150)
10. [Record both attempts’ token usage after truncation fallback.](test_email_recognition.py#L160)

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

### [test_email_telemetry.py](test_email_telemetry.py) — Content-free monitoring (12)

1. [Every writer closes connections after success and failure.](test_email_telemetry.py#L29)
2. [Every writer closes connections after success and failure.](test_email_telemetry.py#L55)
3. [Measure records success and failure elapsed and reraises.](test_email_telemetry.py#L68)
4. [Every writer closes connections after success and failure.](test_email_telemetry.py#L85)
5. [Measure records success and failure elapsed and reraises.](test_email_telemetry.py#L88)
6. [Measure success/failure duration and rethrow the original exception.](test_email_telemetry.py#L105)
7. [Preserve aggregate statistics and 90-day retention.](test_email_telemetry.py#L136)
8. [Admin exclusions and no private fields.](test_email_telemetry.py#L146)
9. [Allowlist diagnostic reasons; hash actors and enforce retention.](test_email_telemetry.py#L158)
10. [Admin exclusions and no private fields.](test_email_telemetry.py#L166)
11. [Existing database without diagnostics is readable without creating table.](test_email_telemetry.py#L177)
12. [Expose only aggregate diagnostic categories with account exclusions.](test_email_telemetry.py#L182)

## Extraction and response diagnostics

### [test_email_content_types.py](../ddl_text/tests/test_email_content_types.py) — Email task/notice classification (4)

1. [Require content types and keep prompt definitions consistent.](../ddl_text/tests/test_email_content_types.py#L21)
2. [Compile notices and undated actions through the public contract.](../ddl_text/tests/test_email_content_types.py#L31)
3. [Require types for new results; preserve legacy task defaults.](../ddl_text/tests/test_email_content_types.py#L44)
4. [Require notice evidence to appear in the source.](../ddl_text/tests/test_email_content_types.py#L52)

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
- [benchmark_email_concurrency.py](benchmark_email_concurrency.py): separate synthetic load benchmark; not counted among the 141 tests. Workload, limits and recorded results: [capacity guide](docs/email-capacity.md).
- Add/rename/remove a test: update its one-line entry, count and source link here. Keep race scenarios separate when they guard different pipeline stages. Add a second-layer test only when it checks transport, persistence or another distinct contract.
- Historical execution results stay in the capacity guide. This index describes coverage, not the latest run status.
