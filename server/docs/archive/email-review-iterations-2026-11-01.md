# Email review iterations — 2026-11-01

Passes 1–3 baseline: `4e5e30d`; passes 4–6 baseline: `fb03105`. These are six self-review/fix passes by the implementation author; independent collaborator review and real mailbox/model acceptance remain pending.

The initial reported defects were restored-summary retries without item IDs, and unbounded MIME recursion failing healthy siblings. The final retry design uses the existing durable job endpoint; no client-side item hydration is needed.

| Pass | Scope and finding | Fix and focused evidence |
| --- | --- | --- |
| 1 | Retry transport, consent and large selections: explicit reprocess ID lists capped failed-job retries at 1,000. | Retry by parent job ID with explicit consent. Server selects unfinished items; successful/excluded items are skipped. A 1,003-item fixture retries 1,001 failures and replays its operation ID without IMAP/model calls during admission. UI summaries omit items, matching production. |
| 2 | Source parsing and per-message failures: oversized headers and invalid Base64 escaped prefetch and failed healthy siblings. | Depth limit plus per-message 422 handling. Grouped HTTP regression verifies one failed/one successful item for deep MIME, oversized header and invalid Base64. |
| 3 | Idempotency, account/login boundaries and cancellation: an unsuccessful retry could reuse its operation ID for a different parent. | Scope the retry ID to its parent; preserve it on same-parent retries. Check foreign parent (404), revoked consent (400), changed login (409), plus existing concurrency/stop regressions. |
| 4 | Source decoding and wire bounds: unsupported charset decoders could fail healthy siblings; header limits were enforced only after receipt. | Per-message charset failure handling; partial header FETCH requests capped at safety limit + 1. Synthetic charset regression and both single/batched header wire assertions pass. |
| 5 | Real IMAP wrapper admission: local pool expiry became a generic connection failure, terminating the job. | Typed local admission error preserved through the wrapper; coordinator/worker deferral, safe header HTTP 503, no model/SSL calls while blocked, successful resume and one-call terminal network timeout verified. |
| 6 | Async frontend control ordering: background summaries could invalidate accepted retry/stop/start/update responses. | Reads defer to mutations; manual reads still discover newer jobs; request-owned poll marker and polling recovery after failed stop. Grouped regression covers four mutation races and stale-read draining. |

Only synthetic accounts, temporary databases and fake IMAP/model responses were used. No private-email replay, real mailbox access, deployment or merge was performed. Final validation results are recorded in PR #195; test definitions are indexed in [EMAIL_TESTS.md](../../EMAIL_TESTS.md).

Passes 1–3 local checks: 166 email-related backend tests (140 importer + 26 account verification), 101 shared regressions, 442 frontend tests, production build and git diff --check passed. All 140 backend test-index links point to test definitions. No real mailbox/model E2E or remote CI result is claimed.

Passes 4–6 local checks: 167 email-related backend tests (141 importer + 26 account verification), 101 shared regressions, 443 frontend tests, production build and git diff --check passed. All 141 backend index links point to test definitions. These remain synthetic checks, not real mailbox/model acceptance or remote CI evidence.
