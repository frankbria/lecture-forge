# Code Review — M1 core I/O (2026-10-05)

**Scope:** `src/lecture_forge/{source,style_guide,config,cli}.py` and their tests (branch `feat/m1-core-io`).
**Risk:** low to medium. This is a local CLI reading local files and holding API keys.
**Plan:** input validation at the file boundary, reliability, secret handling, test quality. Skipped OWASP web and ML (no server, no model code). LLM prompt-injection review is deferred to M2, where untrusted PDF text first reaches a model.

| # | Priority | Finding | Fix | Status |
|---|---|---|---|---|
| 1 | High | `Settings` repr included `elevenlabs_api_key`, so any traceback or debug print would leak it. | `field(repr=False)`, plus a test | Fixed |
| 2 | Medium | A corrupt `.pdf` raised `pymupdf.FileDataError` (a `RuntimeError`), which the CLI doesn't catch, so you got a traceback. | Wrapped as `ValueError` in `open_source` | Fixed |
| 3 | Medium | An encrypted PDF opened, then failed later with "document closed or encrypted". | Checked `needs_pass` up front with a clear message | Fixed |
| 4 | Medium | `read_text()` used the platform encoding, which would garble the em-dashes in the style guide on Windows (cp1252) once the GUI runs there. | `encoding="utf-8"` everywhere | Fixed |
| 5 | Low | Bookmarks with no target come back with page `-1` and would reach the planner. | Filtered out (`page >= 1`) | Fixed |
| 6 | Nit | A `parametrize` over a single value in `test_missing_file_is_rejected`. | Removed | Fixed |
| 7 | Nit | The Markdown outline ignores setext headings (`===` underlines) and `~~~` fences. | — | Accepted; add if a real source needs it |
| 8 | Note | Scanned PDFs (no text layer) extract as empty text. Only affects the OpenAI path, because Claude reads page images. | — | Marked `ponytail:` in code; OCR if needed |

**Carried to M2:** the `claude-code` provider must remove `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` from the environment it passes to `claude -p`, so runs bill your subscription and not the API key (see REQUIREMENTS, providers section).

**Result:** 30 tests pass, 99% coverage.
