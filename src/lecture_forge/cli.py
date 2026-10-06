import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from lecture_forge.config import Settings
from lecture_forge.providers import ProviderError, complete
from lecture_forge.source import open_source, outline


def llm(
    settings: Settings,
    provider: str,
    system: str,
    user: str,
    pdf: Path | None = None,
    *,
    interactive: bool,
    ask: Callable[[str], str] = input,
) -> tuple[str, str]:
    """complete() with retries; once those run out, offer to switch providers.

    Returns (text, provider used), so the rest of the run can stay on the switched provider.
    """
    while True:
        try:
            return complete(provider, system, user, pdf, settings=settings), provider
        except ProviderError as e:
            if not (interactive and e.alternatives):
                raise
            print(f"error: {e}", file=sys.stderr)
            choice = ask(f"Switch provider? [{'/'.join(e.alternatives)}/n]: ").strip()
            if choice not in e.alternatives:
                raise
            provider = choice


def cmd_outline(args: argparse.Namespace) -> None:
    src = open_source(args.source)
    unit = "pages" if src.kind == "pdf" else "lines"
    print(f"{src.path.name}: {src.length} {unit}")
    entries = outline(src)
    if not entries:
        print("(no bookmarks or headings found)")
    for level, title, loc in entries:
        print(f"{'  ' * (level - 1)}{title}  [{loc}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lecture-forge")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("outline", help="show a source's length and bookmarks/headings")
    p.add_argument("source")
    p.set_defaults(func=cmd_outline)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
