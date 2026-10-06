import argparse
import dataclasses
import re
import sys
from collections.abc import Callable
from pathlib import Path

from lecture_forge.config import ConfigError, Settings, load_settings
from lecture_forge.plan import PlanError, make_plan, write_plan
from lecture_forge.providers import ProviderError, complete
from lecture_forge.source import Source, open_source, outline
from lecture_forge.style_guide import StyleGuideError, load_style_guide

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


def cmd_plan(args: argparse.Namespace) -> None:
    if not SLUG.fullmatch(args.series):
        raise ValueError(
            f"--series must be lowercase letters, digits, - or _, got {args.series!r}"
        )
    out = SERIES_DIR / args.series / "plan.yaml"
    if out.exists() and not args.force:  # check before spending an LLM call
        raise FileExistsError(
            f"{out} exists and may hold your edits; pass --force to replace it"
        )
    settings = load_settings()
    guide = load_style_guide(settings.style_guide_path)
    src = open_source(args.source)
    start, end = _range(args.range, src)
    interactive = not args.auto and sys.stdin.isatty()
    current = {"settings": settings}

    def call(system: str, user: str, pdf: Path | None) -> str:
        text, current["settings"] = llm(
            current["settings"], system, user, pdf, interactive=interactive
        )
        return text

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
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (
        FileNotFoundError, FileExistsError, ValueError,
        ConfigError, StyleGuideError, ProviderError, PlanError,
    ) as e:  # fmt: skip
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
