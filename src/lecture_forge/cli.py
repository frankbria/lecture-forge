import argparse
import dataclasses
import re
import sys
from collections.abc import Callable
from pathlib import Path

from lecture_forge import render
from lecture_forge.config import ConfigError, Settings, load_settings
from lecture_forge.plan import MAX_MINUTES, PlanError, load_plan, make_plan, write_plan
from lecture_forge.providers import ProviderError, complete
from lecture_forge.source import Source, open_source, outline
from lecture_forge.style_guide import StyleGuideError, load_style_guide
from lecture_forge.write import WriteError, episode_dir, write_episode

SERIES_DIR = Path("series")
SLUG = re.compile(
    r"[a-z0-9][a-z0-9_-]*"
)  # used as a directory name: no paths, no surprises


def llm(
    settings: Settings,
    system: str,
    user: str,
    pdf: Path | None = None,
    *,
    interactive: bool,
    ask: Callable[[str], str] = input,
) -> tuple[str, Settings]:
    """complete() on settings.provider; once retries run out, offer to switch providers.

    Returns (text, settings to use from now on). After a switch those settings name the
    new provider and drop the old provider's model override, so later calls stay on it.
    """
    while True:
        try:
            return complete(
                settings.provider, system, user, pdf, settings=settings
            ), settings
        except ProviderError as e:
            if not (interactive and e.alternatives):
                raise
            print(f"error: {e}", file=sys.stderr)
            choice = ask(f"Switch provider? [{'/'.join(e.alternatives)}/n]: ").strip()
            if choice not in e.alternatives:
                raise
            if settings.llm_model:  # a model override names the old provider's model
                print(
                    f"note: ignoring LECTURE_FORGE_LLM_MODEL={settings.llm_model} for {choice}"
                )
            settings = dataclasses.replace(settings, provider=choice, llm_model="")


def cmd_outline(args: argparse.Namespace) -> None:
    src = open_source(args.source)
    unit = "pages" if src.kind == "pdf" else "lines"
    print(f"{src.path.name}: {src.length} {unit}")
    entries = outline(src)
    if not entries:
        print("(no bookmarks or headings found)")
    for level, title, loc in entries:
        print(f"{'  ' * (level - 1)}{title}  [{loc}]")


def _range(text: str | None, src: Source) -> tuple[int, int]:
    if text is None:
        return 1, src.length
    if not (m := re.fullmatch(r"(\d+)-(\d+)", text)):
        raise ValueError(f"--range must look like 12-30, got {text!r}")
    start, end = int(m[1]), int(m[2])
    if not 1 <= start <= end <= src.length:
        raise ValueError(f"Bad range {start}-{end}: {src.path.name} has 1-{src.length}")
    return start, end


def _check_series(slug: str) -> Path:
    if not SLUG.fullmatch(slug):
        raise ValueError(
            f"--series must be lowercase letters, digits, - or _, got {slug!r}"
        )
    return SERIES_DIR / slug


def _provider_call(settings: Settings, auto: bool):
    """A (system, user, pdf) -> text call that keeps a provider switch for later calls."""
    interactive = not auto and sys.stdin.isatty()
    current = {"settings": settings}

    def call(system: str, user: str, pdf: Path | None) -> str:
        text, current["settings"] = llm(
            current["settings"], system, user, pdf, interactive=interactive
        )
        return text

    return call


def cmd_plan(args: argparse.Namespace) -> None:
    out = _check_series(args.series) / "plan.yaml"
    if out.exists() and not args.force:  # check before spending an LLM call
        raise FileExistsError(
            f"{out} exists and may hold your edits; pass --force to replace it"
        )
    settings = load_settings()
    guide = load_style_guide(settings.style_guide_path)
    src = open_source(args.source)
    start, end = _range(args.range, src)
    call = _provider_call(settings, args.auto)

    unit = "pages" if src.kind == "pdf" else "lines"
    print(f"Planning {src.path.name} {unit} {start}-{end} with {settings.provider}...")
    plan = make_plan(src, guide, start, end, call)
    write_plan(plan, src, out, force=args.force)
    episodes = plan["episodes"]
    for n, e in enumerate(episodes, 1):
        print(
            f"{n}. {e['title']}  {unit} {e['start']}-{e['end']}  ~{e['est_minutes']} min"
        )
    total = sum(e["est_minutes"] for e in episodes)
    print(f"{len(episodes)} episodes, ~{total} min total -> {out}")
    print("Review and edit the plan before writing scripts.")


