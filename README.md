# lecture-forge

Turn textbook chapters into Feynman-style, chalkboard-free audio lectures you can listen to while driving.

PDF / Markdown / text → episode plan → script (written, then critiqued) → ElevenLabs MP3 in your Dropbox.

**Status:** Milestones 1–4 done: config, source loading, PDF slicing, style-guide loader, the `outline`, `plan` and `write` commands, and swappable LLM providers (Claude Code on your subscription, Claude API, OpenAI) with retry and provider switching. `render` and `run` are still planned. See [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Usage

Available now:

```bash
uv sync
lecture-forge outline book.pdf                  # length + bookmarks/headings of a PDF/md/txt source
lecture-forge plan book.pdf --series topology   # proposes series/topology/plan.yaml; review and edit it
lecture-forge plan book.pdf --series ch3 --range 120-185   # plan one chapter of a full book
lecture-forge write topology --all              # script pass + critique per episode, in order
lecture-forge write topology --episode 3 --force   # rewrite one episode
```

The planner reads the content itself and breaks at clean concept boundaries, one central idea and at most 30 minutes per episode. Each episode records why it ends where it does.

`write` drafts each episode under your style guide, critiques and revises it against the source, and saves `series/<slug>/episodes/NN/script.txt` (spoken text only) plus the production notes, puzzle answer and summary that the next episode builds on. Read the scripts before rendering.

Planned (not implemented yet):

```bash
lecture-forge render topology --all             # MP3s to D:\Dropbox\Lectures\topology
lecture-forge run chapter3.pdf --series topo3 --auto   # everything, no stops
```

## Setup

1. `cp .env.example .env` and fill in `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`, plus an LLM key if you aren't using Claude Code.
2. Provide your style guide. Either copy it to `prompts/style-guide.md` (git-ignored) or set `STYLE_GUIDE_PATH` in `.env`. It must follow the Part 1 (script) / Part 2 (critique) / Part 3 (input template) structure.
