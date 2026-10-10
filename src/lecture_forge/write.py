"""Write one episode: a script pass (style guide Part 1), then a critique pass (Part 2)."""

import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lecture_forge.plan import Call, save
from lecture_forge.source import extract_text, slice_pdf
from lecture_forge.style_guide import StyleGuide

WORDS_PER_MINUTE = 150  # the style guide's speaking rate

DRAFT_SECTIONS = ["PRODUCTION NOTES", "SCRIPT", "PUZZLE ANSWER", "EPISODE SUMMARY"]
CRITIQUE_SECTIONS = [
    "Visual dependencies",
    "Mathematical or factual errors",
    "Working-memory overload",
    "Tone violations",
    "TTS hazards",
    "Revised script",
    "PUZZLE ANSWER",
    "EPISODE SUMMARY",
]

# Added at runtime so the user's style guide file is never edited.
SCRIPT_ADDENDUM = """

Output format addendum (from the lecture-forge app): put each section heading on its own
line, exactly PRODUCTION NOTES, SCRIPT and PUZZLE ANSWER, then add a fourth section,
EPISODE SUMMARY. In PUZZLE ANSWER, restate the closing puzzle in one line, then answer it.
EPISODE SUMMARY (not spoken) is one paragraph on what this episode established; it is used
to recap this episode at the start of the next one."""

CRITIQUE_ADDENDUM = """

The script under review was written under the script-writing style guide above.
Output format addendum (from the lecture-forge app): give items 1-6 under their own heading
lines. Item 6's heading is exactly "Revised script", followed by only the spoken script and
nothing after it except the optional final sections below, each heading on a line of its
own. No separator line, no remarks about your process. If your revision changes
the closing puzzle, add a final section headed PUZZLE ANSWER that restates the new puzzle in
one line and answers it. If your revision changes what the episode establishes (it cuts or
reframes material the draft's EPISODE SUMMARY mentions), add a final section headed
EPISODE SUMMARY, in capitals, with one paragraph on what the revised episode establishes.
It replaces the draft's summary, which recaps this episode at the start of the next one."""


class WriteError(Exception):
    pass


@dataclass(frozen=True)
class Written:
    dir: Path
    words: int
    minutes: float
    split: str  # the writer's suggested split point, "" if none


_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


def _spoken(script: str) -> str:
    """The script up to any markdown rule line (---, ***). Models put remarks after one, and
    anything left in script.txt is read aloud. The style guide bans markdown in scripts."""
    lines = script.splitlines()
    cut = next((i for i, line in enumerate(lines) if _RULE.match(line)), len(lines))
    return "\n".join(lines[:cut]).strip()


