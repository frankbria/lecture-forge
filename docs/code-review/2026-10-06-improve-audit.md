# Whole-repo audit (2026-10-06)

**Scope:** all of `src/lecture_forge/` at `d261105`, plus the test coverage gaps, tooling, dependencies and docs. Correctness and security were read in full; architecture and performance at medium depth. Not audited: test internals beyond coverage gaps, live provider behavior.

**Method:** three read-only audits; every finding re-checked against the code (several cited lines were wrong and were corrected or dropped); a fresh-context cold read of the two most complex plans before publishing.

## Findings → issues (in execution order)

Each issue carries a full implementation plan. `Depends on` is in each issue.

| Issue | Finding |
|---|---|
| #24 | No `AGENTS.md`: agents didn't know the spending rules (done with this record) |
| #25 | A case-only title change deletes the fresh MP3 on the case-insensitive Dropbox drive; the cost preview creates folders |
| #26 | `plan.yaml`/`render.json` written non-atomically; a torn marker blocks an episode; some failures show tracebacks |
| #33 | Scripts stay "done" after `plan.yaml` ranges change, so `render` can pay for the wrong content |
| #34 | The cost preview counts characters; the account is billed ~0.44 credits/char on `eleven_v3` (measured from ElevenLabs history) |
| #27 | A `---` mid-script silently cuts the lecture short |
| #28 | ffmpeg is checked only after credits are spent; render failure paths untested |
| #29 | Coverage script ran integration tests; REQUIREMENTS named mutagen (done with this record) |
| #30 | Direction: a series can continue another (chapter-to-chapter recap) |
| #31 | Direction: `--chapter N` from PDF bookmarks |
| #32 | Direction: move write/render decisions out of `cli.py` (GUI prerequisite; park until the GUI is decided) |

## Considered and rejected

So nobody re-audits these:

| Finding | Why not |
|---|---|
| Album folder collisions between same-titled series | Needs identical series *and* episode titles; per-chapter plans get distinct titles |
| A length ceiling for `run --auto --yes` | Opt-in flag combination, bounded by the balance check; `write` already flags scripts over 30 minutes |
| An env allowlist for the `claude -p` child | It has only the Read tool, in a temp dir; an allowlist risks breaking login or proxy variables |
| Offline tests of the Anthropic/OpenAI request builders | Would mock the SDK clients (no-mocking rule); the opt-in `paid` tests and #7 cover them |
| Cross-module imports between test files | Work under the current pytest rootdir setup |
| Running from any folder (`.env`/`series/` are cwd-relative) | Documented in the README; revisit with the GUI |
| Typecheck (mypy/pyright) | Low value at this size |
| Performance | TTS/LLM latency dominates; within-episode TTS must stay sequential for stitching |
| Dependencies | All used, none duplicated |
| Moving `episode_dir` out of `write.py` | Cosmetic coupling only |

Already tracked before this audit: #1, #5, #6, #7, #10, #12, #20, #22.
