import json
import subprocess

import pytest
import yaml

from lecture_forge import render as r
from lecture_forge.cli import main
from lecture_forge.plan import load_plan
from lecture_forge.render import (
    RenderError,
    char_limit,
    render_episode,
    safe_filename,
    split_text,
)
from lecture_forge.write import episode_dir


@pytest.fixture(scope="session")
def beep(tmp_path_factory):
    """A real 0.4 s MP3, as ElevenLabs would return for one piece."""
    p = tmp_path_factory.mktemp("audio") / "beep.mp3"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
         "-ar", "44100", "-b:a", "128k", str(p)], check=True,
    )  # fmt: skip
    return p.read_bytes()


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags",
         "-of", "json", str(path)], capture_output=True, text=True, check=True,
    )  # fmt: skip
    fmt = json.loads(out.stdout)["format"]
    return float(fmt["duration"]), {
        k.lower(): v for k, v in fmt.get("tags", {}).items()
    }


class FakeTTS:
    """Stands in for ElevenLabs: records what it was asked, returns real MP3 bytes."""

    def __init__(self, audio, fail_at=None):
        self.audio, self.fail_at, self.calls = audio, fail_at, []

    def __call__(self, text, previous_ids):
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            self.calls.append(("FAILED", text))
            raise RenderError("simulated outage")
        self.calls.append((text, list(previous_ids)))
        return self.audio, f"req-{len(self.calls)}"


PARA = (
    "This is one spoken paragraph about cosets and the clock with six hours on it. "
    * 10
)


@pytest.fixture
def series(tmp_path):
    """A plan with two written episodes and one unwritten."""
    src = tmp_path / "notes.md"
    src.write_text("x\n" * 30, encoding="utf-8")
    sdir = tmp_path / "series" / "algebra"
    sdir.mkdir(parents=True)
    episodes = [{"n": n, "title": t, "central_idea": "i", "lines": [n, n]}
                for n, t in [(1, "Cosets: Equal Pieces?"), (2, "Rings/Ideals"), (3, "Later")]]  # fmt: skip
    (sdir / "plan.yaml").write_text(
        yaml.safe_dump({"title": 'Algebra: "Groups" & Rings', "author": "A. Author",
                        "source": str(src), "episodes": episodes}), encoding="utf-8",
    )  # fmt: skip
    for n in (1, 2):
        d = episode_dir(sdir, n)
        d.mkdir(parents=True)
        (d / "script.txt").write_text("\n\n".join([PARA] * 8) + "\n", encoding="utf-8")
    return sdir


def settings(tmp_path, model="eleven_v3"):
    from lecture_forge.config import Settings

    return Settings(
        elevenlabs_api_key="k", voice_id="voice-1", model_id=model,
        style_guide_path=tmp_path / "g.md", output_dir=tmp_path / "Lectures",
        provider="claude-code", llm_model="", anthropic_api_key="", openai_api_key="",
    )  # fmt: skip


# --- splitting ---------------------------------------------------------------------


def test_char_limits_per_model():
    assert char_limit("eleven_v3") == 5000
    assert char_limit("eleven_multilingual_v2") == 10000
    assert char_limit("something_new") == 5000  # unknown: the conservative limit


def test_split_packs_whole_paragraphs_under_the_limit():
    paras = [
        (f"Paragraph {i}. " + "word " * 180).strip() for i in range(10)
    ]  # ~910 chars each
    pieces = split_text("\n\n".join(paras), 5000)
    assert all(len(p) <= 5000 for p in pieces) and len(pieces) == 2
    assert "\n\n".join(pieces) == "\n\n".join(paras)  # nothing lost, nothing reordered
    assert all(
        p.startswith("Paragraph") for p in pieces
    )  # cuts fall on paragraph breaks


def test_split_breaks_a_huge_paragraph_at_sentences_then_words():
    sentences = " ".join(
        f"Sentence number {i} keeps going for a while." for i in range(300)
    )
    giant_word_run = "a " * 4000  # one "sentence" longer than the limit
    for text in (sentences, giant_word_run.strip()):
        pieces = split_text(text, 1000)
        assert all(0 < len(p) <= 1000 for p in pieces)
        assert " ".join(pieces).split() == text.split()


def test_split_of_empty_script_is_an_error():
    with pytest.raises(RenderError, match="empty"):
        split_text("   \n\n ", 5000)


# --- filenames -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Cosets: Equal Pieces?", "Cosets - Equal Pieces"),
        ("Rings/Ideals\\Quotients", "Rings-Ideals-Quotients"),
        ('He said "why" <now>|*', "He said why now"),
        ("Trailing dots...  ", "Trailing dots"),
        ("CON", "_CON"),
        ("com1", "_com1"),
        ("\x00\x1f", "untitled"),
        ("x" * 300, "x" * 120),
    ],
)
def test_safe_filename_is_valid_on_windows(title, expected):
    assert safe_filename(title) == expected