def _cut_problem(name: str, text: str) -> str | None:
    """A rule line with more after it than a remark: a scene break would silently drop the
    rest of the lecture, so it goes back for repair instead."""
    words = len(text.split())
    dropped = words - len(_spoken(text).split())
    if dropped > max(150, words // 10):  # a trailing remark (~30 words) is cut silently
        return (
            f"the {name} has a markdown rule line (---, ***, ___) with {dropped} words after "
            "it; anything after a rule line is dropped, so remove rule lines from the script"
        )
    return None


def episode_dir(series_dir: Path, n: int) -> Path:
    return Path(series_dir) / "episodes" / f"{n:02d}"


def stale_reason(series_dir: Path, plan: dict, ep: dict) -> str:
    """Why episode ep's script was written for other pages than the plan now says, or "".

    No written.json means a script from before it existed: trusted, never rewritten. The
    source path is not compared, so a moved book file never makes every episode stale."""
    path = episode_dir(series_dir, ep["n"]) / "written.json"
    if not path.exists():
        return ""
    unreadable = (
        "its written.json is unreadable"  # torn or hand-edited: can't vouch for it
    )
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        unit, start, end = meta["unit"], meta["start"], meta["end"]
    except (OSError, ValueError, TypeError, KeyError):
        return unreadable
    if type(start) is not int or type(end) is not int:
        return unreadable
    want = plan["unit"]
    if unit != want:
        return f"written from {unit} {start}-{end}, the plan now uses {want}"
    if (start, end) != (ep["start"], ep["end"]):
        return f"written for {unit} {start}-{end}, the plan now says {want} {ep['start']}-{ep['end']}"
    return ""


def script_state(series_dir: Path, plan: dict, ep: dict) -> str:
    """Whether episode ep's script is "missing", "current" or "stale" (written for a range
    the plan no longer has)."""
    if not (episode_dir(series_dir, ep["n"]) / "script.txt").exists():
        return "missing"
    return "stale" if stale_reason(series_dir, plan, ep) else "current"


# --- reading model output ----------------------------------------------------------

_LEAD = re.compile(r"^[\s#>]*(?:\d+[.)]\s*)?")


def _heading(line: str, names: list[str], strict=()) -> tuple[str, str] | None:
    """(name, inline text) if the line is one of the section headings, in any common style:
    **SCRIPT**, ## SCRIPT, SCRIPT:, 6. **Revised script:** text, SCRIPT (spoken text only).
    A name in `strict` takes text after it only on a line marked as a heading, written in
    capitals as given or as a # heading, so "Episode summary: ..." in prose is no heading."""
    s = _LEAD.sub("", line.replace("*", "")).strip()
    for name in sorted(names, key=len, reverse=True):
        if not s.lower().startswith(name.lower()):
            continue
        rest = re.sub(r"^\s*\([^)]*\)", "", s[len(name) :]).strip()
        if not rest:
            return name, ""
        marked = s.startswith(name) or line.lstrip().startswith("#")
        if rest.startswith(":") and (
            name not in strict or not rest[1:].strip() or marked
        ):
            return name, rest[1:].strip()
    return None


def sections(text: str, names: list[str], strict=()) -> dict[str, str]:
    """Split a reply into its named sections; text before the first heading is dropped."""
    out: dict[str, str] = {}
    current, buf = None, []
    for line in text.splitlines():
        if hit := _heading(line, names, strict):
            if current:
                out[current] = "\n".join(buf).strip()
            current, buf = hit[0], [hit[1]] if hit[1] else []
        elif current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


# --- the user's input template (style guide Part 3) ----------------------------------

_LABEL = re.compile(r"^([A-Z][A-Z /&-]*?)\s*(?:\([^)]*\))?\s*:\s*$")
_PLACEHOLDER = re.compile(r"^\s*\{.*\}\s*$")
_FIELDS = [  # label keyword -> value; matched by keyword so edits to the guide keep working
    ("SOURCE", "source"),
    ("SERIES", "series"),
    ("BOOK", "series"),
    ("EPISODE NUMBER", "number"),
    ("SUMMARY", "summary"),
    ("PUZZLE", "puzzle"),
    ("LISTENER", "notes"),
]


def fill_template(template: str, **values: str) -> str:
    """Replace each {placeholder} line with the value for the label above it."""
    out, label = [], ""
    for line in template.splitlines():
        if m := _LABEL.match(line.strip()):
            label = m[1]
        elif _PLACEHOLDER.match(line):
            key = next((k for word, k in _FIELDS if word in label), None)
            line = (values.get(key) if key else "") or "(not provided)"
        out.append(line)
    return "\n".join(out) + ("\n" if template.endswith("\n") else "")


# --- writing ---------------------------------------------------------------------------


def _ask(
    call: Call, system: str, user: str, pdf: Path | None, names: list[str],
    check: Callable[[dict[str, str]], list[str]], strict=(),
) -> tuple[str, dict[str, str]]:  # fmt: skip
    """One call, checked; one repair round with the problems listed, then WriteError."""
    prompt = user
    for _ in range(2):
        text = call(system, prompt, pdf)
        found = sections(text, names, strict)
        if not (problems := check(found)):
            return text, found
        prompt = (
            f"{user}\n\nYour previous reply:\n{text}\n\nIt has these problems. "
            "Reply again in full, fixing them.\n- " + "\n- ".join(problems)
        )
    raise WriteError("; ".join(problems))


def _check_draft(found: dict[str, str]) -> list[str]:
    problems = [
        f"the {name} section is missing or empty"
        for name in ("SCRIPT", "PUZZLE ANSWER", "EPISODE SUMMARY")
        if not found.get(name)
    ]
    if found.get("SCRIPT") and (cut := _cut_problem("SCRIPT", found["SCRIPT"])):
        problems.append(cut)
    return problems


def _check_critique(found: dict[str, str], draft: str) -> list[str]:
    revised = _spoken(found.get("Revised script", ""))
    if not revised:
        return ["the Revised script section is missing or empty"]
    if cut := _cut_problem("revised script", found["Revised script"]):
        return [cut]
    if empty := [
        n for n in ("PUZZLE ANSWER", "EPISODE SUMMARY") if n in found and not found[n]
    ]:
        return [
            f"the {n} section is empty; give it, or leave the heading out"
            for n in empty
        ]
    have, want = len(revised.split()), len(draft.split())
    if have < want / 2:  # a summary or a cut-off reply, not a revision
        return [
            (
                f"the revised script is shorter than half the draft ({have} vs {want} words); "
                "give the complete revised script"
            )
        ]
    return []


_SPLIT_FIELD = re.compile(
    r"^[\s>*#-]*(?:suggested\s+)?split\s+point[^:]*:\s*(.*)$", re.IGNORECASE
)
_NO_SPLIT = re.compile(r"(?i)(none|n/?a|no\b|not needed)")


def _split_suggestion(notes: str) -> str:
    """The production notes' "Suggested split point" field, or "" if it says none.

    Only that field counts: content lines often say "split" ("cosets split a group")."""
    lines = [line.replace("*", "") for line in notes.splitlines()]
    for i, line in enumerate(lines):
        if m := _SPLIT_FIELD.match(line):
            value = m[1].strip() or next(
                (x.strip() for x in lines[i + 1 :] if x.strip()), ""
            )
            return "" if not value or _NO_SPLIT.match(value) else value
    return ""


def _previous(plan: dict, ep: dict, series_dir: Path) -> tuple[str, str] | None:
    """(summary, puzzle and answer) of the episode before this one, which must be written."""
    earlier = [e for e in plan["episodes"] if e["n"] < ep["n"]]
    if not earlier:
        return None
    n = earlier[-1]["n"]
    state = script_state(series_dir, plan, earlier[-1])
    if state == "missing":
        raise WriteError(
            f"write episode {n} first: episode {ep['n']} continues from its summary and puzzle"
        )
    if state == "stale":
        raise WriteError(
            f"rewrite episode {n} first: its script no longer matches the plan"
        )
    prev = episode_dir(series_dir, n)
    summary, puzzle = (
        (prev / f).read_text(encoding="utf-8").strip()
        for f in ("summary.md", "puzzle_answer.md")
    )
    return summary, puzzle


def write_episode(
    plan: dict, ep: dict, guide: StyleGuide, call: Call, series_dir: Path
) -> Written:
    """Draft, critique and save one episode. script.txt is written last: it marks done."""
    prev = _previous(plan, ep, series_dir)
    src, unit, start, end = plan["source"], plan["unit"], ep["start"], ep["end"]
    by = f", {plan['author']}" if plan["author"] else ""
    series = (
        f'{plan["title"]}{by}: episode "{ep["title"]}", source {unit} {start}-{end}'
    )
    notes = f"Central idea for this episode (from the plan): {ep['central_idea']}"
    if ep["listener_notes"]:
        notes += f"\n{ep['listener_notes']}"
    d = episode_dir(series_dir, ep["n"])
    with tempfile.TemporaryDirectory() as tmp:
        if src.kind == "pdf":
            pdf = slice_pdf(src, start, end, Path(tmp) / "source.pdf")
            source = f"The attached PDF: source pages {start}-{end}. Its first page is source page {start}."
        else:
            pdf, source = None, extract_text(src, start, end)
        first = "(none: first episode)"
        user = fill_template(
            guide.input_template, source=source, series=series, number=str(ep["n"]),
            summary=prev[0] if prev else first, puzzle=prev[1] if prev else first, notes=notes,
        )  # fmt: skip
        draft_text, draft = _ask(
            call,
            guide.script_prompt + SCRIPT_ADDENDUM,
            user,
            pdf,
            DRAFT_SECTIONS,
            _check_draft,
        )
        d.mkdir(parents=True, exist_ok=True)
        save(d / "draft.md", draft_text)  # kept even if the critique fails
        save(d / "notes.md", draft.get("PRODUCTION NOTES", ""))
        crit_text, crit = _ask(
            call,
            guide.script_prompt + "\n\n" + guide.critique_prompt + CRITIQUE_ADDENDUM,
            f"SOURCE MATERIAL:\n{source}\n\nSCRIPT TO REVIEW:\n{_spoken(draft['SCRIPT'])}"
            "\n\nTHE DRAFT'S EPISODE SUMMARY (not spoken; recaps this episode in the next "
            f"one):\n{draft['EPISODE SUMMARY']}",
            pdf,
            CRITIQUE_SECTIONS,
            lambda found: _check_critique(found, draft["SCRIPT"]),
            # "Episode summary: ..." in commentary or the spoken text is no heading
            strict=("EPISODE SUMMARY",),
        )
    script = _spoken(crit["Revised script"])
    # A rewrite is "not done" while its files change, so an interrupted rewrite can never
    # leave an old script.txt marking new (mismatched) continuity files as finished.
    (d / "script.txt").unlink(missing_ok=True)
    save(d / "critique.md", crit_text)
    save(d / "puzzle_answer.md", crit.get("PUZZLE ANSWER") or draft["PUZZLE ANSWER"])
    save(d / "summary.md", crit.get("EPISODE SUMMARY") or draft["EPISODE SUMMARY"])
    written = {
        "source": str(src.path.resolve()),
        "unit": unit,
        "start": start,
        "end": end,
    }
    save(d / "written.json", json.dumps(written))
    save(d / "script.txt", script)
    words = len(script.split())
    return Written(
        d,
        words,
        words / WORDS_PER_MINUTE,
        _split_suggestion(draft.get("PRODUCTION NOTES", "")),
    )
