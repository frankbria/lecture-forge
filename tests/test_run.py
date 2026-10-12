import pytest
import yaml
from test_plan import episode, plan_json
from test_render import FakeTTS, beep  # noqa: F401  (beep is a fixture)
from test_source import toc_pdf
from test_write import TEMPLATE, critique_reply, draft_reply

from lecture_forge import providers
from lecture_forge import render as r
from lecture_forge.cli import main
from lecture_forge.plan import RULES


@pytest.fixture
def project(tmp_path, monkeypatch, beep):  # noqa: F811
    """A notes file, a style guide, fake LLM and ElevenLabs, and no terminal."""
    guide = tmp_path / "guide.md"
    guide.write_text(f"## Part 1\nS\n## Part 2\nC\n## Part 3\n```\n{TEMPLATE}\n```\n")
    notes = tmp_path / "notes.md"
    notes.write_text("# Groups\nA group is...\n# Rings\nA ring is...\n")
    monkeypatch.chdir(tmp_path)
    for k, v in {
        "STYLE_GUIDE_PATH": str(guide),
        "LECTURE_FORGE_PROVIDER": "anthropic",
        "ELEVENLABS_API_KEY": "k",
        "ELEVENLABS_VOICE_ID": "voice-1",
        "ELEVENLABS_MODEL_ID": "eleven_v3",
        "LECTURE_FORGE_OUTPUT_DIR": str(tmp_path / "Lectures"),
    }.items():
        monkeypatch.setenv(k, v)
    llm = []

    def fake_llm(system, user, pdf, settings):
        llm.append(user)
        if system.startswith(RULES):
            return plan_json(episode(1, 2), episode(3, 4))
        return critique_reply() if "SCRIPT TO REVIEW" in user else draft_reply()

    monkeypatch.setitem(providers.PROVIDERS, "anthropic", fake_llm)
    tts = FakeTTS(beep)
    monkeypatch.setattr(r, "elevenlabs_tts", lambda s, **kw: tts)
    monkeypatch.setattr(r, "characters_left", lambda s: 100_000)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    return tmp_path, notes, llm, tts


def mp3s(root):
    return sorted(p.name for p in (root / "Lectures").rglob("*.mp3"))


def test_run_without_auto_stops_after_the_plan_then_resumes(project, capsys):
    root, notes, llm, _ = project
    assert main(["run", str(notes), "--series", "alg"]) == 0
    assert "Run the same command again" in capsys.readouterr().out
    assert len(llm) == 1 and not (root / "series/alg/episodes").exists()
    assert main(["run", str(notes), "--series", "alg", "--yes"]) == 0
    assert "Using the existing" in capsys.readouterr().out
    assert len(llm) == 5  # the plan is not made again; 2 episodes x 2 passes
    assert mp3s(root) == ["01 - Ep 1.mp3", "02 - Ep 3.mp3"]


def test_run_auto_yes_goes_from_source_to_mp3s_in_one_go(project):
    root, notes, llm, tts = project
    assert main(["run", str(notes), "--series", "alg", "--auto", "--yes"]) == 0
    assert len(llm) == 5 and len(tts.calls) == 2
    assert mp3s(root) == ["01 - Ep 1.mp3", "02 - Ep 3.mp3"]


def test_run_auto_never_spends_without_yes_and_resumes_after(project, capsys):
    root, notes, llm, tts = project
    assert main(["run", str(notes), "--series", "alg", "--auto"]) == 1
    assert "pass --yes" in capsys.readouterr().err
    assert tts.calls == [] and mp3s(root) == []
    assert (root / "series/alg/episodes/02/script.txt").is_file()  # the writing is kept
    assert main(["run", str(notes), "--series", "alg", "--auto", "--yes"]) == 0
    assert len(llm) == 5  # nothing re-planned or re-written
    assert mp3s(root) == ["01 - Ep 1.mp3", "02 - Ep 3.mp3"]


def test_run_refuses_a_plan_made_for_another_source(project, tmp_path, capsys):
    _, notes, llm, _ = project
    main(["run", str(notes), "--series", "alg"])
    other = tmp_path / "other.md"
    other.write_text("x\n")
    assert main(["run", str(other), "--series", "alg", "--auto", "--yes"]) == 1
    assert "use another --series" in capsys.readouterr().err and len(llm) == 1


def test_run_checks_elevenlabs_settings_before_any_llm_call(
    project, monkeypatch, capsys
):
    _, notes, llm, _ = project
    monkeypatch.delenv("ELEVENLABS_VOICE_ID")
    assert main(["run", str(notes), "--series", "alg", "--auto", "--yes"]) == 1
    assert "ELEVENLABS_VOICE_ID" in capsys.readouterr().err and llm == []


