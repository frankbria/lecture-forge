# lecture-forge — Requirements (v0.1, CLI)

Turn a textbook (or one chapter of it) into a series of single-voice audio lectures you can listen to in the car, written to the rules in your audio-lecture style guide (kept outside the repo; see Configuration).

## Goals

- Input: a PDF (a full book or one chapter), a Markdown file, or a plain-text file.
- Output: one tagged MP3 per episode, about 20–30 minutes long, saved to a synced Dropbox folder.
- Every episode follows the style guide: it opens with a problem, works through one spine example, and ends with a puzzle, and the next episode answers that puzzle.
- CLI first. A GUI comes later and wraps the same core functions, so the core library must not depend on the CLI.

## Non-goals (v0.1)

- GUI, web app, podcast RSS feed, multiple voices or speakers, a database, translation.

## Pipeline

```
source ──plan──▶ plan.yaml ──(you edit)──▶ write ──▶ draft ──critique──▶ script.txt ──(optional review)──▶ render ──▶ MP3
```

### 1. `plan` — split the source into episodes
- `lecture-forge plan <source> --series <slug> [--range A-B] [--force] [--auto]`
- The LLM reads the **content itself** (the PDF pages, or numbered lines for md/txt) and proposes where the breaks go. Bookmarks and headings are passed as hints only, so a section holding several ideas is split and short sections forming one idea are merged. **Break rules:**
  1. **Content:** exactly one central idea per episode (style guide).
  2. **Timing:** each episode targets 20–30 minutes of audio. The planner gets a word count for every page, so it can tell dense pages from light ones, and estimates `est_minutes`. **An episode over 30 minutes is rejected**: split it instead.
  3. **Clean concept breaks:** never mid-proof, mid-example or mid-derivation. Each episode records a `break_reason`.
  - Front matter, exercises and back matter may be skipped (gaps between episodes are allowed). Episodes are in source order and don't overlap.
- **Scanned PDFs:** a scan is a page whose image covers at least half of it and whose text layer has at most 10 words (e.g. a stamped page number or scanner footer). Scans get no OCR. Every provider is sent the PDF pages and reads them as images: Claude reads PDF pages as images, and OpenAI's vision models get the page images alongside the text. In the planner's digest a scan is marked `scanned page image, words not counted` instead of a misleading word count near 0, and the planner is told to judge those pages from the page itself. `plan` and `outline` print a note with how many pages are scans. Blank pages, pages with a figure and some text, and scans with an OCR'd text layer are not scans (they count their words). A page drawn as vectors with no text still counts 0 words.
- `--range A-B` plans only those pages (or lines), e.g. one chapter of a full-book PDF. Ranges in the plan are always absolute positions in the source.
- **Validation:** the LLM's plan is untrusted input. Ranges must exist in the source, be in order and not overlap, and `est_minutes` must be at most 30. An invalid plan gets **one** repair attempt, with the problems listed for the model, and then the command fails.
- It writes `series/<slug>/plan.yaml`: the series title, author and source, then the episodes, each with `n`, `title`, `central_idea`, `pages` (or `lines`), `est_minutes`, `break_reason` and `listener_notes`. An existing plan is never overwritten without `--force`, because it may hold your edits.
- Provider failures follow the retry and switch policy (see LLM providers). `--auto` never prompts.
- **Approval gate:** the command stops here. You edit the YAML (reorder, merge, split, change ranges) before running `write`.