# --- rendering an episode --------------------------------------------------------------


def run(series, tmp_path, tts, n=1, model="eleven_v3", **kw):
    plan = load_plan(series / "plan.yaml")
    ep = next(e for e in plan["episodes"] if e["n"] == n)
    return render_episode(plan, ep, series, settings(tmp_path, model), tts, **kw)


def test_episode_is_split_rendered_joined_and_tagged(series, tmp_path, beep):
    tts = FakeTTS(beep)
    result = run(series, tmp_path, tts)
    script = (episode_dir(series, 1) / "script.txt").read_text(encoding="utf-8")
    assert len(tts.calls) == len(split_text(script, 5000)) > 1
    assert all(prev == [] for _, prev in tts.calls)  # eleven_v3: no request stitching
    out = (
        tmp_path
        / "Lectures"
        / "Algebra - Groups & Rings"
        / "01 - Cosets - Equal Pieces.mp3"
    )
    assert result.path == out and out.is_file()
    duration, tags = probe(out)
    assert duration == pytest.approx(0.4 * len(tts.calls), abs=0.25)
    assert (
        tags["title"] == "Cosets: Equal Pieces?"
        and tags["album"] == 'Algebra: "Groups" & Rings'
    )
    assert tags["track"] == "1/3" and tags["artist"] == "A. Author"
    assert result.billed == sum(len(t) for t, _ in tts.calls) and result.reused == 0


def test_stitching_models_get_up_to_three_previous_request_ids(series, tmp_path, beep):
    script = episode_dir(series, 1) / "script.txt"
    # ~47k chars: 5 pieces at 10k. Distinct paragraphs, or the cache dedupes identical pieces.
    paras = [f"Paragraph {i}. {PARA}" for i in range(60)]
    script.write_text("\n\n".join(paras), encoding="utf-8")
    tts = FakeTTS(beep)
    run(series, tmp_path, tts, model="eleven_multilingual_v2")
    texts_and_prev = [prev for _, prev in tts.calls]
    assert texts_and_prev[0] == []
    assert texts_and_prev[1] == ["req-1"]
    assert texts_and_prev[4] == ["req-2", "req-3", "req-4"]  # only the 3 most recent
    assert all(len(p) <= 3 for p in texts_and_prev)


def test_failure_mid_episode_keeps_paid_pieces_and_resumes_without_paying_twice(
    series, tmp_path, beep
):
    failing = FakeTTS(beep, fail_at=1)
    with pytest.raises(RenderError, match="outage"):
        run(series, tmp_path, failing)
    assert not list((tmp_path / "Lectures").rglob("*.mp3"))  # no half-episode published
    resumed = FakeTTS(beep)
    result = run(series, tmp_path, resumed)
    first_piece = failing.calls[0][0]
    assert first_piece not in [t for t, _ in resumed.calls]  # piece 1 reused from cache
    assert result.reused == 1 and result.path.is_file()


def test_unchanged_episode_is_skipped_and_changed_script_rerenders(
    series, tmp_path, beep
):
    run(series, tmp_path, FakeTTS(beep))
    again = FakeTTS(beep)
    assert run(series, tmp_path, again).skipped and again.calls == []
    script = episode_dir(series, 1) / "script.txt"
    script.write_text(
        script.read_text(encoding="utf-8") + "\nOne more sentence.\n", encoding="utf-8"
    )
    changed = FakeTTS(beep)
    result = run(series, tmp_path, changed)
    assert (
        not result.skipped and len(changed.calls) == 1
    )  # only the edited last piece is paid for


def test_force_rerenders_but_still_reuses_identical_pieces(series, tmp_path, beep):
    run(series, tmp_path, FakeTTS(beep))
    again = FakeTTS(beep)
    result = run(series, tmp_path, again, force=True)
    assert not result.skipped and again.calls == [] and result.path.is_file()


def test_voice_change_invalidates_the_cache(series, tmp_path, beep):
    run(series, tmp_path, FakeTTS(beep))
    plan = load_plan(series / "plan.yaml")
    import dataclasses

    other = dataclasses.replace(settings(tmp_path), voice_id="voice-2")
    tts = FakeTTS(beep)
    render_episode(plan, plan["episodes"][0], series, other, tts)
    assert len(tts.calls) > 1


def test_unwritten_episode_cannot_be_rendered(series, tmp_path, beep):
    with pytest.raises(RenderError, match="write episode 3 first"):
        run(series, tmp_path, FakeTTS(beep), n=3)