def cmd_write(args: argparse.Namespace) -> None:
    series_dir = _check_series(args.series)
    plan_path = series_dir / "plan.yaml"
    if not plan_path.is_file():
        raise FileNotFoundError(
            f"{plan_path} not found; run `lecture-forge plan` first"
        )
    plan = load_plan(plan_path)

    def done(ep: dict) -> bool:
        return (episode_dir(series_dir, ep["n"]) / "script.txt").exists()

    if args.episode is not None:
        todo = [e for e in plan["episodes"] if e["n"] == args.episode]
        if not todo:
            raise ValueError(f"the plan has no episode {args.episode}")
        if done(todo[0]) and not args.force:
            raise FileExistsError(
                f"episode {args.episode} is already written; pass --force to rewrite it"
            )
    else:
        todo = plan["episodes"]
    settings = load_settings()
    guide = load_style_guide(settings.style_guide_path)
    call = _provider_call(settings, args.auto)
    unit = plan["unit"]
    for ep in todo:
        rewriting = done(ep)
        if rewriting and not args.force:
            print(f"Episode {ep['n']}: {ep['title']}: already written, skipping")
            continue
        where = f"{unit} {ep['start']}-{ep['end']}"
        print(
            f"Episode {ep['n']}: {ep['title']} ({where}): drafting, then critiquing...",
            flush=True,
        )
        w = write_episode(plan, ep, guide, call, series_dir)
        print(f"  -> {w.dir / 'script.txt'}  {w.words} words, ~{w.minutes:.0f} min")
        if w.minutes > MAX_MINUTES:
            print(
                f"  note: over {MAX_MINUTES} minutes; consider splitting it in plan.yaml"
            )
        if w.split:
            print(
                f"  note: the writer suggests a split: {w.split} (plan.yaml is unchanged)"
            )
        later = [str(e["n"]) for e in plan["episodes"] if e["n"] > ep["n"] and done(e)]
        if rewriting and args.episode is not None and later:
            print(
                f"  note: episode {', '.join(later)} was written from the old summary and "
                "puzzle; rewrite it with --force if the recap matters"
            )
    print("Review the script.txt files before rendering audio.")


def cmd_render(args: argparse.Namespace) -> None:
    series_dir = _check_series(args.series)
    plan_path = series_dir / "plan.yaml"
    if not plan_path.is_file():
        raise FileNotFoundError(
            f"{plan_path} not found; run `lecture-forge plan` first"
        )
    plan = load_plan(plan_path)
    settings = load_settings()
    settings.require("elevenlabs_api_key")
    settings.require("voice_id")
    if args.episode is not None:
        todo = [e for e in plan["episodes"] if e["n"] == args.episode]
        if not todo:
            raise ValueError(f"the plan has no episode {args.episode}")
    else:
        todo = plan["episodes"]
    ready, total = [], 0
    for ep in todo:
        if not (episode_dir(series_dir, ep["n"]) / "script.txt").exists():
            if args.episode is not None:
                raise render.RenderError(
                    f"write episode {ep['n']} first (lecture-forge write)"
                )
            print(f"Episode {ep['n']}: {ep['title']}: not written yet, skipping")
            continue
        chars = render.cost(plan, ep, series_dir, settings)
        print(f"Episode {ep['n']}: {ep['title']}: {chars:,} characters to synthesize")
        ready.append(ep)
        total += chars
    left = render.characters_left(settings)
    balance = f"; {left:,} left on your ElevenLabs plan" if left is not None else ""
    print(f"Total: {total:,} characters ({settings.model_id}){balance}")
    if left is not None and total > left:
        raise render.RenderError(
            f"this needs {total:,} characters but your plan has only {left:,} characters left"
        )
    if total and not args.yes:  # spending money always needs a yes
        if not sys.stdin.isatty():
            raise render.RenderError(
                "rendering spends ElevenLabs credits; pass --yes to confirm"
            )
        if input("Render? [y/N] ").strip().lower() not in ("y", "yes"):
            raise render.RenderError("cancelled; nothing was spent")
    tts = render.elevenlabs_tts(settings)
    for ep in ready:
        print(f"Episode {ep['n']}: {ep['title']}: rendering...", flush=True)
        res = render.render_episode(
            plan, ep, series_dir, settings, tts, force=args.force
        )
        if res.skipped:
            print(f"  already rendered: {res.path}")
        else:
            print(
                f"  -> {res.path}  ({res.billed:,} characters billed, {res.reused} pieces reused)"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lecture-forge")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("outline", help="show a source's length and bookmarks/headings")
    p.add_argument("source")
    p.set_defaults(func=cmd_outline)
    p = sub.add_parser("plan", help="propose an episode plan (series/<slug>/plan.yaml)")
    p.add_argument("source")
    p.add_argument(
        "--series", required=True, help="name for this series, e.g. topology"
    )
    p.add_argument("--range", help="only plan these pages/lines, e.g. 120-185")
    p.add_argument("--force", action="store_true", help="replace an existing plan.yaml")
    p.add_argument(
        "--auto", action="store_true", help="never prompt (no provider switching)"
    )
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser(
        "write", help="write episode scripts (script pass, then critique)"
    )
    p.add_argument("series")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--episode", type=int, help="write one episode")
    which.add_argument(
        "--all", action="store_true", help="write every unwritten episode, in order"
    )
    p.add_argument(
        "--force", action="store_true", help="rewrite episodes that already exist"
    )
    p.add_argument(
        "--auto", action="store_true", help="never prompt (no provider switching)"
    )
    p.set_defaults(func=cmd_write)
    p = sub.add_parser("render", help="render written scripts to MP3 with ElevenLabs")
    p.add_argument("series")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--episode", type=int, help="render one episode")
    which.add_argument(
        "--all", action="store_true", help="render every written episode"
    )
    p.add_argument(
        "--force", action="store_true", help="re-join and re-tag even if unchanged"
    )
    p.add_argument(
        "--yes", action="store_true", help="don't ask before spending credits"
    )
    p.set_defaults(func=cmd_render)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (
        FileNotFoundError, FileExistsError, ValueError,
        ConfigError, StyleGuideError, ProviderError, PlanError, WriteError, render.RenderError,
    ) as e:  # fmt: skip
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
