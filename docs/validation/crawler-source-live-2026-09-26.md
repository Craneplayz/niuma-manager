# Campus source collection and v0.2 export validation

Authorized read-only campus-card authentication and collection completed on 2026-09-26, starting at 09:47:30 Asia/Shanghai. Private page snapshots, credentials, cookies, and source bodies are not included in this report or in test fixtures. The exported requests were checked locally with the existing `ddl_text.contract_v02.validate_request`; they were not sent to a model or recognition service.

## Live results

- 11 current-term courses and 8 linked content sections inspected.
- 40 full source records: 4 announcements, 7 native assignments, 29 content items.
- Text quality: 3 complete listing texts, 18 partial records, 19 title-only summaries.
- Publication metadata: 4 source datetime values, 36 unknown values. Collection time did not fill missing publication times.
- All 40 source records exported in one valid v0.2 request. Non-homework course-group/class announcements were retained.
- Optional legacy views: 10 homework candidates, 1 upcoming deadline. These views do not filter recognition input.
- All linked reference contents were marked unread.

## Scope and checks

The collector uses listing-item boundaries, keeps titles/body paragraphs/reference metadata together, and excludes page navigation and folder links. The converter preserves source evidence and declared completeness, batches at 100 records per request, and makes no task or deadline decisions. Unit tests use synthetic HTML and test the CLI export path with a mocked collector; interoperability tests invoke the unmodified Python contract validator.

No files in `skills/`, `ddl_text/`, `frontend/`, or `server/` were changed. The source module, converter, CLI, tests, and crawler documentation are the scope of this change.

This is not exhaustive course discovery: nested folders, attachment contents, images, and additional detail pages are not read. An exported record can be a resource rather than a task. Interpretation remains the downstream recognition module's responsibility.