def test_output_dir_is_never_invented_when_its_drive_is_missing(series, tmp_path, beep):
    import dataclasses

    plan = load_plan(series / "plan.yaml")
    s = dataclasses.replace(
        settings(tmp_path), output_dir=tmp_path / "no-drive" / "Dropbox" / "Lectures"
    )
    with pytest.raises(RenderError, match="no-drive"):
        render_episode(plan, plan["episodes"][0], series, s, FakeTTS(beep))
    assert not (tmp_path / "no-drive").exists()


def test_cost_counts_only_pieces_not_yet_paid_for(series, tmp_path, beep):
    plan = load_plan(series / "plan.yaml")
    s = settings(tmp_path)
    ep = plan["episodes"][0]
    full = r.cost(plan, ep, series, s)
    script = (episode_dir(series, 1) / "script.txt").read_text(encoding="utf-8").strip()
    assert full == sum(len(p) for p in split_text(script, 5000))
    with pytest.raises(RenderError):
        render_episode(plan, ep, series, s, FakeTTS(beep, fail_at=1))
    assert r.cost(plan, ep, series, s) == full - len(split_text(script, 5000)[0])


def test_cost_is_zero_for_a_rendered_episode_even_without_its_piece_cache(
    series, tmp_path, beep
):
    plan = load_plan(series / "plan.yaml")
    s, ep = settings(tmp_path), plan["episodes"][0]
    run(series, tmp_path, FakeTTS(beep))
    for f in (episode_dir(series, 1) / "audio").iterdir():
        f.unlink()  # the cache is gone, but the MP3 is current: nothing to pay for
    assert r.cost(plan, ep, series, s) == 0
    assert r.cost(plan, ep, series, s, force=True) > 0  # --force really would re-render


def _three_pieces(series):
    script = episode_dir(series, 1) / "script.txt"
    paras = [f"Paragraph {i}. {PARA}" for i in range(27)]  # 3 pieces at 10k, 6 at 5k
    script.write_text("\n\n".join(paras), encoding="utf-8")
    return script, paras


def test_editing_a_piece_rerenders_the_stitched_pieces_after_it(series, tmp_path, beep):
    script, paras = _three_pieces(series)
    run(series, tmp_path, FakeTTS(beep), model="eleven_multilingual_v2")
    script.write_text(
        "\n\n".join(["Edited. " + paras[0], *paras[1:]]), encoding="utf-8"
    )
    tts = FakeTTS(beep)
    result = run(series, tmp_path, tts, model="eleven_multilingual_v2")
    assert len(tts.calls) == 3 and result.reused == 0  # 2 and 3 continue from new 1


def test_unstitched_pieces_after_an_edit_are_still_reused(series, tmp_path, beep):
    script, paras = _three_pieces(series)
    run(series, tmp_path, FakeTTS(beep))
    script.write_text(
        "\n\n".join(["Edited. " + paras[0], *paras[1:]]), encoding="utf-8"
    )
    tts = FakeTTS(beep)
    assert run(series, tmp_path, tts).reused > 0 and len(tts.calls) == 1


def test_expired_or_missing_request_ids_are_never_stitched(series, tmp_path, beep):
    import os

    _three_pieces(series)
    with pytest.raises(RenderError):  # piece 1 is paid for, then an outage
        run(series, tmp_path, FakeTTS(beep, fail_at=1), model="eleven_multilingual_v2")
    (rid,) = (episode_dir(series, 1) / "audio").glob("*.rid")
    os.utime(rid, (0, 0))  # resumed long after: its id has expired
    tts = FakeTTS(beep)
    run(series, tmp_path, tts, model="eleven_multilingual_v2")
    assert tts.calls[0][1] == [] and tts.calls[1][1] == ["req-1"]


# --- ElevenLabs adapter: retries -----------------------------------------------------------


@pytest.mark.parametrize("status,calls", [(429, 3), (503, 3), (400, 1), (401, 1)])
def test_elevenlabs_errors_retry_only_when_transient(status, calls, monkeypatch):
    from elevenlabs.core.api_error import ApiError

    attempts = []

    def convert(**kw):
        attempts.append(kw)
        raise ApiError(status_code=status, body={"detail": "x"})

    tts = r.elevenlabs_tts(
        settings_obj(), convert=convert, sleep=lambda _: None, attempts=3
    )
    with pytest.raises(RenderError, match=str(status)):
        tts("hello", [])
    assert len(attempts) == calls
    assert attempts[0]["seed"] == r.SEED and attempts[0]["model_id"] == "eleven_v3"
    assert "previous_request_ids" not in attempts[0]  # v3 rejects stitching


def settings_obj():
    from pathlib import Path

    return settings(Path("/tmp"))


# --- CLI ---------------------------------------------------------------------------------


