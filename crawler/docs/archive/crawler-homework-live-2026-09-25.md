# Homework extraction live validation — 2026-09-25

The authorized campus-card login completed through PKU unified authentication. The test read the current term's course announcements and linked content lists without submitting coursework. Credentials, cookies, and private page snapshots are not included in this report or in test fixtures.

## Before and after

Both runs checked 11 courses and 8 content sections. Full-body text splitting returned 32 candidates, including navigation labels, prose fragments, and duplicate attachment titles. The structural extractor returned 10 candidates: 1 with a parsed future deadline and 9 with no resolved deadline. The future assignment retained its actual heading and the same 2026-10-19 23:59 Asia/Shanghai deadline, instead of using the submission paragraph as its title. The corrected run started at 2026-09-25 23:43:24 Asia/Shanghai.

Manual inspection of the returned titles confirmed that the previously observed navigation labels and prose fragments no longer appear as separate records. Native assignments with short titles lacking homework keywords are retained. Chapter homework bodies and attachment names remain within their corresponding item records.

## Automated coverage and limits

Synthetic Blackboard-shaped fixtures cover native assignments, Lab titles, body paragraphs, attachments, navigation and folders, announcement metadata, deadline conflicts, and source-item deduplication. No private HTML was copied into fixtures.

This validates the observed listing-page extraction problems, not exhaustive homework discovery. Attachment contents, nested folders, and additional detail pages are not inspected. A keyword-matched item can be a homework-related resource rather than a submission task; downstream review is still required. Unresolved deadlines are retained for later processing.
