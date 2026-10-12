import itertools
import json
import re
import shutil
from pathlib import Path

import pymupdf
import pytest
import yaml

from lecture_forge import providers
from lecture_forge.cli import _worded, main
from lecture_forge.config import load_settings
from lecture_forge.plan import PlanError, load_plan
from lecture_forge.style_guide import StyleGuide, load_style_guide
from lecture_forge.write import (
    WriteError,
    episode_dir,
    episodes_to_write,
    fill_template,
    on_old_recap,
    script_state,
    sections,
    series_on_old_recap,
    stale_reason,
    write_episode,
)

TEMPLATE = """SOURCE MATERIAL:
{paste section text here}

SERIES / BOOK:
{title, author, chapter and section}

EPISODE NUMBER:
{n}

PREVIOUS EPISODE SUMMARY (optional):
{one paragraph}

PREVIOUS PUZZLE AND ANSWER (optional):
{puzzle} / {answer}

LISTENER NOTES (optional):
{what the listener already knows well, what they find hard, anything to emphasize}"""

GUIDE = StyleGuide("PART ONE RULES", "PART TWO CRITIQUE", TEMPLATE)

SCRIPT = (
    "Why can't a group of order six have a subgroup of order four? " * 60
)  # ~720 words


def draft_reply(
    script=SCRIPT, puzzle="Puzzle: order 9? Answer: yes.", summary="We met cosets."
):
    return (
        "**PRODUCTION NOTES** (not spoken)\n- Episode title: Cosets\n- Suggested split point: none\n\n"
        f"**SCRIPT**\n{script}\n\n**PUZZLE ANSWER**\n{puzzle}\n\n**EPISODE SUMMARY**\n{summary}\n"
    )


def critique_reply(script=SCRIPT, puzzle=None, summary=None):
    tail = f"\n\n## PUZZLE ANSWER\n{puzzle}\n" if puzzle is not None else "\n"
    tail += f"\n## EPISODE SUMMARY\n{summary}\n" if summary is not None else ""
    return (
        "1. **Visual dependencies:** none.\n2. **Mathematical or factual errors:** none.\n"
        "3. **Working-memory overload:** fine.\n4. **Tone violations:** none.\n"
        f"5. **TTS hazards:** none.\n6. **Revised script:**\n{script}{tail}"
    )


@pytest.fixture
def book(tmp_path):
    p = tmp_path / "algebra.pdf"
    doc = pymupdf.open()
    for n in range(1, 7):
        doc.new_page().insert_text((72, 72), f"Page {n} content")
    doc.save(p)
    return p


def make_plan_file(tmp_path, source, episodes, unit="pages", series="algebra", **top):
    data = {
        "title": "Algebra",
        "author": "A. Author",
        "source": str(source),
        **top,
        "episodes": [
            {
                "n": n,
                "title": f"Episode {n}",
                "central_idea": f"idea {n}",
                unit: rng,
                "est_minutes": 25,
                "break_reason": "clean",
                "listener_notes": f"notes {n}",
            }
            for n, rng in episodes
        ],
    }
    path = tmp_path / "series" / series / "plan.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


# --- loading the (possibly hand-edited) plan --------------------------------------


def test_load_plan_orders_episodes_by_number(tmp_path, book):
    path = make_plan_file(tmp_path, book, [(2, [4, 6]), (1, [1, 3])])
    plan = load_plan(path)
    assert [(e["n"], e["start"], e["end"]) for e in plan["episodes"]] == [
        (1, 1, 3),
        (2, 4, 6),
    ]
    assert plan["source"].path == book and plan["unit"] == "pages"


@pytest.mark.parametrize(
    "episodes,expected",
    [
        ([(1, [1, 9])], "outside 1-6"),
        ([(1, [3, 2])], "outside 1-6"),
        ([(1, [1, 3]), (1, [4, 6])], "duplicate episode number 1"),
        ([(0, [1, 3])], "positive whole number"),
        ([(1, "1-3")], "pages must be [start, end]"),
    ],
)
def test_load_plan_explains_bad_edits(tmp_path, book, episodes, expected):
    with pytest.raises(PlanError, match=re.escape(expected)):
        load_plan(make_plan_file(tmp_path, book, episodes))


def test_load_plan_reports_moved_source(tmp_path, book):
    path = make_plan_file(tmp_path, tmp_path / "gone.pdf", [(1, [1, 3])])
    with pytest.raises(PlanError, match="gone.pdf"):
        load_plan(path)


def test_load_plan_reports_broken_yaml(tmp_path):
    path = tmp_path / "plan.yaml"
    path.write_text("episodes: [unclosed", encoding="utf-8")
    with pytest.raises(PlanError, match="plan.yaml"):
        load_plan(path)


def test_load_plan_requires_titles(tmp_path, book):
    path = make_plan_file(tmp_path, book, [(1, [1, 3])])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["episodes"][0]["title"] = ""
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(PlanError, match="title"):
        load_plan(path)


# --- reading model output -----------------------------------------------------


@pytest.mark.parametrize(
    "heading",
    [
        "**SCRIPT**",
        "SCRIPT",
        "## SCRIPT",
        "**SCRIPT** (spoken text only, nothing else)",
        "SCRIPT:",
    ],
)
def test_sections_tolerates_heading_styles(heading):
    text = f"PRODUCTION NOTES\nn\n{heading}\nspoken words\nPUZZLE ANSWER\nanswer"
    got = sections(text, ["PRODUCTION NOTES", "SCRIPT", "PUZZLE ANSWER"])
    assert got["SCRIPT"] == "spoken words" and got["PUZZLE ANSWER"] == "answer"


def test_sections_handles_numbered_and_inline_headings():
    text = "5. **TTS hazards:** none\n6. **Revised script:** First line.\nSecond line."
    got = sections(text, ["TTS hazards", "Revised script"])
    assert (
        got["Revised script"] == "First line.\nSecond line."
        and got["TTS hazards"] == "none"
    )


def test_sections_ignores_prose_that_mentions_a_heading_word():
    text = "SCRIPT\nThe script of history is long, and the puzzle answer comes later in this lecture.\n"
    assert sections(text, ["SCRIPT", "PUZZLE ANSWER"]) == {
        "SCRIPT": "The script of history is long, and the puzzle answer comes later in this lecture."
    }


# --- filling the user's input template -----------------------------------------------


