# Lessons

Process and tooling lessons from working on this repo. Code and product follow-ups go to GitHub issues instead.

## 2026-10-05: completing PRs #2–#4 (stacked)

- **opencode can't run in parallel.** Concurrent `ask-opencode.sh` runs fail with `database is locked` or stall. Run cross-family reviews one after another. A stall (exit 75) means falling back to codex.
- **Don't `git add -A` on stacked branches.** An older branch's `.gitignore` may lack rules a later branch added, so untracked build output (`.coverage`, `__pycache__`) gets committed. Stage explicit paths.
- **Untracking a file deletes it from other checkouts.** Merging a `git rm --cached` commit makes `git pull` remove the file from any working tree whose HEAD still tracks it. After merging such a change, re-copy the file (the style guide's source is `D:\Dropbox\Claude\audio-lecture-style-guide.md`).
- **`codex review` reads the whole checkout,** `.env` included. Run it from a clean `git worktree` that has no secrets or private prompts. It also can't combine `--base` with custom instructions.
- **The pre-commit hook in `.git/hooks` applies to every branch.** Branches that predate `.pre-commit-config.yaml` need `PRE_COMMIT_ALLOW_NO_CONFIG=1`.
