# Email test index

137 backend tests in 14 files. Each line below describes one test and links to its definition, in source order. Account email-verification tests belong to authentication and are excluded. All use synthetic inputs; passing them does not establish real mailbox compatibility or model/deadline accuracy.

Related variants share subtests or one lifecycle fixture. Queue/lease races remain separate; retired popup actions are excluded.

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

### [test_email_header_cache.py](test_email_header_cache.py) — Header snapshot storage (13)

1. [Fetch only new or missing headers during refresh/paging.](test_email_header_cache.py#L57)
2. [Handle empty ranges without crossing account boundaries.](test_email_header_cache.py#L66)
3. [Reject old snapshots after UIDVALIDITY changes.](test_email_header_cache.py#L73)
4. [Keep the previous snapshot when refresh fails.](test_email_header_cache.py#L82)
5. [Restore cached headers after reload; fetch missing page headers without another search.](test_email_header_cache.py#L90)
6. [Reject a changed UID epoch without searches or writes.](test_email_header_cache.py#L111)
7. [Invalidate other ranges when the mailbox UID epoch changes.](test_email_header_cache.py#L125)
8. [Report missing headers as an incomplete cache.](test_email_header_cache.py#L135)
9. [Prevent a slower refresh overwriting a newer snapshot.](test_email_header_cache.py#L142)
10. [Prevent old cross-range refreshes erasing a new UID epoch.](test_email_header_cache.py#L183)
11. [Reject publication when connection guards become stale.](test_email_header_cache.py#L226)
12. [Coalesce matching simultaneous refreshes into one mailbox sync.](test_email_header_cache.py#L234)
13. [Allow retry after a coalesced refresh fails.](test_email_header_cache.py#L260)

### [test_email_cache_routes.py](test_email_cache_routes.py) — Cached header API (6)

1. [Restore full/partial cached headers and revisit pages without decryption or unnecessary IMAP.](test_email_cache_routes.py#L13)
2. [Refresh exclusions and recognition flags on cached headers.](test_email_cache_routes.py#L49)
3. [Clear cached headers and reject old tokens after disconnect/change.](test_email_cache_routes.py#L70)
4. [Reject invalid cache controls before mailbox access.](test_email_cache_routes.py#L83)
5. [Load full-range headers in bounded batches; restore offline.](test_email_cache_routes.py#L92)
6. [Isolate results/exclusions across logins; preserve legacy IDs, password rotation, reconnects and historical evidence.](test_email_cache_routes.py#L106)

### [test_email_list_filters.py](test_email_list_filters.py) — List filtering and sorting (5)

1. [Prioritize pending review across the full range; isolate candidate counts.](test_email_list_filters.py#L38)
2. [Prefer exclusions/successful caches over stale job progress.](test_email_list_filters.py#L66)
3. [Sort the fetched range newest or oldest first.](test_email_list_filters.py#L88)
4. [Reject invalid filters before mailbox access.](test_email_list_filters.py#L98)
5. [Chunk state queries below SQLite’s parameter limit.](test_email_list_filters.py#L108)

### [test_email_import_integration.py](test_email_import_integration.py) — HTTP import lifecycle (9)

1. [Handle encoded job IDs and isolate read/stop/retry operations.](test_email_import_integration.py#L48)
2. [Preview, exclude, recognize and reuse a persistent no-task cache.](test_email_import_integration.py#L74)
3. [Reuse legacy successful results without IMAP or model calls.](test_email_import_integration.py#L115)
4. [Process over 50 messages; retry only failed items.](test_email_import_integration.py#L147)
5. [Prevent operation-ID replay with a different selection.](test_email_import_integration.py#L178)
6. [Review task/notice results, ignore/reopen a task, save both types and reuse their cache.](test_email_import_integration.py#L189)
7. [Keep exact, private-free diagnostics for malformed JSON/schema/evidence, timeout, configuration and save rollback; isolate owners.](test_email_import_integration.py#L230)
8. [Interrupt unfinished work on restart; reuse completed results.](test_email_import_integration.py#L296)
9. [Bind queued ranges to credentials; disconnect revokes consent.](test_email_import_integration.py#L329)

### [test_email_import_store.py](test_email_import_store.py) — Durable storage (3)

1. [Migrate legacy snapshots once; preserve account scope and item order.](test_email_import_store.py#L15)
2. [Roll back jobs, items and revisions atomically.](test_email_import_store.py#L34)
3. [Keep incremental counters consistent with full snapshot counts.](test_email_import_store.py#L48)

### [test_email_import_safety.py](test_email_import_safety.py) — Shared-model admission guards (3)

1. [Cancel queued model admission on consent revocation, exclusion or credential rotation.](test_email_import_safety.py#L206)
2. [Resume after capacity becomes available without rereading bodies.](test_email_import_safety.py#L218)
3. [Enforce consent and ownership through generic recognition endpoints.](test_email_import_safety.py#L239)

### [test_email_import_parallel.py](test_email_import_parallel.py) — Concurrent import behavior (16)

1. [Handle out-of-order completion with correct progress counts.](test_email_import_parallel.py#L30)
2. [Enforce eight concurrent email workers across accounts.](test_email_import_parallel.py#L59)
3. [Stop during a wave; reject late writes and emit terminal latency.](test_email_import_parallel.py#L102)
4. [Prevent replenishment and late saves for newly excluded messages.](test_email_import_parallel.py#L126)
5. [Revoke consent or rotate credentials mid-wave; reject late results and further work.](test_email_import_parallel.py#L149)
6. [Allow explicit retry of failed items in a new job.](test_email_import_parallel.py#L185)
7. [Persist in-flight successes when another dispatch submission fails.](test_email_import_parallel.py#L200)
8. [Release capacity during provider backoff; reuse captured bodies.](test_email_import_parallel.py#L239)
9. [Clear late sibling FIFO tickets created after provider cooldown cleanup.](test_email_import_parallel.py#L279)
10. [Reject HTTP jobs during lease takeover; admit only after recovery commits.](test_email_import_parallel.py#L321)
11. [Bound repeated HTTP 429 retries without rereading IMAP.](test_email_import_parallel.py#L372)
12. [Never automatically replay an ambiguous timeout.](test_email_import_parallel.py#L381)
13. [Keep sibling messages when one is excluded during preparation.](test_email_import_parallel.py#L387)
14. [Prevent another model call after stopping during backoff.](test_email_import_parallel.py#L406)
15. [Reject generic and duplicate email retries during background recognition; preserve successful read-only cache replay.](test_email_import_parallel.py#L422)
16. [Defer worker body-capacity waits; release execution/FIFO slots and resume from one captured body.](test_email_import_parallel.py#L451)

### [test_email_scheduler.py](test_email_scheduler.py) — Fair scheduling and ownership (11)

1. [Cancel all queued work on shutdown without dispatching it.](test_email_scheduler.py#L18)
2. [Serve 30 accounts round-robin with at most two slots each.](test_email_scheduler.py#L41)
3. [Release capacity for canceled queued work.](test_email_scheduler.py#L78)
4. [Wake eligible work when a blocked queue head is canceled.](test_email_scheduler.py#L96)
5. [Release weighted slots when executor submission fails.](test_email_scheduler.py#L114)
6. [Retry lease recovery after a real SQLite write lock.](test_email_scheduler.py#L127)
7. [Probe a live owner without terminating or stealing its lease.](test_email_scheduler.py#L158)
8. [Fence fresh foreign leases; recover interrupted jobs after stale-lease takeover.](test_email_scheduler.py#L189)
9. [Reclaim dead local owners while respecting live owners.](test_email_scheduler.py#L220)
10. [Do not create a missing database during a claim.](test_email_scheduler.py#L235)
11. [Drop process ownership when the database disappears.](test_email_scheduler.py#L241)

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

### [test_email_reprocess.py](test_email_reprocess.py) — Explicit cache updates and reviewed decisions (15)

1. [Reuse stored bodies and publish a new successful result version.](test_email_reprocess.py#L39)
2. [Reuse legacy body snapshots without IMAP.](test_email_reprocess.py#L65)
3. [Project all cache-policy states without recognition.](test_email_reprocess.py#L89)
4. [Preserve ignored, accepted and edited candidates.](test_email_reprocess.py#L116)
5. [Recheck review decisions made while generation is running.](test_email_reprocess.py#L128)
6. [Mark new candidates uncertain when protected cards exist.](test_email_reprocess.py#L147)
7. [Reject late rerun results after stop, revocation or exclusion.](test_email_reprocess.py#L164)
8. [Keep pending cards and the successful cache after failed updates; reuse that cache.](test_email_reprocess.py#L186)
9. [Preserve saved workspace tasks after successful reprocessing.](test_email_reprocess.py#L214)
10. [Update outdated results across the full range; skip excluded/current/unknown items.](test_email_reprocess.py#L263)
11. [Include unknown policy versions only by explicit choice.](test_email_reprocess.py#L283)
12. [Reject stale confirmation after rules or selection change.](test_email_reprocess.py#L292)
13. [Enforce snapshot ownership and explicit processing consent.](test_email_reprocess.py#L302)
14. [Keep successful results available after a failed bulk update.](test_email_reprocess.py#L313)
15. [Support ranges above 1,000 items without hydrating headers.](test_email_reprocess.py#L329)

### [test_email_telemetry.py](test_email_telemetry.py) — Content-free monitoring (10)

1. [Every writer closes connections after success and failure.](test_email_telemetry.py#L29)
2. [Every writer closes connections after success and failure.](test_email_telemetry.py#L55)
3. [Measure records success and failure elapsed and reraises.](test_email_telemetry.py#L68)
4. [Measure records success and failure elapsed and reraises.](test_email_telemetry.py#L85)
5. [Measure success/failure duration and rethrow the original exception.](test_email_telemetry.py#L102)
6. [Preserve aggregate statistics and 90-day retention.](test_email_telemetry.py#L133)
7. [Admin exclusions and no private fields.](test_email_telemetry.py#L143)
8. [Reject invalid tokens; read missing token/diagnostic tables without creating them.](test_email_telemetry.py#L155)
9. [Admin exclusions and no private fields.](test_email_telemetry.py#L165)
10. [Expose only aggregate diagnostic categories with account exclusions.](test_email_telemetry.py#L176)

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
| [import-flow.test.js](../frontend/tests/import-flow.test.js) | Consent/settings, local full-range sort/filter/pagination, selection, stale responses, job progress, stop/retry/update, scheduler-only mail history retries and notice review. Shared with other import sources. |
| [account-email-settings.test.js](../frontend/tests/account-email-settings.test.js) — 6 | Open/focus PKU settings; prevent late reopening or cross-account updates. |
| [connection-return-hint.test.js](../frontend/tests/connection-return-hint.test.js) — 7 | Safe return-to-settings hints, session changes and notification precedence. Includes teaching connections. |
| [email-diagnostics.test.js](../frontend/tests/email-diagnostics.test.js) — 3 | Safe diagnostic rendering, token coverage and overlapping-timing explanations. |

## Fixtures, load checks and maintenance

- [email_test_support.py](email_test_support.py): shared fake IMAP, no-task model result and fresh authenticated HTTP fixture; not a test suite. Workers drain before fixture cleanup.
- [benchmark_email_concurrency.py](benchmark_email_concurrency.py): separate synthetic load benchmark; not counted among the 137 tests. Workload, limits and recorded results: [capacity guide](docs/email-capacity.md).
- Add/rename/remove a test: update its one-line entry, count and source link here. Keep race scenarios separate when they guard different pipeline stages. Add a second-layer test only when it checks transport, persistence or another distinct contract.
- Historical execution results stay in the capacity guide. This index describes coverage, not the latest run status.