def test_template_is_filled_by_field_label():
    out = fill_template(
        TEMPLATE,
        source="THE PAGES",
        series="Algebra, A. Author",
        number="2",
        summary="LAST TIME",
        puzzle="OLD PUZZLE",
        notes="NOTES",
    )
    assert "SOURCE MATERIAL:\nTHE PAGES" in out and "EPISODE NUMBER:\n2" in out
    assert "PREVIOUS EPISODE SUMMARY (optional):\nLAST TIME" in out
    assert "PREVIOUS PUZZLE AND ANSWER (optional):\nOLD PUZZLE" in out
    assert "LISTENER NOTES (optional):\nNOTES" in out and "{" not in out


def test_unknown_template_fields_are_marked_not_provided():
    out = fill_template("MOOD:\n{how you feel}\n", source="s", series="", number="1",
                        summary="", puzzle="", notes="")  # fmt: skip
    assert out == "MOOD:\n(not provided)\n"


# --- writing an episode -------------------------------------------------------------


def run(
    tmp_path, book, call, n=1, episodes=((1, [1, 3]), (2, [4, 6])), series="algebra",
    **top,
):  # fmt: skip
    path = make_plan_file(tmp_path, book, list(episodes), series=series, **top)
    plan = load_plan(path, root=tmp_path)
    ep = next(e for e in plan["episodes"] if e["n"] == n)
    return write_episode(plan, ep, GUIDE, call, path.parent)


def test_first_episode_two_passes_and_all_files(tmp_path, book):
    calls = []

    def call(system, user, pdf):
        calls.append((system, user, pdf and pymupdf.open(pdf).page_count))
        return draft_reply() if len(calls) == 1 else critique_reply()

    result = run(tmp_path, book, call)
    (s1, u1, p1), (s2, u2, p2) = calls
    assert s1.startswith("PART ONE RULES") and "EPISODE SUMMARY" in s1
    assert (
        "PART ONE RULES" in s2 and "PART TWO CRITIQUE" in s2 and "Revised script" in s2
    )
    assert p1 == p2 == 3  # both passes see the episode's pages (1-3) and nothing else
    assert (
        "source page 1" in u1
        and "PREVIOUS EPISODE SUMMARY (optional):\n(none: first" in u1
    )
    assert "idea 1" in u1 and "notes 1" in u1  # plan's central idea and listener notes
    assert SCRIPT.strip()[:40] in u2  # critique reviews the draft script
    d = episode_dir(tmp_path / "series" / "algebra", 1)
    for f in (
        "draft.md",
        "critique.md",
        "notes.md",
        "puzzle_answer.md",
        "summary.md",
        "script.txt",
    ):
        assert (d / f).read_text(encoding="utf-8").strip(), f
    assert (d / "script.txt").read_text(encoding="utf-8") == SCRIPT.strip() + "\n"
    written = json.loads((d / "written.json").read_text(encoding="utf-8"))
    assert written == {
        "source": str(book.resolve()),
        "unit": "pages",
        "start": 1,
        "end": 3,
    }
    assert result.words == len(SCRIPT.split()) and result.minutes == pytest.approx(
        result.words / 150
    )


def test_second_episode_gets_previous_summary_and_puzzle(tmp_path, book):
    replies = iter(
        [
            draft_reply(summary="EP1 SUMMARY", puzzle="EP1 PUZZLE+ANSWER"),
            critique_reply(),
        ]
    )
    run(tmp_path, book, lambda s, u, p: next(replies), n=1)
    seen = []
    replies2 = iter([draft_reply(), critique_reply()])

    def call(system, user, pdf):
        seen.append(user)
        return next(replies2)

    run(tmp_path, book, call, n=2)
    assert "PREVIOUS EPISODE SUMMARY (optional):\nEP1 SUMMARY" in seen[0]
    assert "PREVIOUS PUZZLE AND ANSWER (optional):\nEP1 PUZZLE+ANSWER" in seen[0]
    assert "source page 4" in seen[0]


def test_episode_needs_the_previous_one_written_first(tmp_path, book):
    with pytest.raises(WriteError, match="episode 1 first"):
        run(tmp_path, book, lambda s, u, p: pytest.fail("called"), n=2)


# --- a series that continues another one --------------------------------------------


def write_ch3(tmp_path, book, upto=2):
    """Series ch3 ("Statics"), with its episodes up to `upto` written."""
    replies = itertools.chain.from_iterable(
        (
            draft_reply(summary=f"CH3 EP{n} SUMMARY", puzzle=f"CH3 EP{n} PUZZLE"),
            critique_reply(),
        )
        for n in range(1, upto + 1)
    )
    for n in range(1, upto + 1):
        run(tmp_path, book, lambda s, u, p: next(replies), n=n, series="ch3", title="Statics")  # fmt: skip


def write_ch4(tmp_path, book, n=1):
    """Write episode n of series ch4, which continues ch3; the draft prompt it sent."""
    seen = []
    replies = iter(
        [draft_reply(summary="CH4 EP1 SUMMARY", puzzle="CH4 EP1 PUZZLE"), critique_reply()]
    )  # fmt: skip

    def call(system, user, pdf):
        seen.append(user)
        return next(replies)

    run(tmp_path, book, call, n=n, series="ch4", continues="ch3")
    return seen[0]


def test_first_episode_of_a_continuing_series_recaps_the_others_last(tmp_path, book):
    write_ch3(tmp_path, book)
    user = write_ch4(tmp_path, book)
    assert (
        "PREVIOUS EPISODE SUMMARY (optional):\n"
        "(From the previous series, Statics.) CH3 EP2 SUMMARY" in user
    )
    assert "PREVIOUS PUZZLE AND ANSWER (optional):\nCH3 EP2 PUZZLE" in user
    assert "(none: first" not in user and "source page 1" in user


def test_later_episodes_of_a_continuing_series_recap_their_own_series(tmp_path, book):
    write_ch3(tmp_path, book)
    write_ch4(tmp_path, book)
    user = write_ch4(tmp_path, book, n=2)
    assert "PREVIOUS EPISODE SUMMARY (optional):\nCH4 EP1 SUMMARY" in user
    assert "PREVIOUS PUZZLE AND ANSWER (optional):\nCH4 EP1 PUZZLE" in user
    assert "previous series" not in user and "CH3" not in user


def test_continuing_needs_the_other_series_last_episode_written(tmp_path, book):
    write_ch3(tmp_path, book, upto=1)
    with pytest.raises(WriteError, match="series ch3 .* no finished last episode"):
        run(tmp_path, book, lambda s, u, p: pytest.fail("called"), series="ch4", continues="ch3")  # fmt: skip


def test_continuing_refuses_a_last_episode_written_for_another_range(tmp_path, book):
    write_ch3(tmp_path, book)
    make_plan_file(tmp_path, book, [(1, [1, 3]), (2, [5, 6])], series="ch3")
    with pytest.raises(WriteError, match="series ch3 .* no finished last episode"):
        run(tmp_path, book, lambda s, u, p: pytest.fail("called"), series="ch4", continues="ch3")  # fmt: skip


