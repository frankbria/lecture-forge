import json
import shutil

import pymupdf
import pytest
import yaml

from lecture_forge import providers
from lecture_forge.cli import main
from lecture_forge.config import load_settings
from lecture_forge.plan import (
    MAX_MINUTES,
    PlanError,
    make_plan,
    parse_plan,
    source_digest,
    validate,
    write_plan,
)
from lecture_forge.source import open_source
from lecture_forge.style_guide import StyleGuide

GUIDE = StyleGuide("SCRIPT RULES", "CRITIQUE RULES", "SOURCE MATERIAL:\n{x}")


@pytest.fixture
def book(tmp_path):
    """Six pages, no bookmarks: groups on pages 1-3, rings on 4-6."""
    p = tmp_path / "algebra.pdf"
    doc = pymupdf.open()
    for n in range(1, 7):
        words = " ".join(["groups"] * (n * 10)) if n <= 3 else "rings are here"
        doc.new_page().insert_textbox(
            pymupdf.Rect(72, 72, 540, 760), f"Page {n}. {words}"
        )
    doc.save(p)
    return open_source(p)


@pytest.fixture
def notes(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("# Groups\nA group is...\n# Rings\nA ring is...\n", encoding="utf-8")
    return open_source(p)


def episode(start, end, minutes=25, **kw):
    return {
        "title": f"Ep {start}",
        "central_idea": "one idea",
        "start": start,
        "end": end,
        "est_minutes": minutes,
        "break_reason": "clean break",
        "listener_notes": "",
        **kw,
    }


def plan_json(*episodes, title="Algebra", author="A. Author"):
    return json.dumps({"title": title, "author": author, "episodes": list(episodes)})


# --- digest the model sees -----------------------------------------------------


def test_pdf_digest_lists_absolute_page_word_counts(book):
    digest = source_digest(book, 2, 3)
    assert "page 2: 22 words" in digest and "page 3: 32 words" in digest
    assert "page 1:" not in digest and "page 4:" not in digest


def test_text_digest_numbers_each_line(notes):
    assert source_digest(notes, 3, 4) == "3| # Rings\n4| A ring is...\n"


# --- parsing and validation --------------------------------------------------


def test_parse_finds_json_inside_prose_and_fences():
    text = 'Here is the plan:\n```json\n{"title": "T", "episodes": []}\n```\nDone.'
    assert parse_plan(text) == {"title": "T", "episodes": []}


@pytest.mark.parametrize("text", ["no json here", "{not: valid", '["a list"]'])
def test_parse_rejects_non_plans(text):
    with pytest.raises(PlanError):
        parse_plan(text)


def test_valid_plan_has_no_errors():
    assert validate(json.loads(plan_json(episode(1, 3), episode(5, 6))), 1, 6) == []


@pytest.mark.parametrize(
    "episodes,expected",
    [
        ([], "no episodes"),
        ([episode(0, 3)], "outside 1-6"),
        ([episode(4, 9)], "outside 1-6"),
        ([episode(3, 2)], "outside 1-6"),
        ([episode(1, 4), episode(4, 6)], "overlaps or comes before"),
        ([episode(4, 6), episode(1, 3)], "overlaps or comes before"),
        ([episode(1, 6, minutes=MAX_MINUTES + 5)], f"over {MAX_MINUTES} minutes"),
        ([episode(1, 6, minutes="long")], "est_minutes"),
        ([episode(1, 6, minutes=float("nan"))], "est_minutes"),
        ([episode(1, 6, minutes=float("inf"))], "est_minutes"),
        ([episode(1, 6, title="")], "title"),
        ([episode(1, 6, central_idea=None)], "central_idea"),
        ([episode(1.5, 6)], "start"),
    ],
)
def test_invalid_plans_are_explained(episodes, expected):
    errors = validate({"title": "T", "episodes": episodes}, 1, 6)
    assert any(expected in e for e in errors), errors


# --- make_plan: prompt, slicing, repair ---------------------------------------


def test_plan_prompt_carries_rules_digest_and_style_guide(book):
    calls = []

    def call(system, user, pdf):
        calls.append((system, user, pdf and pymupdf.open(pdf).page_count))
        return plan_json(episode(1, 3), episode(4, 6))

    plan = make_plan(book, GUIDE, 1, 6, call)
    system, user, pages = calls[0]
    assert "SCRIPT RULES" in system and str(MAX_MINUTES) in system
    assert "mid-proof" in system  # clean concept breaks
    assert "page 4: 5 words" in user
    assert pages == 6
    assert [e["start"] for e in plan["episodes"]] == [1, 4]


def test_range_slices_pdf_and_explains_page_offset(book):
    seen = {}

    def call(system, user, pdf):
        seen["pages"] = pymupdf.open(pdf).page_count
        seen["user"] = user
        return plan_json(episode(4, 6))

    make_plan(book, GUIDE, 4, 6, call)
    assert seen["pages"] == 3
    assert "first page of the attached PDF is source page 4" in seen["user"]


def test_text_source_sends_numbered_text_and_no_pdf(notes):
    seen = {}

    def call(system, user, pdf):
        seen.update(user=user, pdf=pdf)
        return plan_json(episode(1, 2), episode(3, 4))

    make_plan(notes, GUIDE, 1, 4, call)
    assert seen["pdf"] is None and "3| # Rings" in seen["user"]


def test_invalid_plan_gets_one_repair_attempt_with_the_problems(book):
    replies = iter(
        [plan_json(episode(1, 6, minutes=55)), plan_json(episode(1, 3), episode(4, 6))]
    )
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    plan = make_plan(book, GUIDE, 1, 6, call)
    assert len(prompts) == 2 and f"over {MAX_MINUTES} minutes" in prompts[1]
    assert len(plan["episodes"]) == 2


def test_plan_that_stays_invalid_fails(book):
    with pytest.raises(PlanError, match=f"over {MAX_MINUTES} minutes"):
        make_plan(
            book, GUIDE, 1, 6, lambda s, u, p: plan_json(episode(1, 6, minutes=90))
        )


def test_nan_minutes_from_raw_json_is_repaired_not_saved(book):
    nan_reply = plan_json(episode(1, 6)).replace(
        '"est_minutes": 25', '"est_minutes": NaN'
    )
    replies = iter([nan_reply, plan_json(episode(1, 6))])
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    assert make_plan(book, GUIDE, 1, 6, call)["episodes"][0]["est_minutes"] == 25
    assert len(prompts) == 2 and "est_minutes" in prompts[1]


def test_unparseable_reply_is_also_repaired(book):
    replies = iter(["sorry, here you go", plan_json(episode(1, 6))])
    assert make_plan(book, GUIDE, 1, 6, lambda s, u, p: next(replies))["episodes"]


# --- writing plan.yaml ----------------------------------------------------------


def test_plan_yaml_is_numbered_and_uses_pages_for_pdfs(book, tmp_path):
    plan = json.loads(plan_json(episode(1, 3), episode(4, 6)))
    out = write_plan(plan, book, tmp_path / "series" / "algebra" / "plan.yaml")
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["title"] == "Algebra" and data["source"] == str(book.path.resolve())
    assert [(e["n"], e["pages"]) for e in data["episodes"]] == [
        (1, [1, 3]),
        (2, [4, 6]),
    ]
    assert "start" not in data["episodes"][0]
    assert out.read_text(encoding="utf-8").startswith("# Episode plan")


def test_plan_yaml_stores_free_text_fields_as_strings(book, tmp_path):
    odd = episode(1, 6, break_reason=["ends", "here"], listener_notes=None, title=7)
    plan = {"title": 42, "author": None, "episodes": [odd]}
    data = yaml.safe_load(
        write_plan(plan, book, tmp_path / "p.yaml").read_text(encoding="utf-8")
    )
    e = data["episodes"][0]
    assert (data["title"], data["author"], e["title"]) == ("42", "", "7")
    assert e["break_reason"] == "['ends', 'here']" and e["listener_notes"] == ""


def test_plan_yaml_uses_lines_for_text_sources(notes, tmp_path):
    out = write_plan(
        json.loads(plan_json(episode(1, 4))), notes, tmp_path / "plan.yaml"
    )
    assert yaml.safe_load(out.read_text(encoding="utf-8"))["episodes"][0]["lines"] == [
        1,
        4,
    ]


def test_existing_plan_is_never_overwritten_without_force(book, tmp_path):
    out = tmp_path / "plan.yaml"
    out.write_text("my edits", encoding="utf-8")
    plan = json.loads(plan_json(episode(1, 6)))
    with pytest.raises(FileExistsError):
        write_plan(plan, book, out)
    assert out.read_text(encoding="utf-8") == "my edits"
    write_plan(plan, book, out, force=True)
    assert "my edits" not in out.read_text(encoding="utf-8")


# --- CLI ------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A working directory with a style guide and no .env."""
    guide = tmp_path / "guide.md"
    guide.write_text(
        "## Part 1\nS\n## Part 2\nC\n## Part 3\n```\nSOURCE MATERIAL:\n```\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STYLE_GUIDE_PATH", str(guide))
    monkeypatch.setenv("LECTURE_FORGE_PROVIDER", "anthropic")
    return tmp_path


def test_plan_command_writes_yaml_and_prints_summary(
    project, book, monkeypatch, capsys
):
    reply = plan_json(episode(1, 3, minutes=24), episode(4, 6, minutes=21))
    monkeypatch.setitem(providers.PROVIDERS, "anthropic", lambda s, u, p, st: reply)
    assert main(["plan", str(book.path), "--series", "algebra", "--auto"]) == 0
    out = capsys.readouterr().out
    assert (project / "series" / "algebra" / "plan.yaml").is_file()
    assert "1. Ep 1  pages 1-3  ~24 min" in out and "2 episodes" in out


def test_plan_command_range_and_refusal_to_overwrite(
    project, book, monkeypatch, capsys
):
    monkeypatch.setitem(
        providers.PROVIDERS, "anthropic", lambda s, u, p, st: plan_json(episode(4, 6))
    )
    assert (
        main(["plan", str(book.path), "--series", "s", "--range", "4-6", "--auto"]) == 0
    )
    assert (
        main(["plan", str(book.path), "--series", "s", "--range", "4-6", "--auto"]) == 1
    )
    assert "--force" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "UPPER"])