@pytest.fixture
def project(series, tmp_path, monkeypatch, beep):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-1")
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_v3")
    monkeypatch.setenv("LECTURE_FORGE_OUTPUT_DIR", str(tmp_path / "Lectures"))
    tts = FakeTTS(beep)
    monkeypatch.setattr(r, "elevenlabs_tts", lambda s, **kw: tts)
    monkeypatch.setattr(r, "characters_left", lambda s: 189403)
    return tmp_path, tts


def test_render_all_asks_first_shows_cost_and_skips_unwritten(
    project, capsys, monkeypatch
):
    root, _ = project
    monkeypatch.setattr(
        "builtins.input", lambda prompt: (print(prompt, end=""), "y")[1]
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert main(["render", "algebra", "--all"]) == 0
    out = capsys.readouterr().out
    assert "characters" in out and "189,403" in out and "Render?" in out
    assert "Episode 3" in out and "not written yet" in out
    assert len(list((root / "Lectures").rglob("*.mp3"))) == 2


def test_render_never_spends_without_a_yes(project, capsys, monkeypatch):
    _, tts = project
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert main(["render", "algebra", "--all"]) == 1
    assert tts.calls == []
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # no terminal and no --yes
    assert main(["render", "algebra", "--all"]) == 1
    assert "--yes" in capsys.readouterr().err and tts.calls == []


def test_render_yes_runs_unattended_and_reports_paths(project, capsys):
    _, tts = project
    assert main(["render", "algebra", "--episode", "2", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "02 - Rings-Ideals.mp3" in out and tts.calls


def test_render_more_than_left_on_plan_is_refused(project, capsys, monkeypatch):
    monkeypatch.setattr(r, "characters_left", lambda s: 100)
    assert main(["render", "algebra", "--all", "--yes"]) == 1
    assert "only 100 characters left" in capsys.readouterr().err


# --- integration: one real (paid) ElevenLabs call ---------------------------------------------


@pytest.mark.integration
@pytest.mark.paid  # ~70 ElevenLabs characters
def test_real_elevenlabs_returns_playable_mp3(tmp_path):
    from lecture_forge.config import load_settings

    tts = r.elevenlabs_tts(load_settings())
    audio, request_id = tts(
        "Here is a question that sounds like it should have a boring answer.", []
    )
    (tmp_path / "x.mp3").write_bytes(audio)
    duration, _ = probe(tmp_path / "x.mp3")
    assert 1.5 < duration < 15 and request_id


def test_renamed_episode_replaces_its_old_mp3_instead_of_duplicating_it(
    series, tmp_path, beep
):
    first = run(series, tmp_path, FakeTTS(beep))
    plan_path = series / "plan.yaml"
    data = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    data["episodes"][0]["title"] = "Cosets, Renamed"
    plan_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    second = run(series, tmp_path, FakeTTS(beep))
    assert second.path.name == "01 - Cosets, Renamed.mp3" and second.path.is_file()
    assert not first.path.exists()  # no duplicate episode 1 in the car's playlist
    assert sorted(p.name for p in second.path.parent.iterdir()) == [
        "01 - Cosets, Renamed.mp3"
    ]


def test_old_output_outside_the_lecture_folder_is_never_deleted(series, tmp_path, beep):
    run(series, tmp_path, FakeTTS(beep))
    marker = episode_dir(series, 1) / "render.json"
    keep = tmp_path / "precious.mp3"
    keep.write_bytes(b"not ours")
    state = json.loads(marker.read_text(encoding="utf-8"))
    state["output"] = str(keep)  # a tampered or stale marker pointing elsewhere
    marker.write_text(json.dumps(state), encoding="utf-8")
    run(series, tmp_path, FakeTTS(beep))
    assert keep.read_bytes() == b"not ours"


def test_cost_preview_creates_no_folders(series, tmp_path):
    """A declined render must not leave an empty album folder in Dropbox."""
    plan = load_plan(series / "plan.yaml")
    assert r.cost(plan, plan["episodes"][0], series, settings(tmp_path)) > 0
    assert not (tmp_path / "Lectures").exists()


def test_case_only_title_change_never_deletes_the_new_mp3(series, tmp_path, beep):
    """On a case-insensitive drive (the Dropbox D: drive) both names are one file."""
    first = run(series, tmp_path, FakeTTS(beep))
    plan_path = series / "plan.yaml"
    data = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    data["episodes"][0]["title"] = "COSETS: EQUAL PIECES?"
    plan_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    second = run(series, tmp_path, FakeTTS(beep))
    assert (
        second.path.name == "01 - COSETS - EQUAL PIECES.mp3" and second.path.is_file()
    )
    assert first.path.is_file()  # differs only in case: never deleted
