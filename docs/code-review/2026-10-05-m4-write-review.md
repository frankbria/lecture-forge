# Code Review — M4 `write` (2026-10-05)

**Scope:** `src/lecture_forge/write.py`, `load_plan` in `plan.py`, the `write` command in `cli.py`, `tests/test_write.py`.
**Risk:** medium–high. Model output becomes **spoken audio** in your voice, and it carries continuity between episodes.
**Plan:** model output that becomes speech (OWASP LLM02), continuity correctness, data integrity on crashes and `--force`, re-validating your edited plan, prompt injection via the source. Skipped web and ML.

## Found by real full-length runs (Claude Code, your style guide)
| # | Priority | Finding | Resolution | Status |
|---|---|---|---|---|
| 1 | **Critical** | The critique model added `---` and then "I couldn't render the PDF as page images…" after the revised script. It landed in `script.txt` and **would have been read aloud**. | The script is cut at the first markdown rule line (`---`, `***`, `___`, `- - -`); the guide bans markdown in scripts anyway. The critique addendum now says "nothing after it: no separator, no remarks". Regression test uses the real tail. | Fixed |
| 2 | High | False "writer suggests a split" warning: any notes line containing "split" matched ("cosets split a group into equal-sized pieces"). | Only the `Suggested split point` field is read; regression tests use the real notes format. | Fixed |
| 3 | — | Item 1's remark suggested Claude Code saw only the PDF text layer, which would hurt equations. | **Checked, not real:** an image-only PDF page (no text layer) was read exactly. No poppler needed. | No change |

## Review of the diff
| # | Priority | Finding | Resolution | Status |
|---|---|---|---|---|
| 4 | High | A crash or failure mid-episode must not look like a finished episode. | `script.txt` is written **last**, and its existence means done. `draft.md`/`notes.md` are kept when pass 2 fails. Tested. | Fixed |
| 5 | High | Your edited `plan.yaml` is input you control, and edits can break it. | `load_plan` re-validates n (unique, positive), titles, ranges against the source, YAML syntax and whether the source still opens, with readable errors. | Fixed |
| 6 | Medium | Model output can lack sections or be truncated. | One repair round for a draft missing SCRIPT/PUZZLE ANSWER/EPISODE SUMMARY, or a revised script under half the draft's length, then `WriteError`. | Fixed |
| 7 | Medium | Continuity could drift: the critique may change the closing puzzle. | The critique must restate a changed puzzle under PUZZLE ANSWER, which overrides the draft's. Tested. A live 2-episode run confirmed episode 2 answers episode 1's puzzle. | Fixed |
| 8 | Low | A spoken line beginning exactly "Puzzle answer:" or "Episode summary:" would be read as a heading. | — | Accepted: unnatural phrasing for a lecture; the repair round catches a resulting empty section |
| 9 | Low | `summary.md` comes from the draft, but the critique may trim content. | — | Accepted: the critique revises wording, not what the episode established |
| 10 | Low | A relative `source:` you type into `plan.yaml` resolves against the working directory. | — | Accepted: `plan` writes absolute paths |
| 11 | Low | A `--force` rewrite that fails after pass 1 leaves the new draft/notes next to the old script/summary. | — | Accepted: `script.txt`, `summary.md` and `puzzle_answer.md` stay mutually consistent, which is what the next episode reads |
| 12 | Note | Source content could try to steer the writer. | The writer runs with Read only, in a temp dir holding just the episode's pages. Output is text you review before rendering. | Accepted |

**Result:** 151 unit tests, plus integration tests that run a real two-pass write through Claude Code. A live 2-episode series: **4,031 and 3,969 words (~27 and ~26 min)**, no markdown, no banned phrases, opening problems as the guide asks, and episode 2 closes the loop on episode 1's puzzle.
