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
    assert "pieces reused)" in out  # the balance didn't move: no credits claimed


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


@pytest.mark.parametrize("torn", [b'{"script_sha', b"[]", b"\xff\xfe{\x00"])
def test_torn_render_marker_is_rebuilt_from_cache(series, tmp_path, beep, torn):
    """A crash mid-write (or a hand edit) must not block the episode forever."""
    run(series, tmp_path, FakeTTS(beep))
    marker = episode_dir(series, 1) / "render.json"
    marker.write_bytes(torn)  # last case: re-saved as UTF-16 by an editor
    result = run(series, tmp_path, FakeTTS(beep))
    assert not result.skipped and result.billed == 0 and result.path.is_file()
    assert json.loads(marker.read_text(encoding="utf-8"))["model"] == "eleven_v3"


def test_cost_preview_creates_no_folders(series, tmp_path):
    """A declined render must not leave an empty album folder in Dropbox."""
    plan = load_plan(series / "plan.yaml")
    assert r.cost(plan, plan["episodes"][0], series, settings(tmp_path)) > 0
    assert not (tmp_path / "Lectures").exists()


def _retitle(series, title):
    plan_path = series / "plan.yaml"
    data = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    data["episodes"][0]["title"] = title
    plan_path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_old_path_that_is_the_new_file_is_never_deleted(series, tmp_path, beep):
    """On the case-insensitive Dropbox drive, a case-only rename leaves old and new
    names pointing at one file. A symlink is the same situation on Linux."""
    first = run(series, tmp_path, FakeTTS(beep))
    _retitle(series, "COSETS: EQUAL PIECES?")
    new = first.path.with_name("01 - COSETS - EQUAL PIECES.mp3")
    first.path.unlink()
    first.path.symlink_to(new.name)  # the old name now resolves to the new file
    second = run(series, tmp_path, FakeTTS(beep))
    assert second.path == new and new.is_file()
    assert first.path.is_symlink()  # same file as the new MP3: not deleted


def test_case_only_rename_on_a_case_sensitive_drive_leaves_no_duplicate(
    series, tmp_path, beep
):
    first = run(series, tmp_path, FakeTTS(beep))
    _retitle(series, "COSETS: EQUAL PIECES?")
    second = run(series, tmp_path, FakeTTS(beep))
    assert second.path.is_file() and not first.path.exists()


