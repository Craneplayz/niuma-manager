# Email service capacity and operation

The target workload is 30 signed-in accounts, including 30 simultaneous imports of 50 messages. This is a workload to validate, not a promise of 30 simultaneous model generations. Qiniu latency and quota still bound throughput. Synthetic checks do not establish real mailbox/model E2E success.

## Scheduling and persistence

Email message work is scheduled round-robin across app accounts. Initial limits are eight concurrent email workers and two per app account; one unfinished job per account remains enforced. The shared business model pool continues to bound all features together. Deferred email work retains a bounded FIFO admission ticket without retaining an execution thread, so continuous interactive requests cannot repeatedly jump ahead. Email does not bypass that pool. Capacity waiting and explicit provider429 responses defer work without treating it as an extraction failure. Provider429 retries are bounded; timeouts and ambiguous network failures are not automatically replayed.

Admission is transactional and bounded at 60 unfinished jobs. Existing operation IDs remain idempotent even when capacity is full. A refused new submission receives a retryable HTTP response and Retry-After. Whole-range selection is preserved; the application must not silently truncate the user's chosen range.

Short process-local SQLite write admission prioritizes submissions and stop/settings controls over background progress, with bounded priority bursts so progress still advances. Stopped coordinators draining already-started calls still count toward admission. Item progress lives in normalized email_import_job_items rows; older JSON snapshots migrate in place. Job metadata/counters/revisions remain durable. Summary polling omits message identifiers, item details and content. The UI fetches details when a revision changes, backs off unchanged polling and pauses hidden tabs. Existing cached sorting/filtering/pagination remain local.

Only one scheduler-owning application process is supported per database. Startup recovery preserves completed items and marks unfinished work interrupted for explicit retry, rather than automatically resending saved private content. Do not scale replicas until all workers and model/provider limits have shared ownership and fencing. A reverse proxy does not make the Python standard-library HTTP server production-grade; serving hardening remains a separate deployment concern.

## Implementation boundaries and concurrency review

The email service keeps the existing `email_import` application entry points:

| Module | Responsibility |
| --- | --- |
| `server/email_import_routes.py` | HTTP input validation, endpoint dispatch and public response construction |
| `server/email_import_store.py` | Email schema/migration, normalized job/item storage, transactions, revisions and pending-review queries |
| `server/email_import.py` | Consent/cache policy, account guards, state projection and background import orchestration |
| `server/email_scheduler.py` | Account fairness, bounded message execution and scheduler process ownership |
| `server/email_mailbox_pool.py` | Short-lived IMAP sessions and bounded body-fetch/consumption reservations |
| `server/model_queue.py` | Shared model-call admission and provider quotas across business features |
| `server/email_db_gate.py` | Short process-local email write admission; SQLite remains the transaction authority |

Routes use the same runtime policy helpers as execution; they do not implement an independent consent or cache policy. Preview and full-range filtering share one cache/progress projection. Full snapshot saves and incremental progress updates share one item-count definition. Schema extraction preserves the existing tables and legacy snapshot migration; it does not introduce a second migration system. The application facade lazily loads the routes module to avoid an import-time dependency cycle. Existing consent and recognition-guard callers keep their entry points.

The concurrency review retains the following distinct boundaries rather than merging unlike resources:

- Job admission bounds accepted coordinators, including stopped jobs still draining calls. It does not authorize additional model execution.
- The dispatcher bounds active email work and enforces account fairness. Its per-account limit also controls coordinator prefetch/dispatch, using the same limit reader.
- IMAP and body reservations bound mailbox connections and transient content memory independently of model availability. Body fetch reservations end before model dispatch; consumption reservations cover loading saved content for recognition.
- The shared model queue bounds calls from all features. Deferred email tickets release dispatcher capacity while waiting; provider budgets separately enforce configured request/token windows and explicit throttle cooldowns.
- The ownership lease prevents another API process from admitting email work for the same database. It is not a distributed worker system.
- The database gate reduces competition between user controls and background progress. It does not replace transactions or serialize writes from other features. Retain it because the synthetic concurrent-submission workload exposed SQLite write contention; removal needs a comparable workload check.

No new queue backend or production-serving dependency is introduced by this cleanup. Shared jobs, multi-process workers, framework/server replacement and repository-wide database migrations need a separate architecture decision with collaborators.

## Recovery and operational checks

Before deployment, confirm there is one scheduler-owning API process per database and verify the isolated environment's capacity profile. Do not configure multiple replicas against this scheduler. Existing model configuration remains environment scoped; do not copy secrets from another profile.

Process-owner checks are read-only: Windows uses a synchronize-only process handle, never `os.kill(pid, 0)`, which terminates processes on Windows. A second instance must leave a live owner running. Dispatcher shutdown cancels all queued work without dispatching it and drains already running work. Provider quota is refunded only when every started model attempt reported completed usage; an unreported timeout after an output-truncation retry retains the conservative reservation.

Telemetry writers explicitly close their SQLite connections after either commit or rollback. SQLite's transaction context alone does not close a connection; diagnostics must not retain handles until garbage collection or prevent test database cleanup on Windows.

