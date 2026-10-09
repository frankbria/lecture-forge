"""Plan a series: the LLM proposes where the episode breaks go; we validate and write plan.yaml."""

import json
import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

import yaml

from lecture_forge.source import (
    Source,
    extract_text,
    open_source,
    outline,
    scanned_pages,
    slice_pdf,
)
from lecture_forge.style_guide import StyleGuide

MAX_MINUTES = 30  # style guide: 20-30 minutes per episode; anything longer gets split

Call = Callable[[str, str, Path | None], str]  # (system, user, pdf) -> reply text


class PlanError(Exception):
    pass


RULES = f"""You plan a series of single-voice audio lectures from source material. A
separate writer turns each episode into a spoken script, following the style guide below.

Decide where the episode breaks go. Read the content itself; bookmarks and headings are
hints only. Rules:
1. Content: each episode covers exactly one central idea. Split a section that holds
   several ideas; merge short sections that together form one idea.
2. Timing: each episode's script will run 20 to {MAX_MINUTES} minutes (about 150 spoken
   words per minute). Judge from the content and the per-page word counts: dense,
   proof-heavy pages need far more lecture time per page than expository prose. Never
   plan an episode over {MAX_MINUTES} minutes; split it instead.
3. Clean breaks: end each episode at a clean concept boundary. Never break mid-proof,
   mid-example or mid-derivation.
4. Skip front matter, exercises, indexes and bibliographies unless they carry the material.
   Gaps between episodes are fine. Episodes are in source order and never overlap.

Return only a JSON object, no other text:
{{"title": "series title", "author": "author(s)",
 "episodes": [{{"title": "...", "central_idea": "one sentence", "start": 12, "end": 19,
   "est_minutes": 25, "break_reason": "why the episode ends here",
   "listener_notes": "what to emphasize or what the listener should already know"}}]}}
"start" and "end" are inclusive source positions (pages for PDFs, lines for text).

The style guide the episodes will be written under:
"""


def source_digest(src: Source, start: int, end: int) -> str:
    """What the planner gets alongside the pages: word counts per PDF page, or numbered text."""
    if src.kind == "text":
        lines = extract_text(src, start, end).splitlines()
        return "".join(f"{n}| {line}\n" for n, line in enumerate(lines, start))
    scanned = set(scanned_pages(src, start, end))  # 0 words would mislead the planner
    # ponytail: reopens the PDF per page; fine for books, batch it if planning gets slow
    return "".join(
        f"page {n}: scanned, no text layer\n"
        if n in scanned
        else f"page {n}: {len(extract_text(src, n, n).split())} words\n"
        for n in range(start, end + 1)
    )


def _user_prompt(src: Source, start: int, end: int) -> str:
    unit = "pages" if src.kind == "pdf" else "lines"
    hints = [
        f"{'  ' * (lvl - 1)}{t} [{loc}]"
        for lvl, t, loc in outline(src)
        if start <= loc <= end
    ]
    parts = [f"Source: {src.path.name}. Plan {unit} {start}-{end}."]
    if src.kind == "pdf":
        parts.append(
            f"The source pages are attached as a PDF. The first page of the attached PDF is "
            f"source page {start}; report source page numbers."
        )
    parts.append("Bookmarks/headings (hints only):\n" + ("\n".join(hints) or "(none)"))
    label = "Words per page" if src.kind == "pdf" else "Numbered source text"
    parts.append(f"{label}:\n{source_digest(src, start, end)}")
    return "\n\n".join(parts)


def parse_plan(text: str) -> dict:
    """The JSON object in the reply, tolerating prose or code fences around it."""
    first, last = text.find("{"), text.rfind("}")
    if first == -1 or last < first:
        raise PlanError("the reply contains no JSON object")
    try:
        plan = json.loads(text[first : last + 1])
    except json.JSONDecodeError as e:
        raise PlanError(f"the reply's JSON is invalid: {e}") from e
    if not isinstance(plan, dict):
        raise PlanError("the reply's JSON is not an object")
    return plan


def validate(plan: dict, lo: int, hi: int) -> list[str]:
    """Problems with an LLM-proposed plan, in words the LLM can act on. Empty means valid."""
    episodes = plan.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        return ["the plan has no episodes"]
    errors, prev_end = [], lo - 1
    for i, e in enumerate(episodes, 1):
        where = f"episode {i}"
        if not isinstance(e, dict):
            errors.append(f"{where} is not an object")
            continue
        for key in ("title", "central_idea", "break_reason"):
            if not isinstance(e.get(key), str) or not e[key].strip():
                errors.append(f"{where}: {key} is missing")
        m = e.get("est_minutes")
        # json.loads accepts NaN/Infinity, and NaN slips past every comparison
        if (
            isinstance(m, bool)
            or not isinstance(m, int | float)
            or not math.isfinite(m)
            or m <= 0
        ):
            errors.append(f"{where}: est_minutes must be a positive finite number")
        elif m > MAX_MINUTES:
            errors.append(
                f"{where}: ~{m} minutes is over {MAX_MINUTES} minutes; "
                "split it at a clean concept break"
            )
        s, t = e.get("start"), e.get("end")
        if type(s) is not int or type(t) is not int:
            errors.append(f"{where}: start and end must be whole numbers")
        elif not lo <= s <= t <= hi:
            errors.append(f"{where}: range {s}-{t} is outside {lo}-{hi} or reversed")
        elif s <= prev_end:
            errors.append(
                f"{where}: range {s}-{t} overlaps or comes before episode {i - 1}"
            )
        else:
            prev_end = t
    return errors