def test_continuing_a_series_without_episodes_or_a_plan_is_refused(tmp_path, book):
    with pytest.raises(WriteError, match="series ch3 .* isn't planned"):
        run(tmp_path, book, lambda s, u, p: pytest.fail("called"), series="ch4", continues="ch3")  # fmt: skip
    make_plan_file(tmp_path, book, [], series="ch3")
    with pytest.raises(WriteError, match="series ch3 .* no finished last episode"):
        run(tmp_path, book, lambda s, u, p: pytest.fail("called"), series="ch4", continues="ch3")  # fmt: skip


def test_the_continued_series_relative_source_is_under_the_project_root(
    tmp_path, book, monkeypatch
):
    write_ch3(tmp_path, book)
    make_plan_file(tmp_path, book.name, [(1, [1, 3]), (2, [4, 6])], series="ch3")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert "CH3 EP2 SUMMARY" in write_ch4(tmp_path, book)


def test_a_moved_source_in_the_continued_series_is_reported(tmp_path, book):
    write_ch3(tmp_path, book)
    make_plan_file(tmp_path, tmp_path / "gone.pdf", [(1, [1, 3])], series="ch3")
    with pytest.raises(PlanError, match="ch3.*gone.pdf"):
        run(tmp_path, book, lambda s, u, p: pytest.fail("called"), series="ch4", continues="ch3")  # fmt: skip


@pytest.mark.parametrize(
    "value", ["../x", "ch3/x", "Ch3", 3, ["ch3"], "algebra", 0, False, ""]
)
def test_load_plan_rejects_a_continues_that_is_no_other_series(tmp_path, book, value):
    path = make_plan_file(tmp_path, book, [(1, [1, 3])], continues=value)
    with pytest.raises(PlanError, match="continues must be the name of another series"):
        load_plan(path)


@pytest.mark.parametrize("top", [{}, {"continues": None}])
def test_load_plan_without_continues_continues_nothing(tmp_path, book, top):
    path = make_plan_file(tmp_path, book, [(1, [1, 3])], **top)
    assert load_plan(path)["continues"] == ""


def test_critique_can_replace_the_puzzle(tmp_path, book):
    replies = iter(
        [draft_reply(puzzle="OLD"), critique_reply(puzzle="NEW PUZZLE + ANSWER")]
    )
    run(tmp_path, book, lambda s, u, p: next(replies))
    d = episode_dir(tmp_path / "series" / "algebra", 1)
    assert (d / "puzzle_answer.md").read_text(
        encoding="utf-8"
    ).strip() == "NEW PUZZLE + ANSWER"


def test_critique_can_replace_the_summary_the_next_episode_recaps(tmp_path, book):
    revised = "We met cosets; the Lagrange section was cut."
    replies = iter([draft_reply(summary="OLD"), critique_reply(summary=revised)])
    run(tmp_path, book, lambda s, u, p: next(replies))
    d = episode_dir(tmp_path / "series" / "algebra", 1)
    assert (d / "summary.md").read_text(encoding="utf-8").strip() == revised
    assert "Lagrange" not in (d / "script.txt").read_text(encoding="utf-8")
    replies, prompts = iter([draft_reply(), critique_reply()]), []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call, n=2)
    assert revised in prompts[0]  # episode 2's "last time" recap


def test_without_a_revised_summary_the_drafts_is_kept(tmp_path, book):
    replies = iter([draft_reply(summary="We met cosets."), critique_reply()])
    run(tmp_path, book, lambda s, u, p: next(replies))
    d = episode_dir(tmp_path / "series" / "algebra", 1)
    assert (d / "summary.md").read_text(encoding="utf-8").strip() == "We met cosets."


def test_an_episode_summary_line_in_the_critique_is_no_heading(tmp_path, book):
    """Commentary or a spoken line starting "Episode summary:" mustn't replace summary.md
    or cut the revised script short: the restated summary is a heading on its own line."""
    spoken = f"{SCRIPT}\nEpisode summary: we covered cosets.\nAnd one more spoken line."
    crit = critique_reply(script=spoken).replace(
        "2. **Mathematical",
        "Episode summary: mentions Lagrange, which is cut.\n"
        "- **Episode summary:** still says Lagrange.\n2. **Mathematical",
    )
    replies = iter([draft_reply(summary="We met cosets."), crit])
    run(tmp_path, book, lambda s, u, p: next(replies))
    d = episode_dir(tmp_path / "series" / "algebra", 1)
    assert (d / "summary.md").read_text(encoding="utf-8").strip() == "We met cosets."
    assert (
        (d / "script.txt")
        .read_text(encoding="utf-8")
        .rstrip()
        .endswith("one more spoken line.")
    )


@pytest.mark.parametrize(
    "line",
    ["EPISODE SUMMARY: {}", "## EPISODE SUMMARY: {}", "**EPISODE SUMMARY:** {}",
     "## Episode Summary: {}"],
)  # fmt: skip
def test_a_restated_summary_marked_as_a_heading_may_be_inline(tmp_path, book, line):
    """Capitals as asked, or a # heading: never left in the spoken script."""
    revised = "We met cosets; Lagrange was cut."
    crit = critique_reply() + "\n" + line.format(revised) + "\n"
    replies = iter([draft_reply(summary="OLD"), crit])
    run(tmp_path, book, lambda s, u, p: next(replies))
    d = episode_dir(tmp_path / "series" / "algebra", 1)
    assert (d / "summary.md").read_text(encoding="utf-8").strip() == revised
    assert "Lagrange" not in (d / "script.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("section", ["summary", "puzzle"])
def test_an_empty_restated_section_gets_one_repair_round(tmp_path, book, section):
    empty, fixed = {section: ""}, {section: "Restated after the revision."}
    replies = iter([draft_reply(), critique_reply(**empty), critique_reply(**fixed)])
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call)
    name = "EPISODE SUMMARY" if section == "summary" else "PUZZLE ANSWER"
    assert len(prompts) == 3 and f"the {name} section is empty" in prompts[2]
    f = "summary.md" if section == "summary" else "puzzle_answer.md"
    d = episode_dir(tmp_path / "series" / "algebra", 1)
    assert (d / f).read_text(encoding="utf-8").strip() == "Restated after the revision."


def test_critique_sees_the_drafts_summary_and_when_to_restate_it(tmp_path, book):
    replies, calls = iter([draft_reply(summary="We met cosets."), critique_reply()]), []

    def call(system, user, pdf):
        calls.append((system, user))
        return next(replies)

    run(tmp_path, book, call)
    system, user = calls[1]
    assert (
        "EPISODE SUMMARY" in system and "changes what the episode establishes" in system
    )
    assert "We met cosets." in user.split("SCRIPT TO REVIEW", 1)[1]


