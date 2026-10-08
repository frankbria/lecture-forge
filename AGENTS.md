# lecture-forge: agent instructions

Turns textbook PDFs/Markdown into single-voice audio lectures: `plan` → `write` → `render` (ElevenLabs) → MP3s in Dropbox; `run` chains them. Spec: `docs/REQUIREMENTS.md`.

## Verify your change

```bash
uv run pytest -m "not integration" --cov --cov-fail-under=85
uv run ruff check .
uv run ruff format --check .
```

## Money and safety (hard rules)

- Never run `lecture-forge render` or `lecture-forge run`, and never set `LECTURE_FORGE_PAID_TESTS`, without the owner's explicit go-ahead: they spend ElevenLabs credits / API money.
- Never run pytest without `-m "not integration"` unless asked: integration tests call Claude Code on the owner's subscription.
- Never read or print `.env` (real keys). The app reads it with `dotenv_values`; never call `load_dotenv()`.
- Never modify `series/` (the owner's plans, scripts and paid audio cache) or the output folder (`LECTURE_FORGE_OUTPUT_DIR`, default `/mnt/d/Dropbox/Lectures`).
- Make code changes in a separate git worktree (e.g. `git worktree add ../lecture-forge-wt-<n> -b <branch> main`), never in the main checkout: the owner's `lecture-forge` command is an editable install that runs the main checkout's code, and may be rendering (spending credits) while you work.
- Never commit PDFs or other source books: this repo is public.
- New tests that touch a billed API use a fake key or the `paid` marker; `tests/conftest.py` blocks real keys otherwise.

## Layout

- `cli.py`: argparse commands (`outline`, `plan`, `write`, `render`, `run`)
- `config.py`: settings from `.env`, overridden by the environment
- `source.py`: PDF/text loading, outline, PDF slicing
- `style_guide.py`: splits the owner's style guide into its parts
- `providers.py`: LLM providers (claude-code, anthropic, openai) and the retry policy
- `plan.py`: episode planning and `plan.yaml` validation
- `write.py`: script and critique passes, episode continuity
- `render.py`: ElevenLabs TTS, piece cache, ffmpeg join and tags

## Conventions

- Minimal code; no speculative abstractions.
- Tests use real files and real ffmpeg with small fakes (`FakeTTS` in `tests/test_render.py`), never mocked SDK clients.
- `# fmt: skip` keeps compact multi-argument calls on one line.
- Errors are domain exceptions (`PlanError`, `WriteError`, `RenderError`, `ProviderError`, `ConfigError`) that `cli.main` prints as `error: …`.

## Read before working

- `tasks/lessons.md`: process lessons from earlier work.
- `docs/code-review/`: accepted trade-offs; don't re-raise them.
