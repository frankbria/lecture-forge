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
- `--range A-B` plans only those pages (or lines), e.g. one chapter of a full-book PDF. Ranges in the plan are always absolute positions in the source.
- **Validation:** the LLM's plan is untrusted input. Ranges must exist in the source, be in order and not overlap, and `est_minutes` must be at most 30. An invalid plan gets **one** repair attempt, with the problems listed for the model, and then the command fails.
- It writes `series/<slug>/plan.yaml`: the series title, author and source, then the episodes, each with `n`, `title`, `central_idea`, `pages` (or `lines`), `est_minutes`, `break_reason` and `listener_notes`. An existing plan is never overwritten without `--force`, because it may hold your edits.
- Provider failures follow the retry and switch policy (see LLM providers). `--auto` never prompts.
- **Approval gate:** the command stops here. You edit the YAML (reorder, merge, split, change ranges) before running `write`.

### 2. `write` — script pass, then critique pass
- `lecture-forge write <slug> [--episode N | --all]`
- **Pass 1:** the style guide Part 1 is the system prompt and the Part 3 template is the input. The template is filled from plan.yaml plus the *previous episode's* summary and puzzle answer.
- **Pass 2:** the style guide Part 2 critiques the draft against the source and returns a revised script.
- Results are saved to `series/<slug>/episodes/NN/`: `draft.md`, `critique.md`, `script.txt` (spoken text only), `notes.md` (production notes), `puzzle_answer.md`, `summary.md`.
- If the model proposes a split point (production notes say the source needs a split), it is reported to you. The plan is never re-split silently.
- Episodes have to be written in order, because episode N needs N−1's summary and puzzle. `--all` writes them one after another.

### 3. `render` — text to speech
- `lecture-forge render <slug> [--episode N | --all]`
- Uses ElevenLabs with model **`eleven_v3`** (the default; overridable with `ELEVENLABS_MODEL_ID`).
- `eleven_v3` accepts at most **5,000 characters per request**, so the script is split at paragraph boundaries into pieces of 5,000 characters or fewer. A 25-minute episode is about 25,000 characters, which is 5–6 requests. The limit is looked up per model, so switching models adjusts the piece size.
- `eleven_v3` does **not** support request stitching. Instead, every piece uses the same voice settings and the same fixed `seed`. That reduces variation between pieces but doesn't guarantee identical delivery. The joins fall on paragraph breaks, where a pause is natural anyway. The pieces are joined with ffmpeg.
- For models that **do** support stitching (`eleven_v4`, `eleven_multilingual_v2`), `render` automatically sends the previous pieces' `previous_request_ids`. If the `eleven_v3` joins are audible, setting `ELEVENLABS_MODEL_ID` to one of those models is therefore all it takes.
- ID3 tags: album = series title, track = episode number, title = episode title, artist = "lecture-forge".
- Output: `<output_dir>/<series>/NN - <title>.mp3`. Titles come from the LLM-written plan, so they're sanitized for filenames (no path separators, reserved characters or Windows-reserved names) before use. The default `output_dir` is `/mnt/d/Dropbox/Lectures` (`D:\Dropbox\Lectures`).
- Running it again is safe: an episode whose `script.txt` hash hasn't changed is skipped, so credits aren't spent twice.

### Review gate
- By default `write` stops after producing `script.txt`, so you can read it before spending ElevenLabs credits.
- `lecture-forge run <source> --series <slug> --auto` does plan, write and render with no stops. In `--auto` mode the plan isn't reviewed either.

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

## Stack

- Python 3.12, `uv`, argparse (CLI), PyYAML, pymupdf (slicing and text extraction), anthropic, openai, elevenlabs, mutagen (ID3 tags), ffmpeg (join).
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