def test_draft_missing_sections_gets_one_repair_round(tmp_path, book):
    replies = iter(["**SCRIPT**\nonly a script", draft_reply(), critique_reply()])
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call)
    assert "PUZZLE ANSWER" in prompts[1] and "EPISODE SUMMARY" in prompts[1]


def test_truncated_revised_script_gets_one_repair_round(tmp_path, book):
    replies = iter(
        [draft_reply(), critique_reply(script="Too short."), critique_reply()]
    )
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call)
    assert len(prompts) == 3 and "shorter than half" in prompts[2]


def test_output_that_stays_broken_fails_without_marking_done(tmp_path, book):
    with pytest.raises(WriteError, match="SCRIPT"):
        run(tmp_path, book, lambda s, u, p: "I'd rather not.")
    assert not (episode_dir(tmp_path / "series" / "algebra", 1) / "script.txt").exists()


def test_split_suggestion_is_reported(tmp_path, book):
    draft = draft_reply().replace(
        "Suggested split point: none", "Suggested split point: after Lagrange"
    )
    replies = iter([draft, critique_reply()])
    result = run(tmp_path, book, lambda s, u, p: next(replies))
    assert "after Lagrange" in result.split


def test_text_source_sends_text_not_pdf(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("# Bayes\nPriors matter.\n", encoding="utf-8")
    seen = []

    def call(system, user, pdf):
        seen.append((user, pdf))
        return draft_reply() if len(seen) == 1 else critique_reply()

    plan = load_plan(make_plan_file(tmp_path, notes, [(1, [1, 2])], unit="lines"))
    write_episode(plan, plan["episodes"][0], GUIDE, call, tmp_path / "s")
    assert (
        seen[0][1] is None
        and "Priors matter." in seen[0][0]
        and "Priors matter." in seen[1][0]
    )


# --- CLI ----------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch, book):
    guide = tmp_path / "guide.md"
    guide.write_text(
        f"## Part 1\nPART ONE RULES\n## Part 2\nPART TWO CRITIQUE\n## Part 3\n```\n{TEMPLATE}\n```\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STYLE_GUIDE_PATH", str(guide))
    monkeypatch.setenv("LECTURE_FORGE_PROVIDER", "anthropic")
    make_plan_file(tmp_path, book, [(1, [1, 3]), (2, [4, 6])])
    calls = []

    def fake(system, user, pdf, settings):
        calls.append(user)
        return critique_reply() if "SCRIPT TO REVIEW" in user else draft_reply()

    monkeypatch.setitem(providers.PROVIDERS, "anthropic", fake)
    return tmp_path, calls


def test_write_all_writes_in_order_then_skips_done(project, capsys):
    root, calls = project
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    out = capsys.readouterr().out
    assert (
        "Episode 1" in out
        and "Episode 2" in out
        and "min" in out
        and "script.txt" in out
    )
    assert (root / "series/algebra/episodes/02/script.txt").is_file() and len(
        calls
    ) == 4
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    assert (
        "already written" in capsys.readouterr().out and len(calls) == 4
    )  # nothing re-run


def test_write_one_episode_refuses_to_overwrite_without_force(project, capsys):
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    capsys.readouterr()
    assert main(["write", "algebra", "--episode", "1", "--auto"]) == 1
    assert "--force" in capsys.readouterr().err
    assert main(["write", "algebra", "--episode", "1", "--force", "--auto"]) == 0
    assert (
        "note: episode 2 is built on the old summary and puzzle; rewrite it with --force"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "args,msg",
    [
        (["write", "nope", "--all"], "plan.yaml"),
        (["write", "algebra", "--episode", "7"], "no episode 7"),
        (["write", "algebra", "--episode", "2"], "episode 1 first"),
        (["write", "../x", "--all"], "series"),
    ],
)
def test_write_errors_are_clear(project, args, msg, capsys):
    assert main([*args, "--auto"]) == 1
    assert msg in capsys.readouterr().err


# --- integration: real model output parses ---------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_real_model_output_parses_through_both_passes(tmp_path, book):
    """A short real run: the formats the model actually produces must parse."""
    real = (
        load_style_guide("prompts/style-guide.md")
        if __import__("os").path.exists("prompts/style-guide.md")
        else GUIDE
    )
    short = StyleGuide(
        real.script_prompt
        + "\n\nFOR THIS TEST ONLY: keep the SCRIPT to about 150 words.",
        real.critique_prompt
        + "\n\nFOR THIS TEST ONLY: keep the revised script to about 150 words.",
        real.input_template,
    )

    def call(system, user, pdf):
        return providers.complete(
            "claude-code", system, user, pdf, settings=load_settings(), attempts=2
        )

    plan = load_plan(make_plan_file(tmp_path, book, [(1, [1, 3])]))
    result = write_episode(plan, plan["episodes"][0], short, call, tmp_path / "out")
    d = episode_dir(tmp_path / "out", 1)
    assert (
        result.words > 0
    )  # parsing is the point; how short the model goes varies run to run
    for f in ("script.txt", "puzzle_answer.md", "summary.md", "notes.md"):
        assert (d / f).read_text(encoding="utf-8").strip(), f


# Real production notes (episode 1 of a live run): "split" appears in content lines.
REAL_NOTES = """**Episode title:** Cutting a Group into Equal Pieces
**What was omitted or simplified, and why:**
- The source gives only three statements: the definition of a group, that cosets split a group into equal-sized pieces.
**Anything to verify against the source:**
- Page 2: cosets split the group into equal-sized pieces.
**Suggested split point:** None. The script is about 4,000 words, roughly 27 minutes."""


@pytest.mark.parametrize(
    "notes,expected",
    [
        (REAL_NOTES, ""),
        (REAL_NOTES.replace("None. The script", "After the proof of Lagrange. The script"),
         "After the proof of Lagrange. The script is about 4,000 words, roughly 27 minutes."),
        ("- Suggested split point: n/a", ""),
        ("Suggested split point:\nSplit after the second example.", "Split after the second example."),
        ("- Cosets split the group evenly.", ""),
    ],
)  # fmt: skip
def test_split_suggestion_reads_only_the_split_point_field(notes, expected):
    from lecture_forge.write import _split_suggestion

    assert _split_suggestion(notes) == expected


# Real critique tail (live run): commentary after a rule line must never be spoken.
REAL_TAIL = (
    "What does that tell you about what every group of size seven must look like?\n\n---\n\n"
    "I couldn't render the PDF as page images because the image tool isn't installed, "
    "but I could read the full text of all three pages."
)


@pytest.mark.parametrize("rule", ["---", "***", "___", "- - -", "-----"])
def test_commentary_after_a_rule_line_is_cut_from_the_script(tmp_path, book, rule):
    revised = SCRIPT + "\n" + REAL_TAIL.replace("---", rule)
    replies = iter([draft_reply(), critique_reply(script=revised)])
    run(tmp_path, book, lambda s, u, p: next(replies))
    script = (episode_dir(tmp_path / "series" / "algebra", 1) / "script.txt").read_text(
        encoding="utf-8"
    )
    assert script.rstrip().endswith("must look like?")
    assert "image tool" not in script


def test_critique_is_told_not_to_add_commentary(tmp_path, book):
    systems = []

    def call(system, user, pdf):
        systems.append(system)
        return draft_reply() if len(systems) == 1 else critique_reply()

    run(tmp_path, book, call)
    assert "nothing after" in systems[1].lower()


# --- review fixes (codex on PR #11) --------------------------------------------------


@pytest.mark.parametrize("value", [None, "", 7, ["a.pdf"]])
def test_load_plan_rejects_missing_or_odd_source(tmp_path, book, value):
    path = make_plan_file(tmp_path, book, [(1, [1, 3])])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["source"] = value
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(PlanError, match="source"):
        load_plan(path)


def test_interrupted_rewrite_never_leaves_a_done_episode_with_mixed_files(
    tmp_path, book, monkeypatch
):
    from lecture_forge import plan as plan_mod  # the atomic writer lives in plan.py

    replies = iter([draft_reply(summary="OLD SUMMARY"), critique_reply()])
    run(tmp_path, book, lambda s, u, p: next(replies))  # episode 1 done, old continuity
    d = episode_dir(tmp_path / "series" / "algebra", 1)
    real_replace = plan_mod.os.replace

    def crash_on_script(src, dst):
        if str(dst).endswith("script.txt"):
            raise KeyboardInterrupt  # power cut just before the episode is marked done
        real_replace(src, dst)

    monkeypatch.setattr(plan_mod.os, "replace", crash_on_script)
    replies = iter([draft_reply(summary="NEW SUMMARY"), critique_reply()])
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, book, lambda s, u, p: next(replies))
    # Either still done with consistent old files, or not done at all: here it must be not done.
    assert not (d / "script.txt").exists()
    assert "NEW SUMMARY" in (d / "summary.md").read_text(encoding="utf-8")
    with pytest.raises(WriteError, match="episode 1 first"):
        run(tmp_path, book, lambda s, u, p: pytest.fail("ran"), n=2)


