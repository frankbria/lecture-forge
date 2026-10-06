import argparse
import sys

from lecture_forge.source import open_source, outline


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