def test_plan_command_rejects_unsafe_series_names(project, book, bad, capsys):
    assert main(["plan", str(book.path), "--series", bad, "--auto"]) == 1
    assert "series" in capsys.readouterr().err
    assert not (project.parent / "escape").exists()


@pytest.mark.parametrize("rng", ["6-4", "x-2", "0-3", "5-9"])
def test_plan_command_rejects_bad_range(project, book, rng, capsys):
    assert (
        main(["plan", str(book.path), "--series", "s", "--range", rng, "--auto"]) == 1
    )
    assert "range" in capsys.readouterr().err.lower()


def test_plan_command_reports_provider_failure(project, book, monkeypatch, capsys):
    def down(*a):
        raise providers.ProviderError("anthropic", "400 bad request")

    monkeypatch.setitem(providers.PROVIDERS, "anthropic", down)
    assert main(["plan", str(book.path), "--series", "s", "--auto"]) == 1
    assert "anthropic: 400 bad request" in capsys.readouterr().err


# --- integration: a real planner run ----------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_real_planner_breaks_at_the_topic_boundary(tmp_path):
    """No bookmarks: the planner must find the groups/rings boundary from content."""
    p = tmp_path / "algebra.pdf"
    doc = pymupdf.open()
    groups = [
        "A group is a set with an associative operation, an identity, and inverses.",
        "Cosets of a subgroup partition the group into equal-sized pieces.",
        "Lagrange: the order of a subgroup divides the order of the group.",
    ]
    rings = [
        "A ring has addition and multiplication; multiplication distributes over addition.",
        "An ideal is a subring that absorbs multiplication by any ring element.",
        "Quotient rings are formed by collapsing an ideal to zero.",
    ]
    for text in groups + rings:
        doc.new_page().insert_textbox(pymupdf.Rect(72, 72, 540, 760), text * 12)
    doc.save(p)
    guide = StyleGuide(
        "Single-voice audio lecture, 20-30 minutes, one central idea.", "", ""
    )

    def call(system, user, pdf):
        return providers.complete(
            "claude-code", system, user, pdf, settings=load_settings(), attempts=2
        )

    plan = make_plan(open_source(p), guide, 1, 6, call)
    assert validate(plan, 1, 6) == []
    # The property that matters: no episode straddles the groups/rings boundary (3|4).
    # How finely it splits within a topic is the planner's call.
    assert not any(e["start"] <= 3 and e["end"] >= 4 for e in plan["episodes"])


def test_plan_records_absolute_source_path(notes, tmp_path, monkeypatch):
    monkeypatch.chdir(notes.path.parent)
    src = open_source("notes.md")  # relative, as typed on the command line
    out = write_plan(json.loads(plan_json(episode(1, 4))), src, tmp_path / "p.yaml")
    assert yaml.safe_load(out.read_text(encoding="utf-8"))["source"] == str(
        notes.path.resolve()
    )
