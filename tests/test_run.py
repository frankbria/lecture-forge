import pytest
from test_plan import episode, plan_json
from test_render import FakeTTS, beep  # noqa: F401  (beep is a fixture)
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