def test_files_are_published_atomically(tmp_path, book, monkeypatch):
    from lecture_forge import plan as plan_mod  # the atomic writer lives in plan.py

    published = []
    real_replace = plan_mod.os.replace
    monkeypatch.setattr(
        plan_mod.os,
        "replace",
        lambda s, d: (published.append(Path(d).name), real_replace(s, d)),
    )
    replies = iter([draft_reply(), critique_reply()])
    run(tmp_path, book, lambda s, u, p: next(replies))
    assert published[-2:] == ["written.json", "script.txt"]  # the done marker goes last
    assert {"critique.md", "puzzle_answer.md", "summary.md"} <= set(published)
    assert not list(episode_dir(tmp_path / "series" / "algebra", 1).glob("*.tmp"))


# --- is a script still the one the plan asks for? ---------------------------------------


def _state(tmp_path, book, meta, episodes=((1, [1, 3]),), unit="pages"):
    """script_state and stale_reason of episode 1, written with this written.json."""
    plan = load_plan(make_plan_file(tmp_path, book, list(episodes), unit=unit))
    sdir = tmp_path / "series" / "algebra"
    d = episode_dir(sdir, 1)
    d.mkdir(parents=True, exist_ok=True)
    (d / "script.txt").write_text("words\n", encoding="utf-8")
    if meta is not None:
        (d / "written.json").write_text(
            meta if isinstance(meta, str) else json.dumps(meta), encoding="utf-8"
        )
    ep = plan["episodes"][0]
    return script_state(sdir, plan, ep), stale_reason(sdir, plan, ep)


def test_script_state_is_missing_without_a_script(tmp_path, book):
    plan = load_plan(make_plan_file(tmp_path, book, [(1, [1, 3])]))
    assert (
        script_state(tmp_path / "series" / "algebra", plan, plan["episodes"][0])
        == "missing"
    )


@pytest.mark.parametrize(
    "meta",
    [
        None,  # legacy: written before written.json existed, so trusted
        {"source": "x", "unit": "pages", "start": 1, "end": 3},
        {"source": "/moved/elsewhere.pdf", "unit": "pages", "start": 1, "end": 3},
    ],
)
def test_script_state_is_current(tmp_path, book, meta):
    assert _state(tmp_path, book, meta)[0] == "current"


@pytest.mark.parametrize(
    "meta,reason",
    [
        (
            {"source": "x", "unit": "pages", "start": 1, "end": 2},
            "written for pages 1-2, the plan now says pages 1-3",
        ),
        (
            {"source": "x", "unit": "lines", "start": 1, "end": 3},
            "written from lines 1-3, the plan now uses pages",
        ),
        ("{torn", "its written.json is unreadable"),
        ("[1, 3]", "its written.json is unreadable"),
        ({"unit": "pages"}, "its written.json is unreadable"),
        (  # hand-edited: never "written for pages 1-3, the plan now says pages 1-3"
            {"source": "x", "unit": "pages", "start": "1", "end": 3},
            "its written.json is unreadable",
        ),
    ],
)
def test_script_state_is_stale(tmp_path, book, meta, reason):
    assert _state(tmp_path, book, meta) == ("stale", reason)


def test_episode_after_a_stale_one_is_refused(tmp_path, book):
    replies = iter([draft_reply(), critique_reply()])
    run(tmp_path, book, lambda s, u, p: next(replies))
    with pytest.raises(WriteError, match="rewrite episode 1 first"):
        run(tmp_path, book, lambda s, u, p: pytest.fail("called"), n=2,
            episodes=((1, [1, 2]), (2, [3, 6])))  # fmt: skip


def test_write_rewrites_an_episode_whose_range_changed(project, book, capsys):
    root, calls = project
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    capsys.readouterr()
    make_plan_file(root, book, [(1, [1, 3]), (2, [5, 6])])
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    out = capsys.readouterr().out
    assert "Episode 1: Episode 1: already written, skipping" in out
    assert (
        "Episode 2: Episode 2: plan changed (written for pages 4-6, the plan now says "
        "pages 5-6), rewriting" in out
    )
    assert len(calls) == 6  # 4 for the first run, 2 for episode 2's rewrite
    meta = root / "series/algebra/episodes/02/written.json"
    assert json.loads(meta.read_text(encoding="utf-8"))["start"] == 5