def test_run_chapter_plans_that_bookmarks_pages(project, capsys):
    root, _, llm, _ = project
    book = toc_pdf(root / "book.pdf", 6, [[1, "Ch 1", 1], [1, "Ch 2", 5]])
    assert main(["run", str(book), "--series", "ch1", "--chapter", "1"]) == 0
    out = capsys.readouterr().out
    assert "Chapter 1: Ch 1, pages 1-4\nPlanning book.pdf pages 1-4 with" in out
    assert out.count("Chapter 1:") == 1
    assert "Run the same command again" in out and len(llm) == 1


def test_run_rejects_a_chapter_the_book_does_not_have(project, capsys):
    root, _, llm, _ = project
    book = toc_pdf(root / "book.pdf", 6, [[1, "Ch 1", 1], [1, "Ch 2", 5]])
    args = ["run", str(book), "--series", "ch3", "--chapter", "3", "--auto", "--yes"]
    assert main(args) == 1
    assert "book.pdf has chapters 1-2" in capsys.readouterr().err
    assert llm == [] and not (root / "series/ch3/plan.yaml").exists()


def test_run_refuses_a_plan_made_for_another_chapter(project, capsys):
    root, _, llm, tts = project
    book = toc_pdf(root / "book.pdf", 6, [[1, "Ch 1", 1], [1, "Ch 2", 5]])
    main(["run", str(book), "--series", "ch1", "--chapter", "1"])
    capsys.readouterr()
    for part, where in (["--chapter", "2"], "5-6"), (["--range", "5-5"], "5-5"):
        args = ["run", str(book), "--series", "ch1", *part, "--auto", "--yes"]
        assert main(args) == 1
        err = capsys.readouterr().err
        assert f"plan.yaml has no episode in pages {where}; use another --series" in err
    assert main(["run", str(book), "--series", "ch1", "--chapter", "3", "--yes"]) == 1
    assert "book.pdf has chapters 1-2" in capsys.readouterr().err
    assert len(llm) == 1 and tts.calls == []
    assert not (root / "series/ch1/episodes").exists()


