# lecture-forge

Turn textbook chapters into Feynman-style, chalkboard-free audio lectures you can listen to while driving.

PDF / Markdown / text → episode plan → script (written, then critiqued) → ElevenLabs MP3 in your Dropbox.

**Status:** Milestones 1–6 done: config, source loading, PDF slicing, style-guide loader, the `outline`, `plan`, `write`, `render` and `run` commands, and swappable LLM providers (Claude Code on your subscription, Claude API, OpenAI) with retry and provider switching. The M6 end-to-end run on a real chapter is still to do: it needs your go-ahead to spend credits. See [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Usage

Available now:

```bash
uv sync
lecture-forge outline book.pdf                  # length + bookmarks/headings of a PDF/md/txt source
lecture-forge plan book.pdf --series topology   # proposes series/topology/plan.yaml; review and edit it
lecture-forge plan book.pdf --series ch3 --range 120-185   # plan one chapter of a full book
lecture-forge write topology --all              # script pass + critique per episode, in order
lecture-forge write topology --episode 3 --force   # rewrite one episode
lecture-forge render topology --all             # MP3s to D:\Dropbox\Lectures\<series title> (asks first)
lecture-forge run chapter3.pdf --series topo3   # plan, stop for review; run it again to write and render
lecture-forge run chapter3.pdf --series topo3 --auto --yes   # everything, no stops, spending included
```

The planner reads the content itself and breaks at clean concept boundaries, one central idea and at most 30 minutes per episode. Each episode records why it ends where it does.

`write` drafts each episode under your style guide, critiques and revises it against the source, and saves `series/<slug>/episodes/NN/script.txt` (spoken text only) plus the production notes, puzzle answer and summary that the next episode builds on. Read the scripts before rendering.

`render` shows the characters it will spend and what's left on your ElevenLabs plan, and asks before spending (`--yes` to skip). It splits each script into pieces, caches every piece so nothing is paid for twice, joins them with ffmpeg and writes tagged MP3s (album = series, track = episode) into your Dropbox. Requires `ffmpeg`.

`run` chains the three steps. Each step skips finished work, so after a failure or Ctrl-C the same command picks up where it stopped. Without `--auto` it stops after a new plan so you can edit it. With `--auto` there are no review stops, but spending ElevenLabs credits still needs `--yes` (otherwise it writes the scripts and asks, or refuses with no terminal).

## Tests and spending

```bash
uv run pytest                      # unit + free integration tests: never spends ElevenLabs or API credits
LECTURE_FORGE_PAID_TESTS=1 uv run pytest -m paid   # ~70 ElevenLabs characters + a few cents of API usage
```

Run the paid tests only when the ElevenLabs or API client code changes. A guard in `tests/conftest.py` blocks any other test from using your real keys. CI has no keys at all. See "Testing and spending policy" in [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Setup

1. `cp .env.example .env` and fill in `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`, plus an LLM key if you aren't using Claude Code.
2. Provide your style guide. Either copy it to `prompts/style-guide.md` (git-ignored) or set `STYLE_GUIDE_PATH` in `.env`. It must follow the Part 1 (script) / Part 2 (critique) / Part 3 (input template) structure.
