# Lessons

Process and tooling lessons from working on this repo. Code and product follow-ups go to GitHub issues instead.

## 2026-10-05: completing PRs #2–#4 (stacked)

- **opencode can't run in parallel.** Concurrent `ask-opencode.sh` runs fail with `database is locked` or stall. Run cross-family reviews one after another. A stall (exit 75) means falling back to codex.
- **Don't `git add -A` on stacked branches.** An older branch's `.gitignore` may lack rules a later branch added, so untracked build output (`.coverage`, `__pycache__`) gets committed. Stage explicit paths.
- **Untracking a file deletes it from other checkouts.** Merging a `git rm --cached` commit makes `git pull` remove the file from any working tree whose HEAD still tracks it. After merging such a change, re-copy the file (the style guide's source is `D:\Dropbox\Claude\audio-lecture-style-guide.md`).
- **`codex review` reads the whole checkout,** `.env` included. Run it from a clean `git worktree` that has no secrets or private prompts. It also can't combine `--base` with custom instructions.
- **The pre-commit hook in `.git/hooks` applies to every branch.** Branches that predate `.pre-commit-config.yaml` need `PRE_COMMIT_ALLOW_NO_CONFIG=1`.
- **Check for conflict markers before `git rebase --continue`.** A failed file write followed by `git add` staged `.env.example` with `<<<<<<<` markers still in it (caught and amended before pushing). Run `git diff --check` or grep for markers after resolving.
- **opencode/GLM stalled on every review in this run** (exit 75, even run sequentially). codex did all three PRs. Re-test opencode before assuming it's back.
- **opencode stalls on review-sized prompts, not on everything** (2026-10-05, PR #9). A trivial "PONG" probe answered in seconds; the 37 KB review prompt stalled (exit 75). A passing probe doesn't mean a review will run. Go straight to codex for diffs over ~20 KB until that changes.
- **Keep demo checks that use regexes in script files** (2026-10-05, PR #11). An inline `python3 -c "…"` regex inside `showboat exec bash "…"` lost a backslash to shell quoting and crashed the step. Write the check as a `.py` file and exec that.

## 2026-10-06: completing PR #14

- **Before starting a milestone, check for open PRs and remote branches** (`gh pr list`, `git ls-remote --heads origin`). The M5 work was already open as PR #14 from the previous session, but `main` didn't show it. A second M5 was built from scratch, and its push was rejected by the existing `feat/m5-render`. Hours of duplicate work.
- **The spending policy lives on a branch until it merges.** The duplicate M5 demo spent ~5,100 ElevenLabs characters (more than the ~400 a demo is allowed) because the policy in REQUIREMENTS was only on PR #14's branch. Demos default to 0 characters: real CLI for cost, skip and refusal paths, plus a stand-in TTS returning ffmpeg-made MP3s through the real `render_episode`.
- **`showboat verify` re-executes every block.** On a stateful demo (renames, cache deletion) the diff fails as expected. Never run it on a demo whose blocks could spend money: keep `--yes` out of every block, so a re-run can't pay.
- **Use absolute paths for `rm` in Bash.** A `cd dir && … rm -rf rel/*` command is blocked whole by the harness safety check (the target can't be resolved), so the steps before it in the same command don't run either.

## 2026-10-07: implementing #25 (PR #36)

- **Work in a worktree when the owner runs the editable install.** `uv tool install --editable .` makes `lecture-forge` run the main checkout's code. #25 was first implemented on a branch in that checkout, including mutation checks that briefly broke `render.py`, while the owner was rendering paid episodes from it. Moved to a worktree mid-run; the rule is now in AGENTS.md.
- **A demo on the real filesystem type beats a unit test for platform bugs.** The case-only-rename bug only exists on case-insensitive drives. Linux tests can't show it. A scratch folder on `/mnt/c` (the same drvfs type as the Dropbox `D:`) reproduced it with main's code and verified the fix, at zero credits.
- **Prefer exact checks over heuristics a reviewer can break.** The plan's `casefold()` name compare had false positives (ß/ss) and left duplicates on case-sensitive drives. `os.path.samefile`, verified on the real drive first, is exact.

## 2026-10-07: implementing #26 (PR #38)

- **The `feature-dev:code-reviewer` agent has no shell.** It couldn't run `git diff` or pytest, so it reviewed the post-change files and couldn't confirm which test assertions had changed. Paste the diff into its prompt (as the opencode review does), or use an agent type with Bash.
- **Demo error-path fixes before and after through the real CLI.** A torn marker, a read-only folder and a stand-in `claude` on PATH reproduced three tracebacks on main at zero cost, and showed the PR's `error:` lines. That is stronger evidence than the unit tests alone.


## 2026-10-08: implementing #33 (PR #41)

- **Never undo a mutation with `git checkout <file>`.** It resets the file to HEAD, so it also wiped the uncommitted fix the mutation was testing. A backup copy made just before the mutation saved it. Commit before mutation checks, or undo the mutation with the reverse edit.
- **opencode finished two reviews of ~20 KB diffs** (about 5 minutes each, run one after another in the background with `timeout 900`). The "go straight to codex over ~20 KB" lesson from PR #9 no longer holds. Try opencode first and fall back to codex on a stall.
- **Run zero-credit demos from a scratch folder.** `load_settings` reads `.env` from the current folder, so a folder without one loads no real keys. The installed `lecture-forge` (main) and the worktree's `.venv/bin/lecture-forge` (the branch) give before/after runs side by side. A stand-in `claude` on PATH replaces the model.

## 2026-10-08: implementing #34 (PR #44)

- **Demo ElevenLabs paths with a driver script, not the CLI binary.** The CLI's balance read is a real API call, and a stand-in on PATH can't replace it. A small `drive.py` replaced `render.characters_left` and `render.elevenlabs_tts`, then called `cli.main`. The balance lived in a JSON file and dropped 0.44 per character sent. Run with each checkout's `.venv/bin/python`, it showed main's refusal next to the branch's credit preview at zero credits, `--yes` included, because nothing could reach ElevenLabs.
- **opencode reviews the checkout, not only the diff it is given.** With the worktree as its cwd, it found the commit made after the prompt's diff and reviewed the branch tip. Start it from the worktree after the last commit, or say in the PR comment which SHA it saw.
- **`code-reviewer` (an agent type with Bash) ran the tests itself** and gave concrete edge-case findings: replace-vs-accumulate, negative hand-edited values. Prefer it to `feature-dev:code-reviewer` (see #26).
- **ruff reformats tests between edits.** A scripted `str.replace` written against text you wrote earlier can miss after `ruff format` has wrapped it. Re-read the current text before a scripted edit.

## 2026-10-09: implementing #5 (PR #47)

- **Make the mutation helper fail loudly when the mutation doesn't apply.** `ruff format` had re-wrapped the target line, so the replace assertion failed. The helper went on to run pytest on the unmutated code anyway and printed "34 passed", which looked like a surviving mutant. Exit before pytest when the replace fails, and grep the formatted line first.
- **Probe a reviewer's example before using it as a test.** The internal review said `---\n# One\ntext\n---` should keep `One` and `Two`. Under CommonMark, `text` underlined by `---` is also an H2, so the first test expectation built from it was wrong. Running the example settled it.
- **`gh pr edit` and `gh pr merge` can fail on this repo.** `gh pr edit` hit the Projects (classic) GraphQL deprecation: it exited 8 and left the body unchanged without saying so. `gh pr merge` can't delete a branch checked out in a worktree. Use `gh api -X PATCH repos/frankbria/lecture-forge/pulls/N -F body=@file` and `gh api -X PUT …/pulls/N/merge -f merge_method=squash`, then remove the worktree and delete the branch.
- **opencode stalled on a 6 KB re-review** (exit 75) after finishing round 1 normally. codex from the worktree (no `.env` there, since it's untracked) worked as the fallback. Stalls aren't tied to prompt size.

## 2026-10-09: implementing #6 (PR #50)

- **Run `git status` before every mutation loop, not just once per branch.** The #33 lesson ("commit before mutation checks") was broken again. A review fix was still uncommitted when a mutation loop ran `git checkout <file>`, which wiped it. It was caught only because the next grep showed the old threshold. Make "the tree is clean" the loop's first line: `git diff --quiet || { echo "commit first"; exit 1; }`.
- **A one-line mutation guard can invert itself.** `sys.exit(0) if … and not p.write_text(…) else sys.exit(1)` reports "did not apply" after writing the mutation, because `write_text` returns the character count (truthy). Seven mutations were written and left in the tree. Use a small script that checks the count, writes, and exits 0 (in the session scratchpad as `mutate.py`).
- **Don't commit in the worktree while opencode reviews it.** opencode reads the checkout, so it noticed the branch move and reviewed the new tip. That's useful, but the record must name the SHA it actually saw, not the one in the prompt.
- **Check what "no text layer" means on real scans before coding it.** Strict "no text and an image" missed scans with a stamped page number or scanner footer, and the first threshold (3 words) missed firmware footers. Image coverage from `get_image_info()` (which sees Form XObjects) plus a word limit held up against the reviewers' probes.

## 2026-10-09: implementing #10 (PR #52)

- **`pytest … | tail -1 && git commit` commits a red tree.** The chain sees `tail`'s exit status (0), so a failing suite went on to commit and push (`8fe8fc3`, fixed in the next commit). Write pytest's output to a file and test its own exit code, or run under `set -o pipefail`.
- **Build demo inputs so the code path keeps what you're testing.** A 51 MB "fat" PDF padded with a file attachment shrank to a few KB, because `plan` sends a `slice_pdf` page slice and attachments aren't copied. Make size come from page content (noise images don't compress).
- **A stale `.git/index.lock` appeared in the main checkout mid-session** (zero bytes, no git process) and blocked the post-merge `git pull`. Check `pgrep -x git` before removing one: the owner may be running git there.
- **Check provider limits against current docs, not memory.** context7 has the Claude API limits (and the `claude-api` skill lists them), but not OpenAI's PDF limits. A web search found the current file-inputs guide: 50 MB per file, with no page limit any more. Older posts still cite 100 pages / 32 MB. mcporter's tool names here are `context7.resolve-library-id`, `context7.query-docs` and `tavily.tavily-search` (hyphens), not `context7.query` / `tavily.tavily_search`.
- **Point SDK base URLs at a closed port for zero-cost demos of API paths.** `ANTHROPIC_BASE_URL` and `OPENAI_BASE_URL` set to `http://127.0.0.1:9` turn any upload attempt into an immediate "Connection error", which proves whether a request would have been sent. No real key, no network.

## 2026-10-09: implementing #12 (PR #55)

- **A stand-in model only proves what it is written to do.** The demo stand-in wrote the restated summary on its own line, so the inline form a real model may use went untested until a side note asked about it. A one-flag variant (inline) then showed the summary leaking into the *spoken* script. When a fix depends on a model's output format, make the stand-in emit each plausible form.
- **Check what a stand-in keys on.** It decided "the draft mentions Lagrange" by searching the whole critique prompt, which also holds the source notes ("# Lagrange"), so the "nothing cut" scene showed a restate. Match only the part of the prompt the real model would judge.
- **Extract a reviewer's verbatim text with Python, not a `sed` line anchor.** opencode's output often puts the "## Review" heading mid-line after its progress chatter. `sed -n '/^## Review/,$p'` then captures nothing, and a review comment went out without the review (twice: #6 and #12). `raw[raw.index("## Review"):]` works.
- **`gh issue create` takes no `--jq`.** The call failed, a fallback `||` printed an unrelated issue, and it looked like success. Check the created URL.
- **Prefer a marked-heading rule to a strict one for model output.** "EPISODE SUMMARY counts only on its own line" blocked commentary false positives, but it also blocked the instructed inline form. "In capitals or as a `#` heading" separates the instructed heading from prose.

## 2026-10-10: the queue #27, #28, #32, #58, #59, #60 (PRs #62-#69)

- #27: opencode stalled (exit 75) on a 7 KB post-PR re-review right after a clean first round; codex from the worktree worked. Pattern now seen on #5, #27: re-reviews stall more than first reviews.
- #28: an issue plan's own mutation check can be untestable as specified: garbage input makes ffmpeg fail before it writes any .part, so the "delete tmp.unlink" mutant survived. Probing the failure modes directly (good+bad pieces) found a real bug instead: ffmpeg's concat exits 0 and writes a truncated file when a later piece is bad. When a planned mutant survives, probe the real tool's behavior before changing the test.
- #28: opencode stalled (exit 75) on the post-PR re-review again, the second issue in a row (#27, #28), after a clean first round each time. codex from the worktree (no .env) was a clean fallback both times.
- #28: `PATH=x cmd` also changes how bash finds `cmd` itself, so `PATH=$EMPTY script -qec ...` found no `script` and printed nothing. Put the PATH change inside the command (`script -qec "env PATH=$EMPTY ..."`).
- #32: scope every edit anchor to its own function. `s[s.index(A):s.index(B)]` with B also matching earlier in the file gives an empty slice, and `s.replace("", new, 1)` then inserts `new` at the top of the file (cli.py was corrupted; restored with `git checkout` because nothing in it was uncommitted). Search B only after A.
- #32: a comparison must fail when a run fails. The first main-vs-branch diff said "IDENTICAL" because both runs died the same way (unexported shell variables gave "command not found"). The check now greps for "command not found|Traceback" before diffing.
- #32: opencode stalled on a 23 KB *first* review too (exit 75), and then finished the post-PR review of the same diff. Stalls aren't tied to first or repeat reviews or to size; keep codex ready every time.
- #58: opencode stalled (exit 75) on 5 of the last 6 reviews (#27 post, #28 post, #32 pre, #58 pre and round 2); it completed only #32 post and #58 round 1. Start opencode and codex together, or go straight to codex when opencode has stalled twice in a row in a session; waiting 3+ minutes for each stall adds up.
- #58: make a demo's decoy realistic enough to slip past validation. A 2-line decoy notes.md was rejected by plan validation ("lines 1-30 outside 1-2"), which hid the real danger. A same-length decoy showed main silently opening the wrong book.
- #58: the reviewer that found the Major (opencode, a hand-edited relative plan.yaml source) did so by questioning an accepted trade-off whose premise the PR changed ("cwd == project root"). When a change removes an assumption, re-read the accepted trade-offs in docs/code-review/ that rest on it.
- #60: when earlier work changes an issue's premises, re-read its definition of done before coding. #60 asked for an engine-owned run chain, written before #32 landed. Afterwards almost nothing was left to move, and the owner's "one engine, two front ends" pointed to front ends composing calls. That was a genuine design fork, so I asked: a one-line question saved a large restructure. The decision went on the issue as a comment, not a body rewrite (rewriting #32's body had been denied).
- #60: a stale .git/index.lock showed up in the main checkout twice right after the merge-and-cleanup chain (#10, #60), with zero bytes and no git process in this repo. Another session's `git commit` was running in a *different* repo (`pgrep -x git` showed it). Check each git process's folder (`readlink /proc/<pid>/cwd`) before deciding a lock is stale; only then remove it.

## 2026-10-10: implementing #22 (PR #71)

- **Never build a PR comment in an unquoted heredoc.** A Python script inside `<<EOF` held the text `` `codex review --base origin/main` ``. The shell ran it as a command substitution: a second codex review started, and its verdict replaced the command name in the posted comment (fixed with a PATCH). Write comment bodies with the Write tool or a quoted `<<'EOF'`, and pass paths in as arguments.
- **opencode finished a 5 KB review in about 4 minutes while another session's opencode was running.** The "can't run in parallel" lesson (2026-10-05) didn't hold this time. codex, started at the same moment, finished first; starting both together cost nothing.
- **Add a new entry point to an installed tool with `uv tool install --editable . --reinstall-package lecture-forge`.** Tried first in a scratch tool folder (`UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`), it created `lforge` and left `uv pip freeze` identical. A tool install doesn't use `uv.lock`, so compare the freeze before and after any reinstall of the owner's tool, and check `pgrep` for a running render first.
- **A subprocess test of the installed scripts covers what an in-process test can't.** `Path(sys.executable).parent / name` runs the real entry point, so one test fails for both a missing `[project.scripts]` line and a fixed `prog`. Don't add a skip when the script is missing: that is the failure it exists to catch.

## 2026-10-10: implementing #7 (PR #73)

- **The agent session can't run paid or key-bearing tests, even with the owner's go-ahead on the issue.** Its permission classifier refused a command that set `LECTURE_FORGE_PAID_TESTS=1` (a `--collect-only`, so nothing would have been sent) and then a plain `pytest -m integration -k openai` with the real `.env` in reach. Don't look for another route: ask the owner at the start to run the one command with the `!` prefix, so its output lands in the session. Do the free checks first (the SDK's request types and model list in `.venv`, `--collect-only` without the flag to see what would run).
- **A verify-only issue that passes ends as a docs-only PR.** Record the result where the spec is (REQUIREMENTS) and close the note that asked for it (`docs/code-review/`). There is nothing for a mutation check, a deslop scan or a Showboat demo to act on, and a re-executable demo block must not hold a paid command. The owner's run, quoted in the PR, is the evidence.
- **opencode and codex both finished a 2-line docs review when started together** (codex in about 2 minutes, opencode in about 3).