On restart, completed recognition remains reusable and queued/processing items become interrupted. A user must explicitly retry unfinished work; startup never automatically retransmits saved private mail. Stop prevents new dispatch and late result persistence but cannot recall a provider request already sent. Keep stopped/draining jobs in the admission count until their coordinators exit. Retry and cache updates retain operation-ID idempotency and reviewed decisions.

If latency rises, inspect dispatch waiting, shared model waiting, provider backoff, generation, IMAP and database waiting independently. A full model pool calls for provider/capacity diagnosis, not additional API replicas. An interrupted job calls for explicit retry after checking service health. Consent revocation, exclusion, connection changes and disconnect still purge affected pending body snapshots and block their use.

## Isolated environment configuration

Set the following numeric fields in the selected environment config.json. Older profiles receive defaults. manage_environment.py propagates profile values into native and Compose environments and removes inherited NIUMA_* shell overrides. Restart the API after changing capacity settings. Never copy model credentials between environments.

| Profile field | Default | Meaning |
| --- | ---: | --- |
| email_concurrency | 2 | Per-account email concurrency |
| email_global_concurrency | 8 | Global email execution cap |
| email_max_jobs | 60 | Accepted unfinished jobs |
| email_imap_concurrency | 4 | IMAP connections including header refresh |
| email_prefetch | 16 | Body buffer count limit |
| email_prefetch_bytes | 2097152 | Body literal byte budget; Python object overhead is additional |
| model_requests_per_minute | 0 | Provider request budget; zero means unconfigured |
| model_tokens_per_minute | 0 | Provider token budget; zero means unconfigured |

Provider quotas are shared by local business features, keyed by endpoint/model identity. The local TPM admission estimate is a conservative UTF8-byte bound including prompt/schema/framing and output allowances; it is not the provider's tokenizer or reported token usage. Recognition reserves capacity for its permitted 8K plus 16K truncation fallback, then settles known usage. Unknown usage remains reserved for the window. Planning/decomposition settle usage when the provider reports it. Configure budgets against actual provider quotas with appropriate margin; no Qiniu quota has been assumed or verified by these changes. Remote harnesses have their own provider admission boundary and require equivalent controls there.

Mailbox sessions remain read-only and use BODY.PEEK. Small batches reuse connections, but sessions close before model work. Consent, exclusions, stop and credential guards still apply per body and before dispatch/persistence. Buffered content is account scoped and bounded. Private pending body snapshots support retry without an IMAP reread; revoking consent, excluding a message, changing credentials or disconnecting purges the affected staged snapshots. Stopping a job retains its snapshots for explicit retry. Completed recognition history follows the existing retention behavior. Header refreshes coalesce only matching account/range/credential work; cached reads need no mailbox session.

## Monitoring

The existing administrator usage endpoint includes emailCapacity: process-local aggregate scheduler, mailbox, provider and bounded status-response gauges. These gauges reset on restart and are independent of the selected historical date range. They contain no user IDs, mailbox addresses, subjects, bodies or model text. Stage metrics retain the existing content-free 90-day policy. Token usage remains based on provider-reported fields, with missing values shown as unknown. Telemetry write loss is counted rather than silently interpreted as zero activity.

Monitor first-result latency, dispatch/model/provider waiting, generation/validation time, DB lock/write time, IMAP connection time, status-response P95/bytes, provider429s, cache hits and token consumption separately. Overlapping stage totals must not be added as wall-clock duration.

## Reproducible synthetic load checks

Run only against the temporary server/database created by the benchmark; it never uses real credentials, IMAP or a paid model:

```sh
.venv/bin/python server/benchmark_email_concurrency.py --users 30 --mails 50 --model-seconds 2 --output /tmp/niuma-email-load.json
```

The harness seeds synthetic authenticated accounts, runs concurrent HTTP submissions/status polling, and replaces SSL IMAP/model implementations with local fixtures. Its report contains aggregate timings/counts only. It measures scheduler, DB and HTTP overhead under a controlled model delay; it does not measure actual Qiniu latency, remote quotas, task accuracy or production host capacity.

Initial acceptance goals on the recorded test host: cached/status/submission P95 under 500 ms; every user gets a first result within 15 seconds with a two-second synthetic model and fast fake IMAP; global/per-account limits hold; no duplicate successful recognition, starvation, cross-account leakage or unhandled SQLite lock failures. Validate cache-only repeat imports, stop/revoke/exclusion/rotation, provider throttling and restart recovery separately. Record workload, host, source state and actual results before claiming capacity. At eight workers and ten seconds per model call, 1,500 uncached emails require about 31 minutes of ideal model time alone.

## Synthetic evidence and test index

The [email test index](../EMAIL_TESTS.md) lists current cases and commands. Full feature acceptance still requires real mailbox/model validation.

Archived synthetic reports retain their own workload, host and source hashes:

- [30 accounts × 50 messages, two-second fake model](archive/email-capacity-30-users-2026-10-20.json).
- [Short-generation stress run](archive/email-capacity-stress-2026-10-20.json).
- [Post-refactor regression load](archive/email-cleanup-capacity-2026-10-20.json).

These reports do not establish real provider quota, extraction accuracy or production-host capacity. No private mailbox content or raw provider output is included. Current validation results and remaining acceptance gaps belong in the feature PR.