### 2. `write` — script pass, then critique pass
- `lecture-forge write <slug> (--episode N | --all) [--force] [--auto]`
- Reads `series/<slug>/plan.yaml` **with your edits** and validates it again: unique positive episode numbers, titles present, ranges inside the source, and the source still openable. Episodes run in order of `n`.
- **Pass 1 (script):** the style guide's Part 1 is the system prompt. The Part 3 template is filled **by its field labels** (SOURCE, SERIES/BOOK, EPISODE NUMBER, …SUMMARY, …PUZZLE…, LISTENER…), so edits to your guide keep working; unknown fields get "(not provided)". The plan's central idea and listener notes go into LISTENER NOTES, and the episode's pages (sliced PDF) or lines are the source. A runtime addendum (your file is never edited) asks for a fourth section, `EPISODE SUMMARY`, and for the puzzle to be restated in one line before its answer.
- **Pass 2 (critique):** the system prompt is Part 1 **plus** Part 2, since Part 2 reviews "under the style guide above". It gets the source pages again, so it can check facts. If the revision changes the closing puzzle, it adds a `PUZZLE ANSWER` section, so continuity always matches the final script.
- **Output is untrusted:** headings are recognized in any common style (`**SCRIPT**`, `## SCRIPT`, `6. **Revised script:**` …). A draft missing `SCRIPT`, `PUZZLE ANSWER` or `EPISODE SUMMARY`, or a revised script shorter than half the draft (truncated or summarized), gets **one** repair round, then the command fails.
- **Files** in `series/<slug>/episodes/NN/`: `draft.md` and `notes.md` (saved after pass 1, kept even if pass 2 fails), `critique.md`, `puzzle_answer.md`, `summary.md`, `written.json`, then `script.txt` **last**. It holds the spoken text only, and its existence marks the episode done. Every file is written atomically (a temp file, then a rename), and a rewrite removes the old `script.txt` first, so an interrupted run is either fully done or not done. It is never "done" with continuity files that don't match its script. `written.json` records the source range a script was written from; if the plan's range changes, `write` rewrites the episode and `render` refuses it until then.
- **Continuity:** episode N's prompt carries the previous episode's `summary.md` and `puzzle_answer.md`. N refuses to start until the previous episode is written for its current range. The first episode gets "(none: first episode)".
- `--all` writes every unwritten or stale episode in order and skips current ones. `--episode N` refuses to overwrite a current episode without `--force`. Rewriting an episode warns that later written episodes were built on its old summary and puzzle.
- Reported, never acted on silently: script length (words and ~minutes at 150 wpm, with a note over 30 minutes) and any split point the writer suggests in its production notes. The plan is never re-split automatically.
- Provider failures follow the retry and switch policy; a switch carries across the episodes of one run.

### 3. `render` — text to speech
- `lecture-forge render <slug> (--episode N | --all) [--force] [--yes]`
- ElevenLabs with model **`eleven_v3`** (the default; `ELEVENLABS_MODEL_ID` overrides it), your voice (`ELEVENLABS_VOICE_ID`), MP3 at 44.1 kHz / 128 kbps.
- **Cost control (it spends money):**
  - Before any call it prints the estimated credits per episode, the total, and the credit balance left on your plan (read from your ElevenLabs subscription, which calls credits "characters"). Credits per character differ by model (`eleven_v3` bills about 0.44), so the estimate uses the rate measured from your earlier renders of that model in the series, and shows characters until the first render.
  - The rate is measured, not looked up: the balance is read just before an episode's first paid piece and after its last, and `render.json` records the characters sent and the credits used. A balance that didn't drop (it can lag) records nothing. Later renders of the same episode and model add to its measurement, and a rebuild that sends nothing keeps it. The rate is approximate: other use of the account during a render, or a balance that has only partly caught up, skews it.
  - An estimate over the balance is refused (rate 1.0 when nothing is measured yet).
  - Otherwise it asks `Render? [y/N]`. `--yes` skips the question, and with no terminal it never spends without `--yes`.
  - Only pieces not already cached count toward the cost, and an episode that is already rendered and unchanged costs 0 (unless `--force`), even if its piece cache was deleted.
