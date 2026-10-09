"""Load source material. Locations are 1-based and inclusive: pages for PDFs, lines for text."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
HEADING = re.compile(
    r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$"
)  # closing #s need a space before them
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
SETEXT = re.compile(r"^\s*(=+|-+)\s*$")  # underlines the text line above it
RULE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")  # ---, ***, _ _ _: a thematic break


@dataclass(frozen=True)
class Source:
    path: Path
    kind: Literal["pdf", "text"]
    length: int  # pages for PDFs, lines for text


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def open_source(path: str | Path) -> Source:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            doc = pymupdf.open(path)
        except pymupdf.FileDataError as e:
            raise ValueError(f"Cannot open {path.name} as a PDF: {e}") from e
        with doc:
            if doc.needs_pass:
                raise ValueError(
                    f"{path.name} is password-protected; remove the password first"
                )
            return Source(path, "pdf", doc.page_count)
    if suffix in TEXT_SUFFIXES:
        return Source(path, "text", len(_read(path).splitlines()))
    raise ValueError(f"Unsupported source type {suffix!r}: use .pdf, .md or .txt")


def outline(src: Source) -> list[tuple[int, str, int]]:
    """(level, title, location) for each PDF bookmark or Markdown heading."""
    if src.kind == "pdf":
        with pymupdf.open(src.path) as doc:
            return [
                (lvl, title, page) for lvl, title, page in doc.get_toc() if page >= 1
            ]
    lines = _read(src.path).splitlines()
    start = 0
    # YAML front matter: its closing --- underlines nothing
    if lines and lines[0].strip() == "---":
        ends = (i for i in range(1, len(lines)) if lines[i].strip() in ("---", "..."))
        start = next(ends, -1) + 1
    # text: (line, title) of the line an underline below it would make a heading
    entries, fence, text = [], "", None
    for n, line in enumerate(lines[start:], start + 1):
        s = line.strip()
        # A fence closes only on the same character, at least as long, nothing after.
        if fence:
            fence = "" if s.startswith(fence) and set(s) == {fence[0]} else fence
            continue
        if m := FENCE.match(line):
            fence, text = m[1], None
        elif m := HEADING.match(line):
            entries.append((len(m[1]), m[2], n))
            text = None
        elif text and (u := SETEXT.match(line)):
            entries.append((1 if u[1][0] == "=" else 2, text[1], text[0]))
            text = None
        else:  # blank lines and rules are no heading text
            text = (
                (n, s) if s and not (RULE.match(line) or SETEXT.match(line)) else None
            )
    return entries


def _check_range(src: Source, start: int, end: int) -> None:
    if not 1 <= start <= end <= src.length:
        raise ValueError(f"Bad range {start}-{end}: {src.path.name} has 1-{src.length}")


def extract_text(src: Source, start: int = 1, end: int | None = None) -> str:
    end = src.length if end is None else end
    _check_range(src, start, end)
    if src.kind == "text":
        lines = _read(src.path).splitlines(keepends=True)
        return "".join(lines[start - 1 : end])
    # ponytail: text layer only; scanned PDFs come back empty, add OCR if one shows up
    with pymupdf.open(src.path) as doc:
        return "\n".join(doc[i].get_text() for i in range(start - 1, end))


def slice_pdf(src: Source, start: int, end: int, out: str | Path) -> Path:
    """Write pages start..end to a new PDF, so providers only see the episode's pages."""
    if src.kind != "pdf":
        raise ValueError("Only PDF sources can be sliced")
    _check_range(src, start, end)
    out = Path(out)
    with pymupdf.open(src.path) as doc, pymupdf.open() as part:
        part.insert_pdf(doc, from_page=start - 1, to_page=end - 1)
        part.save(out)
    return out