def make_plan(src: Source, guide: StyleGuide, start: int, end: int, call: Call) -> dict:
    """Ask the planner for a plan of start..end; one repair round if it comes back invalid."""
    system = RULES + guide.script_prompt
    user = _user_prompt(src, start, end)
    with tempfile.TemporaryDirectory() as tmp:
        pdf = (
            slice_pdf(src, start, end, Path(tmp) / "source.pdf")
            if src.kind == "pdf"
            else None
        )
        prompt = user
        for _ in range(2):
            reply = call(system, prompt, pdf)
            try:
                plan = parse_plan(reply)
                errors = validate(plan, start, end)
            except PlanError as e:
                errors = [str(e)]
            if not errors:
                return plan
            prompt = (
                f"{user}\n\nYour previous reply:\n{reply}\n\nIt has these problems. "
                "Return a corrected JSON object only.\n- " + "\n- ".join(errors)
            )
    raise PlanError("the planner's plan is still invalid: " + "; ".join(errors))


def save(path: Path, text: str) -> None:
    """Write via a temp file and an atomic rename: a crash never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text.strip() + "\n", encoding="utf-8")
    os.replace(tmp, path)


HEADER = """# Episode plan. Edit freely: reorder, merge, split or change ranges, then write scripts.
# Ranges are inclusive and absolute: source pages for PDFs, lines for text.

"""


def _text(value) -> str:
    """LLM free-text fields as plain strings, whatever type the model returned."""
    return "" if value is None else str(value)


def write_plan(plan: dict, src: Source, path: Path, *, force: bool = False) -> Path:
    if path.exists() and not force:
        raise FileExistsError(
            f"{path} exists and may hold your edits; pass --force to replace it"
        )
    unit = "pages" if src.kind == "pdf" else "lines"
    data = {
        "title": _text(plan.get("title")) or src.path.stem,
        "author": _text(plan.get("author")),
        "source": str(src.path.resolve()),  # write may run from another directory
        "episodes": [
            {
                "n": n,
                "title": _text(e["title"]),
                "central_idea": _text(e["central_idea"]),
                unit: [e["start"], e["end"]],
                "est_minutes": e["est_minutes"],
                "break_reason": _text(e.get("break_reason")),
                "listener_notes": _text(e.get("listener_notes")),
            }
            for n, e in enumerate(plan["episodes"], 1)
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    save(path, HEADER + body)
    return path


def load_plan(path: Path) -> dict:
    """Read a (possibly hand-edited) plan.yaml, checking everything `write` relies on.

    Returns {title, author, source: Source, unit, episodes: [{n, title, central_idea,
    start, end, listener_notes}]} with episodes ordered by n.
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PlanError(f"{path} is not valid YAML: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("episodes"), list):
        raise PlanError(f"{path} has no episodes list")
    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        raise PlanError(
            f"{path}: source is missing; it must be the path of the book or notes"
        )
    try:
        src = open_source(source)
    except (FileNotFoundError, ValueError) as e:
        raise PlanError(
            f"{path}: source {data.get('source')!r} can't be opened ({e})"
        ) from e
    unit = "pages" if src.kind == "pdf" else "lines"
    errors, episodes, seen = [], [], set()
    for i, e in enumerate(data["episodes"], 1):
        where = f"episode entry {i}"
        if not isinstance(e, dict):
            errors.append(f"{where} is not a mapping")
            continue
        n = e.get("n")
        if type(n) is not int or n < 1:
            errors.append(f"{where}: n must be a positive whole number")
            continue
        where = f"episode {n}"
        if n in seen:
            errors.append(f"duplicate episode number {n}")
        seen.add(n)
        if not isinstance(e.get("title"), str) or not e["title"].strip():
            errors.append(f"{where}: title is missing")
        rng = e.get(unit)
        if not (
            isinstance(rng, list) and len(rng) == 2 and all(type(x) is int for x in rng)
        ):
            errors.append(f"{where}: {unit} must be [start, end]")
            continue
        start, end = rng
        if not 1 <= start <= end <= src.length:
            errors.append(
                f"{where}: {unit} {start}-{end} is outside 1-{src.length} or reversed"
            )
        episodes.append(
            {
                "n": n,
                "title": e.get("title", ""),
                "central_idea": _text(e.get("central_idea")),
                "start": start,
                "end": end,
                "listener_notes": _text(e.get("listener_notes")),
            }
        )
    if errors:
        raise PlanError(f"{path}: " + "; ".join(errors))
    return {
        "title": _text(data.get("title")) or src.path.stem,
        "author": _text(data.get("author")),
        "source": src,
        "unit": unit,
        "episodes": sorted(episodes, key=lambda e: e["n"]),
    }
