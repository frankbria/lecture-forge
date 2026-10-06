import re
import shutil

import pymupdf
import pytest
import yaml

from lecture_forge import providers
from lecture_forge.cli import main
from lecture_forge.config import load_settings
from lecture_forge.plan import PlanError, load_plan
from lecture_forge.style_guide import StyleGuide, load_style_guide
from lecture_forge.write import (
    WriteError,
    episode_dir,
    fill_template,
    sections,
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


def critique_reply(script=SCRIPT, puzzle=None):
    tail = f"\n\n## PUZZLE ANSWER\n{puzzle}\n" if puzzle else "\n"
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


def make_plan_file(tmp_path, source, episodes, unit="pages"):
    data = {
        "title": "Algebra",
        "author": "A. Author",
        "source": str(source),
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
    path = tmp_path / "series" / "algebra" / "plan.yaml"
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


def run(tmp_path, book, call, n=1, episodes=((1, [1, 3]), (2, [4, 6]))):
    plan = load_plan(make_plan_file(tmp_path, book, list(episodes)))
    ep = next(e for e in plan["episodes"] if e["n"] == n)
    return write_episode(plan, ep, GUIDE, call, tmp_path / "series" / "algebra")


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


def test_critique_can_replace_the_puzzle(tmp_path, book):
    replies = iter(
        [draft_reply(puzzle="OLD"), critique_reply(puzzle="NEW PUZZLE + ANSWER")]
    )
    run(tmp_path, book, lambda s, u, p: next(replies))
    d = episode_dir(tmp_path / "series" / "algebra", 1)
    assert (d / "puzzle_answer.md").read_text(
        encoding="utf-8"
    ).strip() == "NEW PUZZLE + ANSWER"


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
        "episode 2" in capsys.readouterr().out.lower()
    )  # warns later episodes may be stale


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
    assert result.words > 50
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