def test_write_all_notes_a_later_episode_left_on_a_rewritten_ones_old_recap(
    project, book, capsys
):
    root, calls = project
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    capsys.readouterr()
    make_plan_file(root, book, [(1, [1, 2]), (2, [4, 6])])
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    out = capsys.readouterr().out
    assert (
        "note: episode 2 is built on the old summary and puzzle; rewrite it with --force"
        in out
    )
    assert "Episode 2: Episode 2: already written, skipping" in out
    assert len(calls) == 6  # only episode 1 is rewritten


def test_write_all_notes_later_episodes_after_writing_a_lost_script_again(
    project, capsys
):
    root, calls = project
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    capsys.readouterr()
    (root / "series/algebra/episodes/01/script.txt").unlink()
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    out = capsys.readouterr().out
    assert (
        "note: episode 2 is built on the old summary and puzzle; rewrite it with --force"
        in out
    )
    assert len(calls) == 6  # only episode 1 is written again


def test_write_one_stale_episode_needs_no_force(project, book, capsys):
    root, calls = project
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    make_plan_file(root, book, [(1, [1, 3]), (2, [5, 6])])
    assert main(["write", "algebra", "--episode", "2", "--auto"]) == 0
    assert "plan changed" in capsys.readouterr().out and len(calls) == 6


def test_written_json_that_cannot_be_read_is_stale(tmp_path, book):
    plan = load_plan(make_plan_file(tmp_path, book, [(1, [1, 3])]))
    sdir, ep = tmp_path / "series" / "algebra", plan["episodes"][0]
    d = episode_dir(sdir, 1)
    (d / "written.json").mkdir(parents=True)  # read_text raises an OSError
    (d / "script.txt").write_text("words\n", encoding="utf-8")
    assert script_state(sdir, plan, ep) == "stale"
    assert stale_reason(sdir, plan, ep) == "its written.json is unreadable"


def test_critique_without_a_revised_script_gets_one_repair_round(tmp_path, book):
    replies = iter([draft_reply(), "1. Visual dependencies: none.", critique_reply()])
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call)
    assert "the Revised script section is missing or empty" in prompts[2]


def test_rule_line_in_the_middle_of_the_script_gets_a_repair_round(tmp_path, book):
    """A '---' scene break mustn't silently drop the half of the lecture after it."""
    split = SCRIPT + "\n\n---\n\n" + SCRIPT
    replies = iter([draft_reply(), critique_reply(script=split), critique_reply()])
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call)
    assert len(prompts) == 3 and "rule line" in prompts[2]


def test_rule_line_in_the_middle_of_the_draft_gets_a_repair_round(tmp_path, book):
    split = SCRIPT + "\n\n***\n\n" + SCRIPT
    replies = iter([draft_reply(script=split), draft_reply(), critique_reply()])
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call)
    assert len(prompts) == 3 and "rule line" in prompts[1]
    assert "SCRIPT TO REVIEW" in prompts[2]  # the critique saw the repaired draft


@pytest.mark.parametrize("after,repaired", [(140, False), (160, True)])
def test_up_to_150_words_after_a_rule_are_cut_silently(tmp_path, book, after, repaired):
    """The threshold is flat: 10% of a 4,000-word lecture would be minutes of it."""
    long = SCRIPT * 5  # ~3,600 words, like a real episode
    tail = " ".join(["word"] * after)
    replies = iter([draft_reply(script=long), critique_reply(script=f"{long}\n---\n{tail}"),
                    critique_reply(script=long)])  # fmt: skip
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call)
    assert (len(prompts) == 3) is repaired
    if repaired:
        assert f"with {after} words after it" in prompts[2]
        assert "keep the spoken script after it" in prompts[2]


def test_a_revision_starting_with_a_rule_names_the_rule(tmp_path, book):
    replies = iter(
        [draft_reply(), critique_reply(script=f"---\n{SCRIPT}"), critique_reply()]
    )
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call)
    assert "rule line" in prompts[2] and "missing or empty" not in prompts[2]


def test_a_rule_and_an_empty_section_are_repaired_together(tmp_path, book):
    split = critique_reply(script=SCRIPT + "\n\n---\n\n" + SCRIPT, puzzle="")
    replies = iter([draft_reply(), split, critique_reply()])
    prompts = []

    def call(system, user, pdf):
        prompts.append(user)
        return next(replies)

    run(tmp_path, book, call)
    assert "rule line" in prompts[2] and "PUZZLE ANSWER section is empty" in prompts[2]


# --- the write decision in the engine (#32) ------------------------------------------------


def test_episodes_to_write_decides_write_rewrite_and_skip(tmp_path, book):
    replies = iter([draft_reply(), critique_reply()])
    run(tmp_path, book, lambda s, u, p: next(replies))  # episode 1 written
    sdir = tmp_path / "series" / "algebra"
    plan = load_plan(sdir / "plan.yaml")
    todo = episodes_to_write(plan, sdir, episode=None, force=False)
    assert [(ep["n"], action) for ep, action, _ in todo] == [(1, "skip"), (2, "write")]
    forced = episodes_to_write(plan, sdir, episode=None, force=True)
    assert [action for _, action, _ in forced] == ["rewrite", "write"]
    with pytest.raises(FileExistsError, match="episode 1 is already written"):
        episodes_to_write(plan, sdir, episode=1, force=False)
    with pytest.raises(ValueError, match="no episode 7"):
        episodes_to_write(plan, sdir, episode=7, force=False)


def test_episodes_to_write_says_why_a_stale_one_is_rewritten(tmp_path, book):
    replies = iter([draft_reply(), critique_reply()])
    run(tmp_path, book, lambda s, u, p: next(replies))
    sdir = tmp_path / "series" / "algebra"
    plan = load_plan(make_plan_file(tmp_path, book, [(1, [1, 2]), (2, [4, 6])]))
    (ep, action, why), _ = episodes_to_write(plan, sdir, episode=None, force=False)
    assert ep["n"] == 1 and action == "rewrite" and "1-3" in why and "1-2" in why


FOUR = [(1, [1, 2]), (2, [3, 4]), (3, [5, 5]), (4, [6, 6])]
ALL = {"episode": None, "force": False}


def _replanned(tmp_path, book, written, changed):
    """(plan, series folder) after writing episodes `written` of FOUR and then changing
    the `changed` ranges in the plan."""
    replies = itertools.cycle([draft_reply(), critique_reply()])
    for n in written:
        run(tmp_path, book, lambda s, u, p: next(replies), n=n, episodes=FOUR)
    episodes = [(n, changed.get(n, rng)) for n, rng in FOUR]
    plan = load_plan(make_plan_file(tmp_path, book, episodes))
    return plan, tmp_path / "series" / "algebra"