def test_run_resumes_a_plan_for_the_chapter_asked_for(project, capsys):
    root, _, llm, _ = project
    book = toc_pdf(root / "book.pdf", 6, [[1, "Ch 1", 1], [1, "Ch 2", 5]])
    main(["run", str(book), "--series", "ch1", "--chapter", "1"])
    assert main(["run", str(book), "--series", "ch1", "--chapter", "1", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Chapter 1: Ch 1, pages 1-4\nUsing the existing" in out
    assert len(llm) == 5 and mp3s(root) == ["01 - Ep 1.mp3", "02 - Ep 3.mp3"]
    # a range that only touches the plan is the same part of the book, edited
    assert main(["run", str(book), "--series", "ch1", "--range", "4-6", "--yes"]) == 0
    assert main(["run", str(book), "--series", "ch1", "--yes"]) == 0  # nothing asked
    assert len(llm) == 5


# --- --continues ---------------------------------------------------------------------


def continues_of(root, series):
    return yaml.safe_load((root / f"series/{series}/plan.yaml").read_text()).get(
        "continues"
    )


def test_plan_continues_writes_the_other_series_into_the_plan(project):
    root, notes, _, _ = project
    assert main(["plan", str(notes), "--series", "ch3"]) == 0
    assert main(["plan", str(notes), "--series", "ch4", "--continues", "ch3"]) == 0
    assert continues_of(root, "ch4") == "ch3" and continues_of(root, "ch3") is None


@pytest.mark.parametrize("value", ["missing", "ch4", "../ch3", "ch3/x", ""])
@pytest.mark.parametrize("command", ["plan", "run"])
def test_continues_must_be_another_planned_series_before_any_llm_call(
    project, capsys, command, value
):
    root, notes, llm, _ = project
    main(["plan", str(notes), "--series", "ch3"])
    (root / "ch3").mkdir()  # what series/../ch3 would reach
    (root / "ch3/plan.yaml").write_text((root / "series/ch3/plan.yaml").read_text())
    args = [command, str(notes), "--series", "ch4", "--continues", value]
    assert main(args) == 1
    assert (
        f"--continues must name another series that has a plan.yaml, got {value!r}"
        in (capsys.readouterr().err)
    )
    assert len(llm) == 1 and not (root / "series/ch4").exists()


def test_a_series_cannot_continue_itself(project, capsys):
    root, notes, llm, _ = project
    main(["plan", str(notes), "--series", "ch4"])
    args = ["plan", str(notes), "--series", "ch4", "--continues", "ch4", "--force"]
    assert main(args) == 1
    assert "--continues must name another" in capsys.readouterr().err
    assert len(llm) == 1 and continues_of(root, "ch4") is None


def test_run_continues_opens_the_series_with_the_others_last_episode(project):
    root, notes, llm, _ = project
    assert main(["run", str(notes), "--series", "ch3", "--auto", "--yes"]) == 0
    del llm[:]
    args = ["run", str(notes), "--series", "ch4", "--continues", "ch3"]
    assert main(args) == 0  # stops after the plan
    assert continues_of(root, "ch4") == "ch3"
    assert main([*args, "--yes"]) == 0  # the same command again writes and renders
    _, draft1, _, draft2, _ = llm
    assert "(From the previous series, Algebra.) We met cosets." in draft1
    assert "previous series" not in draft2


def test_run_refuses_a_plan_that_does_not_continue_what_the_flag_says(project, capsys):
    root, notes, llm, _ = project
    main(["plan", str(notes), "--series", "ch3"])
    main(["plan", str(notes), "--series", "ch4"])
    args = ["run", str(notes), "--series", "ch4", "--continues", "ch3", "--auto", "--yes"]  # fmt: skip
    assert main(args) == 1
    assert "plan.yaml doesn't say `continues: ch3`" in capsys.readouterr().err
    assert len(llm) == 2 and not (root / "series/ch4/episodes").exists()


# --- a continued series: notes at plan time and after a rewrite (#93) ---------------------

NOT_YET = (
    "note: series ch3 (continued from) has no finished last episode; write it first. "
    "Until then the first episode of ch4 can't be written.\n"
)


@pytest.mark.parametrize("command", ["plan", "run"])
def test_continuing_a_series_that_is_not_written_yet_is_a_note_and_still_plans(
    project, capsys, command
):
    """Planning chapter 4 before chapter 3 is finished is a normal order of work."""
    root, notes, llm, _ = project
    main(["plan", str(notes), "--series", "ch3"])
    capsys.readouterr()
    assert main([command, str(notes), "--series", "ch4", "--continues", "ch3"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(NOT_YET) and out.count("note:") == 1
    assert continues_of(root, "ch4") == "ch3" and len(llm) == 2


def test_no_note_when_the_continued_series_is_written(project, capsys):
    _, notes, _, _ = project
    main(["plan", str(notes), "--series", "ch3"])
    main(["write", "ch3", "--all", "--auto"])
    capsys.readouterr()
    assert main(["plan", str(notes), "--series", "ch4", "--continues", "ch3"]) == 0
    assert "note:" not in capsys.readouterr().out


@pytest.mark.parametrize("command", ["plan", "run"])
def test_a_continued_series_whose_plan_does_not_load_stops_before_planning(
    project, capsys, command
):
    root, notes, llm, _ = project
    main(["plan", str(notes), "--series", "ch3"])
    (root / "series/ch3/plan.yaml").write_text("episodes: [", encoding="utf-8")
    assert main([command, str(notes), "--series", "ch4", "--continues", "ch3"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ") and "series/ch3/plan.yaml" in captured.err
    assert len(llm) == 1 and not (root / "series/ch4").exists()


def test_rewriting_a_series_last_episode_names_the_series_that_continues_it(
    project, capsys
):
    root, notes, _, _ = project
    main(["plan", str(notes), "--series", "ch3"])
    main(["write", "ch3", "--all", "--auto"])
    # A source named from the project folder, as the owner types it.
    main(["plan", "notes.md", "--series", "ch4", "--continues", "ch3"])
    main(["write", "ch4", "--episode", "1", "--auto"])
    capsys.readouterr()
    assert main(["write", "ch3", "--episode", "1", "--force", "--auto"]) == 0
    assert "series ch4" not in capsys.readouterr().out  # not ch3's last episode
    assert main(["write", "ch3", "--episode", "2", "--force", "--auto"]) == 0
    old = "note: episode 1 of series ch4 is built on the old summary and puzzle"
    assert (
        f"  {old}; rewrite it with --force if the recap matters\n"
        in capsys.readouterr().out
    )
    plan = (
        root / "series/ch4/plan.yaml"
    )  # ch4's first episode gets another range: stale
    plan.write_text(
        plan.read_text().replace("- 1\n  - 2", "- 1\n  - 1"), encoding="utf-8"
    )
    assert main(["write", "ch3", "--episode", "2", "--force", "--auto"]) == 0
    assert (
        f"  {old} and no longer matches the plan; write it again (no --force needed)\n"
        in capsys.readouterr().out
    )


def test_a_lost_summary_in_the_continued_series_is_a_note_at_plan_time(project, capsys):
    root, notes, _, _ = project
    main(["plan", str(notes), "--series", "ch3"])
    main(["write", "ch3", "--all", "--auto"])
    (root / "series/ch3/episodes/02/summary.md").unlink()
    capsys.readouterr()
    assert main(["plan", str(notes), "--series", "ch4", "--continues", "ch3"]) == 0
    assert capsys.readouterr().out.startswith(
        "note: episode 2 of series ch3 has a script but no summary.md; rewrite it: "
        "lecture-forge write ch3 --episode 2 --force. Until then the first episode of ch4 "
        "can't be written.\n"
    )
    assert continues_of(root, "ch4") == "ch3"