def _stale(series):
    """Episode 1's script was written for line 2; the plan now says line 1."""
    meta = {"source": "x", "unit": "lines", "start": 2, "end": 2}
    (episode_dir(series, 1) / "written.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )


def test_script_written_for_another_range_is_never_rendered(series, tmp_path, beep):
    _stale(series)
    tts = FakeTTS(beep)
    with pytest.raises(RenderError, match="no longer matches") as e:
        run(series, tmp_path, tts)
    assert "written for lines 2-2, the plan now says lines 1-1" in str(e.value)
    assert str(e.value).endswith(
        "lecture-forge write algebra --episode 1"
    )  # no --force needed
    assert tts.calls == []


def test_render_all_skips_a_stale_script_and_spends_nothing_on_it(project, capsys):
    root, tts = project
    _stale(root / "series" / "algebra")
    assert main(["render", "algebra", "--all", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Episode 1: Cosets: Equal Pieces?: script no longer matches the plan" in out
    assert [p.name for p in (root / "Lectures").rglob("*.mp3")] == [
        "02 - Rings-Ideals.mp3"
    ]
    assert main(["render", "algebra", "--episode", "1", "--yes"]) == 1
    assert "no longer matches" in capsys.readouterr().err
    assert len(tts.calls) == 2  # episode 2's two pieces; episode 1 would add two more


def test_render_one_unwritten_episode_is_an_error(project, capsys):
    _, tts = project
    assert main(["render", "algebra", "--episode", "3", "--yes"]) == 1
    assert "write episode 3 first" in capsys.readouterr().err and tts.calls == []


# --- measured credits -------------------------------------------------------------------------


def readings(*values):
    """A balance reader returning `values` in order, recording each call."""
    calls, it = [], iter(values)
    return calls, lambda: calls.append(1) or next(it)


def marker(series, n=1):
    return json.loads(
        (episode_dir(series, n) / "render.json").read_text(encoding="utf-8")
    )


def test_render_records_the_credits_it_used_and_a_skip_reads_no_balance(
    series, tmp_path, beep
):
    calls, balance = readings(10000, 9560)
    result = run(series, tmp_path, FakeTTS(beep), balance=balance)
    assert result.credits == 440 and len(calls) == 2  # before and after, not per piece
    m = marker(series)
    assert m["chars_sent"] == result.billed and m["credits_used"] == 440
    calls, balance = readings()
    again = run(series, tmp_path, FakeTTS(beep), balance=balance)
    assert (
        again.skipped and calls == []
    )  # the measurement doesn't spoil the up-to-date check


# lagging, topped up, unreadable after, unreadable before
@pytest.mark.parametrize(
    "before,after", [(10000, 10000), (10000, 10200), (10000, None), (None, 9000)]
)
def test_a_balance_that_did_not_drop_records_no_measurement(
    series, tmp_path, beep, before, after
):
    result = run(series, tmp_path, FakeTTS(beep), balance=readings(before, after)[1])
    assert result.credits is None and "credits_used" not in marker(series)


def test_a_fully_cached_render_reads_no_balance(series, tmp_path, beep):
    run(series, tmp_path, FakeTTS(beep))
    calls, balance = readings()
    result = run(series, tmp_path, FakeTTS(beep), force=True, balance=balance)
    assert not result.skipped and result.billed == 0 and calls == []


def test_a_rebuild_keeps_the_measurement_of_the_same_model(series, tmp_path, beep):
    run(series, tmp_path, FakeTTS(beep), balance=readings(10000, 9560)[1])
    plan_path = series / "plan.yaml"
    data = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    data["episodes"][0]["title"] = "Cosets, Renamed"
    plan_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    run(series, tmp_path, FakeTTS(beep))  # from cache, nothing measured
    assert marker(series)["credits_used"] == 440
    run(series, tmp_path, FakeTTS(beep), model="eleven_multilingual_v2")
    assert "credits_used" not in marker(series)  # never credited to another model


def test_an_edit_adds_its_measurement_to_the_episodes_earlier_one(
    series, tmp_path, beep
):
    first = run(series, tmp_path, FakeTTS(beep), balance=readings(10000, 9560)[1])
    script = episode_dir(series, 1) / "script.txt"
    script.write_text(
        script.read_text(encoding="utf-8") + "\nOne more.\n", encoding="utf-8"
    )
    edit = run(series, tmp_path, FakeTTS(beep), balance=readings(9560, 9550)[1])
    assert edit.credits == 10  # this run's own cost
    m = marker(series)  # the big sample isn't replaced by the small one
    assert m["chars_sent"] == first.billed + edit.billed and m["credits_used"] == 450


def test_credit_rate_is_measured_per_series_and_model(tmp_path):
    def mark(n, text):
        d = episode_dir(tmp_path, n)
        d.mkdir(parents=True)
        (d / "render.json").write_text(text, encoding="utf-8")

    assert r.credit_rate(tmp_path, "eleven_v3") is None  # nothing rendered yet
    mark(1, json.dumps({"model": "eleven_v3", "chars_sent": 1000, "credits_used": 400}))
    mark(
        2, json.dumps({"model": "eleven_v3", "chars_sent": 3000, "credits_used": 1360})
    )
    mark(3, json.dumps({"model": "eleven_v3"}))  # rendered, never measured
    mark(4, '{"model": "eleven_v3", "chars_')  # torn
    mark(5, json.dumps({"model": "eleven_v4", "chars_sent": 10, "credits_used": 10}))
    mark(6, json.dumps({"model": "eleven_v3", "chars_sent": 9, "credits_used": -99}))
    mark(7, json.dumps({"model": "eleven_v3", "chars_sent": True, "credits_used": 1}))
    assert r.credit_rate(tmp_path, "eleven_v3") == pytest.approx(0.44)
    assert r.credit_rate(tmp_path, "eleven_multilingual_v2") is None


@pytest.mark.parametrize("measured", [True, False])
def test_render_preview_and_refusal_use_the_measured_rate(
    project, capsys, monkeypatch, measured
):
    root, tts = project
    sdir = root / "series" / "algebra"
    plan = load_plan(sdir / "plan.yaml")
    c2 = r.cost(plan, plan["episodes"][1], sdir, settings(root))
    if measured:  # episode 1 rendered earlier at 0.44 credits per character
        episode_dir(sdir, 1).joinpath("render.json").write_text(json.dumps(
            {"script_sha256": "x", "model": "eleven_v3", "voice": "voice-1",
             "output": "gone.mp3", "chars_sent": 1000, "credits_used": 440}), encoding="utf-8")  # fmt: skip
    left = round(c2 * 0.6)  # under the character count, over the measured estimate
    vals = iter([left, 50000, 49000])  # preview, then before and after episode 2
    monkeypatch.setattr(r, "characters_left", lambda s: next(vals))
    code = main(["render", "algebra", "--episode", "2", "--yes"])
    out, err = capsys.readouterr()
    if measured:
        assert code == 0 and tts.calls
        assert f"~{round(c2 * 0.44):,} credits" in out and "0.44/char, measured" in out
        assert f"{left:,} credits left" in out
        assert f"({c2:,} characters sent, 0 pieces reused, 1,000 credits)" in out
    else:
        assert code == 1 and tts.calls == []
        assert (
            f"{c2:,} characters" in out
            and "no render measured yet for eleven_v3" in out
        )
        assert (
            f"about {c2:,} credits" in err and f"only {left:,} characters left" in err
        )


def test_render_all_measures_each_episode_between_its_own_balance_reads(
    project, monkeypatch
):
    root, _ = project
    vals = iter([100000, 100000, 99000, 99000, 98500])  # preview, then per episode
    monkeypatch.setattr(r, "characters_left", lambda s: next(vals))
    assert main(["render", "algebra", "--all", "--yes"]) == 0
    sdir = root / "series" / "algebra"
    assert [marker(sdir, n)["credits_used"] for n in (1, 2)] == [1000, 500]


# --- failure paths on the money path (#28) ------------------------------------------------


def test_missing_ffmpeg_fails_before_any_paid_request(
    series, tmp_path, beep, monkeypatch
):
    monkeypatch.setenv("PATH", "")
    tts = FakeTTS(beep)
    with pytest.raises(RenderError, match="ffmpeg"):
        run(series, tmp_path, tts)
    assert tts.calls == []  # nothing paid for
    assert not (tmp_path / "Lectures").exists()  # no empty album folder in Dropbox


class CorruptAfter(FakeTTS):
    """Real audio for the first piece, then bytes that aren't audio (a corrupt piece)."""

    def __call__(self, text, previous_ids):
        audio, rid = super().__call__(text, previous_ids)
        return (audio if len(self.calls) == 1 else b"not audio at all"), rid


@pytest.mark.parametrize("corrupt", ["every piece", "after the first"])
def test_ffmpeg_failure_leaves_no_partial_or_published_file(
    series, tmp_path, beep, corrupt
):
    """ffmpeg exits 0 after a good piece and a bad one, writing only the good part: that
    truncated episode must never be published."""
    tts = (
        FakeTTS(b"not audio at all") if corrupt == "every piece" else CorruptAfter(beep)
    )
    with pytest.raises(RenderError, match="ffmpeg failed"):
        run(series, tmp_path, tts)
    assert len(tts.calls) > 1  # the episode really had a piece after the first
    out = tmp_path / "Lectures"
    assert not list(out.rglob("*.mp3")) and not list(out.rglob("*.part"))
    assert not (episode_dir(series, 1) / "render.json").exists()


def test_network_error_is_retried_then_succeeds():
    import httpx

    replies = [httpx.ConnectError("boom"), (b"x", "req-1")]

    def convert(**kw):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    delays = []
    tts = r.elevenlabs_tts(
        settings_obj(), convert=convert, sleep=delays.append, attempts=3
    )
    assert tts("hi", []) == (b"x", "req-1") and delays == [10]


def test_rendering_twice_spends_nothing_the_second_time(project, capsys):
    _, tts = project
    assert main(["render", "algebra", "--all", "--yes"]) == 0
    paid = len(tts.calls)
    capsys.readouterr()
    assert main(["render", "algebra", "--all", "--yes"]) == 0
    out = capsys.readouterr().out
    assert len(tts.calls) == paid and paid > 0
    assert "already rendered" in out and "0 characters to synthesize" in out


def test_a_cache_only_rebuild_without_ffmpeg_fails_without_paying(
    series, tmp_path, beep, monkeypatch
):
    run(series, tmp_path, FakeTTS(beep))  # every piece cached
    monkeypatch.setenv("PATH", "")
    tts = FakeTTS(beep)
    with pytest.raises(RenderError, match="ffmpeg is not installed"):
        run(series, tmp_path, tts, force=True)
    assert tts.calls == []


def test_render_without_ffmpeg_fails_before_asking_to_spend(
    project, capsys, monkeypatch
):
    _, tts = project
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("asked to spend"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert main(["render", "algebra", "--all"]) == 1
    out, err = capsys.readouterr()
    assert "ffmpeg is not installed" in err and tts.calls == []
    assert "characters to synthesize" in out  # the free cost preview still shows