def _left(plan, sdir, **kw):
    todo = episodes_to_write(plan, sdir, **kw)
    left = {
        ep["n"]: [n for n, _ in on_old_recap(plan, sdir, todo, ep)] for ep, _, _ in todo
    }
    return {n: later for n, later in left.items() if later}


@pytest.mark.parametrize(
    "changed, kw, expected",
    [
        ({1: [1, 1]}, ALL, {1: [2, 3, 4]}),
        # episode 3 is rewritten in the same run: 1 doesn't list it, and it lists 4
        ({1: [1, 1], 3: [4, 5]}, ALL, {1: [2], 3: [4]}),
        # a moved boundary makes two neighbours stale: episode 3 is listed once, under 2
        ({1: [1, 1], 2: [2, 4], 4: [5, 6]}, ALL, {2: [3]}),
        ({}, {"episode": None, "force": True}, {}),
        # --episode rewrites nothing else: a stale later episode is listed too
        ({3: [4, 5]}, {"episode": 1, "force": True}, {1: [2, 3, 4]}),
        ({}, {"episode": 2, "force": True}, {2: [3, 4]}),
    ],
)
def test_on_old_recap_lists_later_written_episodes_this_run_leaves(
    tmp_path, book, changed, kw, expected
):
    """All four episodes are written, then the plan changes the `changed` ranges."""
    plan, sdir = _replanned(tmp_path, book, (1, 2, 3, 4), changed)
    assert _left(plan, sdir, **kw) == expected


def test_on_old_recap_says_which_later_episodes_are_stale(tmp_path, book):
    """A front end words its advice by state: a stale episode is rewritten without force."""
    plan, sdir = _replanned(tmp_path, book, (1, 2, 3, 4), {3: [4, 5]})
    todo = episodes_to_write(plan, sdir, episode=1, force=True)
    assert on_old_recap(plan, sdir, todo, plan["episodes"][0]) == [
        (2, "current"), (3, "stale"), (4, "current")
    ]  # fmt: skip


OLD = "built on the old summary and puzzle"
FORCE = "with --force if the recap matters"


@pytest.mark.parametrize(
    "changed, notes",
    [
        ({}, [f"episodes 2, 3 and 4 are {OLD}; rewrite them {FORCE}"]),
        (
            {3: [4, 5]},
            [
                f"episodes 2 and 4 are {OLD}; rewrite them {FORCE}",
                f"episode 3 is {OLD} and no longer matches the plan; write it again (no --force needed)",
            ],
        ),
        (
            {2: [2, 4], 3: [4, 5]},
            [
                f"episode 4 is {OLD}; rewrite it {FORCE}",
                f"episodes 2 and 3 are {OLD} and no longer match the plan; write them again in order (no --force needed)",
            ],
        ),
    ],
)
def test_write_words_the_old_recap_note_by_number_and_state(
    project, book, capsys, changed, notes
):
    """All four episodes are written, the plan changes the `changed` ranges, and episode 1
    is rewritten alone: a stale later episode is not told to use --force."""
    root, _ = project
    make_plan_file(root, book, FOUR)
    assert main(["write", "algebra", "--all", "--auto"]) == 0
    make_plan_file(root, book, [(n, changed.get(n, rng)) for n, rng in FOUR])
    capsys.readouterr()
    assert main(["write", "algebra", "--episode", "1", "--force", "--auto"]) == 0
    out = capsys.readouterr().out.splitlines()
    found = [line for line in out if line.startswith("  note: ")]
    assert [line.removeprefix("  note: ") for line in found] == notes


def test_on_old_recap_counts_a_lost_script_written_again_like_a_rewrite(tmp_path, book):
    """Episode 1 is stale and episode 2's script is gone (an interrupted rewrite, or a
    deleted folder), so 2 is a "write": it lists 3 and 4, and nothing lists it."""
    plan, sdir = _replanned(tmp_path, book, (1, 2, 3, 4), {1: [1, 1]})
    (episode_dir(sdir, 2) / "script.txt").unlink()
    todo = episodes_to_write(plan, sdir, **ALL)
    assert [action for _, action, _ in todo] == ["rewrite", "write", "skip", "skip"]
    assert _left(plan, sdir, **ALL) == {2: [3, 4]}
    assert _left(plan, sdir, episode=1, force=False) == {1: [3, 4]}
    assert _left(plan, sdir, episode=2, force=False) == {2: [3, 4]}


def test_on_old_recap_leaves_out_an_episode_the_run_wrote_for_the_first_time(
    tmp_path, book
):
    """Asked after the run, as a GUI may: episode 4 is written by then, from the new recap."""
    plan, sdir = _replanned(tmp_path, book, (1, 2, 3), {3: [4, 5]})
    todo = episodes_to_write(plan, sdir, **ALL)
    assert [action for _, action, _ in todo] == ["skip", "skip", "rewrite", "write"]
    replies = itertools.cycle([draft_reply(), critique_reply()])
    for ep in plan["episodes"][2:]:
        write_episode(plan, ep, GUIDE, lambda s, u, p: next(replies), sdir)
    assert on_old_recap(plan, sdir, todo, plan["episodes"][2]) == []


def test_write_episode_reports_each_pass_and_repair(tmp_path, book):
    from lecture_forge.progress import Progress

    events = []
    replies = iter(["**SCRIPT**\nonly a script", draft_reply(), critique_reply()])
    plan = load_plan(make_plan_file(tmp_path, book, [(1, [1, 3]), (2, [4, 6])]))
    ep = plan["episodes"][0]
    write_episode(plan, ep, GUIDE, lambda s, u, p: next(replies),
                  tmp_path / "series" / "algebra", on_progress=events.append)  # fmt: skip
    assert events == [
        Progress("draft", episode=1), Progress("draft", note="repair round", episode=1),
        Progress("critique", episode=1),
    ]  # fmt: skip


def test_write_episode_reports_a_critique_repair_round(tmp_path, book):

    events = []
    replies = iter(
        [draft_reply(), critique_reply(script="Too short."), critique_reply()]
    )
    plan = load_plan(make_plan_file(tmp_path, book, [(1, [1, 3]), (2, [4, 6])]))
    write_episode(plan, plan["episodes"][0], GUIDE, lambda s, u, p: next(replies),
                  tmp_path / "series" / "algebra", on_progress=events.append)  # fmt: skip
    assert [(e.stage, e.note) for e in events] == [
        ("draft", ""), ("critique", ""), ("critique", "repair round")
    ]  # fmt: skip


