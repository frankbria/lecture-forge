# lecture-forge — Requirements (v0.1, CLI)

Turn a textbook (or one chapter of it) into a series of single-voice audio lectures you can listen to in the car, written to the rules in [`prompts/style-guide.md`](../prompts/style-guide.md).

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
- `lecture-forge plan <source> --series <slug>`
- The LLM reads the TOC, PDF bookmarks or headings and proposes episodes. Each episode has exactly one central idea (style guide: "one central idea per episode").
- It writes `series/<slug>/plan.yaml`: series title and author, then a list of episodes with `n`, `title`, `central_idea`, `source_range` (PDF pages, or a heading range for md/txt) and `listener_notes`.
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
- Uses ElevenLabs. The script is split at paragraph boundaries into pieces under the model's per-request character limit. Each request passes `previous_text`/`next_text` so the voice stays consistent across joins. The pieces are joined with ffmpeg.
- ID3 tags: album = series title, track = episode number, title = episode title, artist = "lecture-forge".
- Output: `<output_dir>/<series>/NN - <title>.mp3`. The default `output_dir` is `/mnt/d/Dropbox/Lectures` (`D:\Dropbox\Lectures`).
- Running it again is safe: an episode whose `script.txt` hash hasn't changed is skipped, so credits aren't spent twice.

### Review gate
- By default `write` stops after producing `script.txt`, so you can read it before spending ElevenLabs credits.
- `lecture-forge run <source> --series <slug> --auto` does plan, write and render with no stops. In `--auto` mode the plan isn't reviewed either.

## LLM providers (swappable)

All three providers implement one interface: `complete(system, user, pdf: (path, pages) | None) -> str`.

| Provider | Selector | Auth | How PDFs are passed |
|---|---|---|---|
| Claude Code headless (**default**) | `claude-code` | your subscription (`claude -p`) | The prompt points it at a temporary PDF cut to the episode's pages, which it reads with its Read tool. The model sees the pages, so equations survive. |
| Claude API | `anthropic` | `ANTHROPIC_API_KEY` | The sliced PDF is sent as a native `document` block. |
| OpenAI | `openai` | `OPENAI_API_KEY` | Text extracted with pymupdf (equations may come out degraded; a warning is logged). |

- Selected with `--provider` or in `config.toml`. Models are configurable for each provider.
- Markdown and text sources always go in as plain text.
- The PDF is always sliced to the episode's page range first, which keeps you under provider page limits and keeps cost down.

## Configuration

- `.env`: `ELEVENLABS_API_KEY`, plus `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` as needed.
- `config.toml`: provider, models, ElevenLabs `voice_id`, `model_id`, voice settings, `output_dir`, path to the style guide.
- The style guide is a prompt file on disk (`prompts/style-guide.md`) and is split by its `## Part N` headings. You can edit it without touching code.

## State

- Plain files under `series/<slug>/` (git-ignored). No database: the whole state is one plan plus a few files per episode.
- Episode N+1 reads `summary.md` and `puzzle_answer.md` from episode N.

## Stack

- Python 3.12, `uv`, Typer (CLI), PyYAML, pymupdf (slicing and text extraction), anthropic, openai, elevenlabs, mutagen (ID3 tags), ffmpeg (join).
- Tests: pytest. Integration tests call the real providers and are marked so they can be skipped when no key is set.

## Open questions

1. **Episode summary:** the style guide's output format has no summary section, but the next episode needs one. Plan: append a 4th section, `EPISODE SUMMARY`, to the Part 1 output format in our copy of the prompt.
2. **Pronunciation:** the production notes list pronunciation hints. Should v0.2 turn them into an ElevenLabs pronunciation dictionary automatically?
3. **ElevenLabs voice and model:** which voice? `eleven_multilingual_v2` (stable, takes previous/next text) or `eleven_v3` (more expressive)?

## Milestones

1. **M1:** scaffold, config, source loading and PDF slicing.
2. **M2:** provider interface plus the three providers.
3. **M3:** `plan`.
4. **M4:** `write` (script pass, critique pass, episode-to-episode state).
5. **M5:** `render` (ElevenLabs, chunking, ffmpeg, ID3, idempotency).
6. **M6:** `run --auto`; end-to-end test on a real chapter.
7. **Later:** GUI over the same core.
