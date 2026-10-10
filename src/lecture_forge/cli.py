import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path

from lecture_forge import render
from lecture_forge.config import ConfigError, Settings, load_settings
from lecture_forge.plan import (
    MAX_MINUTES,
    PlanError,
    load_plan,
    make_plan,
    reusable_plan,
    series_dir,
    write_plan,
)
from lecture_forge.providers import ProviderError, complete_switching
from lecture_forge.source import Source, open_source, outline, scanned_note
from lecture_forge.style_guide import StyleGuideError, load_style_guide
from lecture_forge.write import (
    WriteError,
    episodes_to_write,
    script_state,
    write_episode,
)

# The CLI's project is the folder it runs in; the engine takes the root explicitly.
ROOT = Path(".")


def llm(
    settings: Settings,
    system: str,
    user: str,
    pdf: Path | None = None,
    *,
    interactive: bool,
    ask: Callable[[str], str] = input,
) -> tuple[str, Settings]:
    """complete_switching() with the terminal as the chooser: when retries run out, show
    the error and ask which provider to switch to."""

    def choose(e: ProviderError) -> str:
        print(f"error: {e}", file=sys.stderr)
        return ask(f"Switch provider? [{'/'.join(e.alternatives)}/n]: ").strip()

    return complete_switching(
        settings,
        system,
        user,
        pdf,
        choose=choose if interactive else None,
        on_note=print,
    )


def cmd_outline(args: argparse.Namespace) -> None:
    src = open_source(args.source)
    unit = "pages" if src.kind == "pdf" else "lines"
    print(f"{src.path.name}: {src.length} {unit}")
    if note := scanned_note(src):
        print(note)
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
    return series_dir(ROOT, slug)


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
    settings = load_settings(root=ROOT)
    guide = load_style_guide(settings.style_guide_path)
    src = open_source(args.source)
    start, end = _range(args.range, src)
    call = _provider_call(settings, args.auto)

    unit = "pages" if src.kind == "pdf" else "lines"
    print(f"Planning {src.path.name} {unit} {start}-{end} with {settings.provider}...")
    if note := scanned_note(src, start, end):
        print(note)
    plan = make_plan(src, guide, start, end, call)
    write_plan(plan, src, out, force=args.force)
    episodes = plan["episodes"]
    for n, e in enumerate(episodes, 1):
        print(
            f"{n}. {e['title']}  {unit} {e['start']}-{e['end']}  ~{e['est_minutes']} min"
        )
    total = sum(e["est_minutes"] for e in episodes)
    n = len(episodes)
    print(f"{n} episode{'s' * (n != 1)}, ~{total} min total -> {out}")
    print("Review and edit the plan before writing scripts.")