def test_write_one_written_episode_names_the_cli_flag(project, capsys):
    root, _ = project
    assert main(["write", "algebra", "--episode", "1", "--auto"]) == 0
    capsys.readouterr()
    assert main(["write", "algebra", "--episode", "1", "--auto"]) == 1
    assert capsys.readouterr().err == (
        "error: episode 1 is already written; pass --force to rewrite it\n"
    )
    sdir = root / "series" / "algebra"
    with pytest.raises(FileExistsError) as e:  # the engine names no flag
        episodes_to_write(load_plan(sdir / "plan.yaml"), sdir, episode=1, force=False)
    assert (
        str(e.value)
        == "episode 1 is already written; it is rewritten only if you ask for that"
    )


# --- a continued series (#93) -----------------------------------------------------------


@pytest.mark.parametrize(
    "lost, named",
    [
        (["summary.md"], "summary.md"),
        (["puzzle_answer.md"], "puzzle_answer.md"),
        (["summary.md", "puzzle_answer.md"], "summary.md and puzzle_answer.md"),
    ],
)
def test_a_lost_summary_or_puzzle_says_which_episode_to_rewrite(
    tmp_path, book, lost, named
):
    """Both files are saved before script.txt, so one that is missing was lost since."""
    write_ch3(tmp_path, book, upto=1)
    for name in lost:
        (episode_dir(tmp_path / "series" / "ch3", 1) / name).unlink()
    with pytest.raises(WriteError) as e:
        run(tmp_path, book, lambda s, u, p: pytest.fail("called"), n=2, series="ch3")
    wrong = f"episode 1 of series ch3 has a script but no {named}"
    assert str(e.value) == f"{wrong}; rewrite it"
    assert _worded(e.value) == (
        f"{wrong}; rewrite it: lecture-forge write ch3 --episode 1 --force"
    )


def test_a_lost_summary_in_the_continued_series_names_that_series(tmp_path, book):
    write_ch3(tmp_path, book)
    (episode_dir(tmp_path / "series" / "ch3", 2) / "puzzle_answer.md").unlink()
    with pytest.raises(WriteError) as e:
        run(tmp_path, book, lambda s, u, p: pytest.fail("called"), series="ch4", continues="ch3")  # fmt: skip
    assert str(e.value) == (
        "episode 2 of series ch3 has a script but no puzzle_answer.md; rewrite it"
    )


def _after(tmp_path, n=2, **kw):
    """series_on_old_recap for episode n of ch3, in the run that `kw` asks for."""
    sdir = tmp_path / "series" / "ch3"
    plan = load_plan(sdir / "plan.yaml")
    todo = episodes_to_write(plan, sdir, **kw)
    return series_on_old_recap(plan, sdir, todo, plan["episodes"][n - 1])


LAST = {"episode": 2, "force": True}  # a run that rewrites ch3's last episode


def test_a_series_that_continues_this_one_is_named_when_the_last_episode_is_written_again(
    tmp_path, book
):
    write_ch3(tmp_path, book)
    assert _after(tmp_path, **LAST) == []  # nothing continues ch3
    two = [(1, [1, 3]), (2, [4, 6])]
    make_plan_file(tmp_path, book, two, series="ch4", continues="ch3")
    assert (
        _after(tmp_path, **LAST) == []
    )  # ch4 is planned; its first episode isn't written
    write_ch4(tmp_path, book)
    assert _after(tmp_path, **LAST) == [("ch4", 1, "current")]
    assert _after(tmp_path, episode=None, force=True) == [("ch4", 1, "current")]
    # Only for the last episode, and only when the run writes it.
    assert _after(tmp_path, n=1, episode=1, force=True) == []
    assert _after(tmp_path, episode=1, force=True) == []
    assert _after(tmp_path, episode=None, force=False) == []


def test_a_stale_first_episode_of_the_continuing_series_is_told_apart(tmp_path, book):
    write_ch3(tmp_path, book)
    write_ch4(tmp_path, book)
    changed = [(1, [1, 2]), (2, [4, 6])]
    make_plan_file(tmp_path, book, changed, series="ch4", continues="ch3")
    assert _after(tmp_path, **LAST) == [("ch4", 1, "stale")]


def test_a_continuing_series_with_a_relative_source_is_found_from_anywhere(
    tmp_path, book, monkeypatch
):
    """A relative source is under the project root, not under the folder `write` runs in."""
    write_ch3(tmp_path, book)
    write_ch4(tmp_path, book)
    two = [(1, [1, 3]), (2, [4, 6])]
    make_plan_file(tmp_path, book.name, two, series="ch4", continues="ch3")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert _after(tmp_path, **LAST) == [("ch4", 1, "current")]


def test_only_series_that_continue_this_one_and_can_be_read_are_named(tmp_path, book):
    write_ch3(tmp_path, book)
    write_ch4(tmp_path, book)
    write_ch4(tmp_path, book, n=2)  # ch5 continues ch4, so ch4 must be finished
    two = [(1, [1, 3]), (2, [4, 6])]
    replies = itertools.cycle([draft_reply(), critique_reply()])
    for series, top in [("ch5", {"continues": "ch4"}), ("other", {}), ("too", {"continues": "ch3"})]:  # fmt: skip
        run(tmp_path, book, lambda s, u, p: next(replies), series=series, **top)
    make_plan_file(tmp_path, book, [], series="empty", continues="ch3")
    broken = tmp_path / "series" / "broken" / "plan.yaml"
    broken.parent.mkdir()
    broken.write_text("continues: ch3\nepisodes: [", encoding="utf-8")
    (tmp_path / "series" / "notes.txt").write_text("not a series", encoding="utf-8")
    (tmp_path / "series" / "no-plan").mkdir()
    binary = tmp_path / "series" / "binary" / "plan.yaml"
    binary.parent.mkdir()
    binary.write_bytes(b"\xff\xfe not UTF-8")
    (tmp_path / "series" / "folder" / "plan.yaml").mkdir(parents=True)
    later = ((2, [1, 3]), (3, [4, 6]))  # a first episode that isn't number 1
    run(tmp_path, book, lambda s, u, p: next(replies), n=2, episodes=later, series="odd", continues="ch3")  # fmt: skip
    assert make_plan_file(tmp_path, book, two, series="moved", continues="ch3")
    moved = tmp_path / "series" / "moved" / "plan.yaml"
    moved.write_text(moved.read_text().replace(str(book), "gone.pdf"), encoding="utf-8")
    assert _after(tmp_path, **LAST) == [
        ("ch4", 1, "current"), ("odd", 2, "current"), ("too", 1, "current")
    ]  # fmt: skip
