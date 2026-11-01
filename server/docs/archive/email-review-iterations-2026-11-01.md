# Email review iterations — 2026-11-01

Baseline: `4e5e30d`. These are three self-review/fix passes by the implementation author; independent collaborator review and real mailbox/model acceptance remain pending.

The initial reported defects were restored-summary retries without item IDs, and unbounded MIME recursion failing healthy siblings. The final retry design uses the existing durable job endpoint; no client-side item hydration is needed.

| Pass | Scope and finding | Fix and focused evidence |
| --- | --- | --- |
| 1 | Retry transport, consent and large selections: explicit reprocess ID lists capped failed-job retries at 1,000. | Retry by parent job ID with explicit consent. Server selects unfinished items; successful/excluded items are skipped. A 1,003-item fixture retries 1,001 failures and replays its operation ID without IMAP/model calls during admission. UI summaries omit items, matching production. |
| 2 | Source parsing and per-message failures: oversized headers and invalid Base64 escaped prefetch and failed healthy siblings. | Depth limit plus per-message 422 handling. Grouped HTTP regression verifies one failed/one successful item for deep MIME, oversized header and invalid Base64. |
| 3 | Idempotency, account/login boundaries and cancellation: an unsuccessful retry could reuse its operation ID for a different parent. | Scope the retry ID to its parent; preserve it on same-parent retries. Check foreign parent (404), revoked consent (400), changed login (409), plus existing concurrency/stop regressions. |

Only synthetic accounts, temporary databases and fake IMAP/model responses were used. No private-email replay, real mailbox access, deployment or merge was performed. Final validation results are recorded in PR #195; test definitions are indexed in [EMAIL_TESTS.md](../../EMAIL_TESTS.md).

Final local checks: 166 email-related backend tests (140 importer + 26 account verification), 101 shared regressions, 442 frontend tests, production build and git diff --check passed. All 140 backend test-index links point to test definitions. No real mailbox/model E2E or remote CI result is claimed.