def cmd_write(args: argparse.Namespace) -> None:
    series_dir = _check_series(args.series)
    plan_path = series_dir / "plan.yaml"
    if not plan_path.is_file():
        raise FileNotFoundError(
            f"{plan_path} not found; run `lecture-forge plan` first"
        )
    plan = load_plan(plan_path, root=ROOT)

    def state(ep: dict) -> str:
        return script_state(series_dir, plan, ep)

    todo = episodes_to_write(plan, series_dir, episode=args.episode, force=args.force)
    settings = load_settings(root=ROOT)
    guide = load_style_guide(settings.style_guide_path)
    call = _provider_call(settings, args.auto)
    unit = plan["unit"]
    for ep, action, why in todo:
        rewriting = action == "rewrite"
        if action == "skip":
            print(f"Episode {ep['n']}: {ep['title']}: {why}, skipping")
            continue
        if why:  # stale: the plan changed since it was written
            print(f"Episode {ep['n']}: {ep['title']}: plan changed ({why}), rewriting")
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
        later = [
            str(e["n"])
            for e in plan["episodes"]
            if e["n"] > ep["n"] and state(e) != "missing"
        ]
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
    plan = load_plan(plan_path, root=ROOT)
    settings = load_settings(root=ROOT)
    settings.require("elevenlabs_api_key")
    settings.require("voice_id")
    p = render.preflight(
        plan, series_dir, settings, episode=args.episode, force=args.force
    )
    for ep, chars, why in p.episodes:
        if chars is None:
            print(f"Episode {ep['n']}: {ep['title']}: {why}, skipping")
            continue
        cost = (
            f"~{round(chars * p.rate):,} credits ({chars:,} characters)"
            if p.rate
            else f"{chars:,} characters"
        )
        print(f"Episode {ep['n']}: {ep['title']}: {cost} to synthesize")
    balance = (
        f"; {p.left:,} credits left on your ElevenLabs plan"
        if p.left is not None
        else ""
    )
    if p.rate:
        cost = f"~{p.estimate:,} credits ({p.total:,} characters at {p.rate:.2f}/char, measured)"
    else:
        cost = f"{p.total:,} characters (about as many credits; no render measured yet for {settings.model_id})"
    print(f"Total: {cost}{balance}")
    p.check()  # over the balance, or no ffmpeg to finish with
    if p.total and not args.yes:  # spending money always needs a yes
        if not sys.stdin.isatty():
            raise render.RenderError(
                "rendering spends ElevenLabs credits; pass --yes to confirm"
            )
        if input("Render? [y/N] ").strip().lower() not in ("y", "yes"):
            raise render.RenderError("cancelled; nothing was spent")
    tts = render.elevenlabs_tts(settings)
    for ep in p.ready:
        print(f"Episode {ep['n']}: {ep['title']}: rendering...", flush=True)
        res = render.render_episode(
            plan, ep, series_dir, settings, tts, force=args.force,
            balance=lambda: render.characters_left(settings),
        )  # fmt: skip
        if res.skipped:
            print(f"  already rendered: {res.path}")
        else:
            credits = f", {res.credits:,} credits" if res.credits else ""
            print(
                f"  -> {res.path}  ({res.billed:,} characters sent, {res.reused} pieces reused{credits})"
            )


def cmd_run(args: argparse.Namespace) -> None:
    """plan, write --all, render --all. Every step skips finished work, so a failed or
    interrupted run picks up where it stopped when run again."""
    plan_path = _check_series(args.series) / "plan.yaml"
    settings = load_settings(root=ROOT)
    settings.require("elevenlabs_api_key")  # fail now, not after an hour of writing
    settings.require("voice_id")
    if reusable_plan(plan_path, args.source, root=ROOT) is not None:
        print(f"Using the existing {plan_path} (delete it to plan again)")
    else:
        cmd_plan(argparse.Namespace(
            source=args.source, series=args.series, range=args.range, force=False,
            auto=args.auto,
        ))  # fmt: skip
        if not args.auto:  # the approval gate: review the plan, then run again
            print("Run the same command again to write and render, or pass --auto.")
            return
    cmd_write(argparse.Namespace(
        series=args.series, episode=None, all=True, force=False, auto=args.auto
    ))  # fmt: skip
    cmd_render(argparse.Namespace(
        series=args.series, episode=None, all=True, force=False, yes=args.yes
    ))  # fmt: skip


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
    p = sub.add_parser(
        "run", help="plan, write and render in one go; resumes where it stopped"
    )
    p.add_argument("source")
    p.add_argument(
        "--series", required=True, help="name for this series, e.g. topology"
    )
    p.add_argument("--range", help="only plan these pages/lines, e.g. 120-185")
    p.add_argument(
        "--auto",
        action="store_true",
        help="no review stops and no prompts (spending still needs --yes)",
    )
    p.add_argument(
        "--yes", action="store_true", help="don't ask before spending credits"
    )
    p.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (
        OSError, ValueError,  # OSError: also a Dropbox-locked file, a full disk
        ConfigError, StyleGuideError, ProviderError, PlanError, WriteError, render.RenderError,
    ) as e:  # fmt: skip
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