- **Splitting:** pieces of at most the model's per-request limit (`eleven_v3` 5,000; v4 and multilingual v2 10,000; flash 30–40,000; unknown models 5,000). Pieces are packed from whole paragraphs. A paragraph over the limit is split at sentences, then words.
- **Continuity across pieces:** every piece uses the same fixed `seed`, which reduces variation but doesn't guarantee identical delivery. Models that support stitching automatically get the **3** most recent `previous_request_ids`, never an empty one or one older than ElevenLabs' 2-hour window (a render resumed later simply skips it). `eleven_v3` rejects stitching, so it gets none. The joins fall on paragraph breaks.
- **Never pay twice:** each piece is cached in `episodes/NN/audio/` under a hash of (model, voice, seed, format, text), plus, for stitching models, the keys of the pieces before it, since a stitched piece is spoken in continuity with them. Pieces are stored only once fully on disk. A failed render resumes from the cached pieces. After an edit, only the pieces whose text changed are synthesized again (with stitching, also the pieces that follow them).
- **Skip when unchanged:** `render.json` records the script hash, model, voice and output path (plus the measured cost, which doesn't count toward "unchanged"). An unchanged episode is skipped. `--force` rebuilds the file from the cache without new spending. `render.json`, the cached request ids and `plan.yaml` are written atomically (a temp file, then a rename); an unreadable `render.json` (torn, hand-edited, not UTF-8) counts as missing, so the episode is rebuilt from cached pieces instead of failing.
- **Join and tag:** ffmpeg concatenates the pieces and writes ID3v2.3 tags (title, album = series title, artist and album artist = author, track `n/last`, genre Speech). The file is published atomically, so Dropbox never syncs a half-written file.
- **Output:** `<output_dir>/<series title>/NN - <episode title>.mp3` (default `output_dir` `/mnt/d/Dropbox/Lectures`, i.e. `D:\Dropbox\Lectures`).
  - Titles come from the LLM-written plan, so they're sanitized for Windows: no `<>:"/\|?*` or control characters, no trailing dots, reserved names (CON, COM1…) prefixed, at most 120 characters.
  - The output folder is created only if its parent exists. If the drive isn't mounted, the command fails rather than inventing a Linux-only folder. Folders are created only when an episode is actually rendered, never by the cost preview, so a declined render leaves nothing in Dropbox.
  - If an episode was renamed since it was last rendered, its previous MP3 is removed: only an `.mp3` for that episode number inside the output folder, so the playlist never has duplicates. It is never removed when it is the same file as the new MP3, e.g. after a capitalization-only rename on the case-insensitive Dropbox drive.
- Transient ElevenLabs errors (408, 409, 429, 5xx, network) are retried with backoff (10, 20, 40 s). Other errors fail with ElevenLabs' message.
- Tests: the real ElevenLabs integration test runs only with `LECTURE_FORGE_PAID_TESTS=1`, so routine test runs never spend credits.

### Review gate
- By default `write` stops after producing `script.txt`, so you can read it before spending ElevenLabs credits.
- `lecture-forge run <source> --series <slug> [--range A-B] [--auto] [--yes]` chains `plan`, `write --all` and `render --all`.
  - Without `--auto` it stops after making a new plan (the approval gate). Running the same command again continues from your edited plan; render's cost prompt is the script review gate.
  - `--auto` removes the review stops and the provider-switch prompts, so the plan isn't reviewed either. **Spending still needs consent:** `--yes`, or answering the cost prompt in a terminal. With no terminal and no `--yes`, it writes the scripts and then refuses to render. `--auto --yes` is fully unattended.
  - **Resumable:** an existing `plan.yaml` is reused (it must be for the same source, otherwise it fails and suggests another `--series`); written and rendered episodes are skipped.
  - The ElevenLabs key and voice are checked before the first LLM call, so a missing setting fails in seconds, not after the scripts are written.

## LLM providers (swappable)

All three providers implement one interface: `complete(provider, system, user, pdf=None) -> str` (`src/lecture_forge/providers.py`).

| Provider | Selector | Auth | Default model | How PDFs are passed |
|---|---|---|---|---|
| Claude Code headless (**default**) | `claude-code` | your subscription (`claude -p`) | your Claude Code default | The sliced PDF is copied into a temporary directory, which it reads with the Read tool (the only tool enabled; reads outside that directory are denied). |
| Claude API | `anthropic` | `ANTHROPIC_API_KEY` | `claude-opus-5-5`, effort `high` | Native `document` block. Server-side refusal fallback is on (`fallbacks: "default"`). |
| OpenAI | `openai` | `OPENAI_API_KEY` | `gpt-5.5` | Native `input_file` in the Responses API. |

- Every provider sees the actual PDF pages, so there is **no text extraction** on any LLM path.
- `LECTURE_FORGE_LLM_MODEL` overrides the model for whichever provider is in use.
- `claude -p` runs with `--setting-sources ""`, so your user hooks, plugins, output styles and `CLAUDE.md` don't leak into the scripts. `--bare` is **not** used, because it only accepts API-key auth and would ignore your subscription.
- **Failures:** transient errors (408, 409, 429, 5xx, network, timeout) are retried 3 times with exponential backoff (10 s, 20 s, 40 s). The SDKs' own retries are turned off, so there's one retry policy. Permanent errors (other 4xx, a refusal from any provider, a cut-off or empty response, a missing key or CLI) fail immediately. On final failure the error lists the other providers you can use. Interactively, the CLI asks whether to switch, and the provider you pick stays in use for the rest of the run. Switching drops any `LECTURE_FORGE_LLM_MODEL` override, because that names the old provider's model. In `--auto` mode, or with no alternatives, the run stops.
- Markdown and text sources always go in as plain text.
- **Subscription billing guard:** if Claude Code finds `ANTHROPIC_API_KEY` in its environment, it uses that key **instead of your subscription**. The `claude-code` provider therefore runs `claude -p` with `ANTHROPIC_API_KEY` (and `ANTHROPIC_AUTH_TOKEN`) removed from the environment it passes, and a test enforces this. Settings are read from `.env` with `dotenv_values`, which never exports them into `os.environ`; the app must never call `load_dotenv()`.
- The PDF is always sliced to the episode's page range first, which keeps you under provider page limits and keeps cost down.

## Configuration

- `.env` (git-ignored): `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, optional `ELEVENLABS_MODEL_ID` (default `eleven_v3`), optional `STYLE_GUIDE_PATH`, plus `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` as needed.
- Also in `.env`: `LECTURE_FORGE_PROVIDER` (default `claude-code`) and `LECTURE_FORGE_OUTPUT_DIR` (default `/mnt/d/Dropbox/Lectures`). There is no separate config file: `.env` holds everything, and real environment variables override it.
- **Style guide:** this is your own prompt file, and it is **not** tracked in the repo. It is loaded from `STYLE_GUIDE_PATH`, or from `prompts/style-guide.md` (git-ignored) if that isn't set, and split by its `## Part N` headings. If it's missing, the command exits with a message saying where to put it.

## State

- Plain files under `series/<slug>/` (git-ignored). No database: the whole state is one plan plus a few files per episode.
- Episode N+1 reads `summary.md` and `puzzle_answer.md` from episode N.

## Testing and spending policy

Credits and per-use API charges are spent **only when they have to be**. Each kind of run has a fixed budget:

| Run | ElevenLabs | Anthropic / OpenAI API | Claude Code |
|---|---|---|---|
| CI (every push and PR) | **0**: no secrets exist in CI | **0** | not installed |
| Pre-commit hook, `pytest`, `pytest -m integration` | **0** | **0** (fake-key 401 checks only, which are free) | your subscription |
| `LECTURE_FORGE_PAID_TESTS=1 pytest -m paid` | ~70 characters | a few cents | your subscription |
| `lecture-forge render` | the cost it shows you, **only after you confirm** | — | — |

- **Enforced, not just conventional:** `tests/conftest.py` blocks every test from building an ElevenLabs, Anthropic or OpenAI client with one of your real keys (or with no key, since the SDKs would then read the environment) unless the test is marked `paid` **and** `LECTURE_FORGE_PAID_TESTS=1` is set. `tests/test_spend_guard.py` checks the guard, and a mutation check confirms it fails when the guard is off.
- **When to run the paid tests:** only when the code that talks to a billed API changes, i.e. the ElevenLabs adapter in `render.py`, the `anthropic`/`openai` paths in `providers.py`, or an upgrade of those SDKs. Not for unrelated changes.
- **Demos and reviews** use cached audio (`render --force` rebuilds an MP3 from cached pieces at 0 characters) or the ~400-character two-piece render, and the latter only when splitting, joining or tagging changes. A full real episode is rendered only with your explicit go-ahead.
- **New tests that touch a billed API** must use a fake key or be marked `paid`. The guard turns a mistake into a failing test, not a charge.

## Stack

- Python 3.12, `uv`, argparse (CLI), PyYAML, pymupdf (slicing and text extraction), anthropic, openai, elevenlabs, ffmpeg (join and ID3 tags).
- Tests: pytest. Integration tests call the real providers and are marked so they can be skipped when no key is set.

## Decisions

1. **Episode summary:** the style guide's output format has no summary section, but the next episode needs one. The app adds an instruction for a 4th section, `EPISODE SUMMARY` (one paragraph, not spoken), to the Part 1 prompt at runtime. Your style guide file is never modified.

## Milestones

1. **M1:** scaffold, config, source loading and PDF slicing.
2. **M2:** provider interface plus the three providers.
3. **M3:** `plan`.
4. **M4:** `write` (script pass, critique pass, episode-to-episode state).
5. **M5:** `render` (ElevenLabs, chunking, ffmpeg, ID3, idempotency).
6. **M6:** `run --auto`; end-to-end test on a real chapter.
7. **Later:** GUI over the same core.
8. **Later:** custom pronunciation. Turn the pronunciation hints in each episode's production notes into an ElevenLabs pronunciation dictionary, applied at render time. *Deferred: tracked in [#1](https://github.com/frankbria/lecture-forge/issues/1).*
