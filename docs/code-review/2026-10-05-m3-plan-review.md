# Code Review — M3 `plan` (2026-10-05)

**Scope:** `src/lecture_forge/plan.py`, the `plan` command in `cli.py`, `tests/test_plan.py`.
**Risk:** medium. LLM output becomes a file you edit and later steps consume, and `--series` becomes a directory name.
**Plan:** model output as untrusted input (OWASP LLM02), path safety, protecting your edits, reliability of the planner loop, test trustworthiness. Skipped web and ML.

| # | Priority | Finding | Resolution | Status |
|---|---|---|---|---|
| 1 | High | `--series` is used as a directory name, so `../x` or `a/b` could write outside `series/`. | Strict slug pattern `[a-z0-9][a-z0-9_-]*`; test checks that nothing is created outside. | Fixed |
| 2 | High | Overwriting `plan.yaml` would destroy your edits. | Refused without `--force`, checked **before** the LLM call (no wasted call) and again at write time. | Fixed |
| 3 | High | The plan is LLM output: ranges, types and timing can't be trusted. | `validate()` checks ranges against the source, order, overlap, minutes ≤ 30 and required fields. One repair round gets the exact problems, then it fails. | Fixed |
| 4 | Medium | `source:` was stored as a relative path, so `write` (M4) would break when run from another directory. | Stored with `resolve()`, plus a test. | Fixed |
| 5 | Medium | Free-text fields (`break_reason`, `listener_notes`, `title`, `author`) were written as whatever type the model returned. | Coerced to strings, plus a test. | Fixed |
| 6 | Medium | Integration test flakiness: requiring every episode to start at page 1 or 4 would fail a legitimate finer split. | Asserts the real property: no episode straddles the topic boundary. | Fixed |
| 7 | Low | `--range` page numbers vs. the sliced PDF: the model could report slice-relative pages. | The prompt states the offset ("first page of the attached PDF is source page N"), the word table uses absolute numbers, and validation rejects anything outside the range. A shift *within* the range can't be detected automatically, which is what the approval gate is for. | Mitigated |
| 8 | Note | LLM titles will become MP3 filenames in M5. | Sanitizing requirement added to REQUIREMENTS (render). | Deferred to M5 |
| 9 | Note | Whole-book planning on `anthropic`/`openai` can exceed their per-request PDF limits. The API's 400 becomes a non-retryable error that offers a switch to `claude-code` (which reads pages incrementally). `--range` per chapter also works. | — | Accepted |
| 10 | Note | `source_digest` reopens the PDF per page. | Marked `ponytail:`; fine at book scale. | Accepted |

**Result:** 109 passed, 1 skipped (OpenAI, no key), 97% coverage. A real planner run (Claude Code, a bookmark-free PDF) found the groups/rings boundary from content alone and produced two ~25-minute episodes with sensible `break_reason`s and listener notes.
