from pathlib import Path

import pytest

from lecture_forge.config import ConfigError, load_settings

KEYS = [
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "ELEVENLABS_MODEL_ID",
    "STYLE_GUIDE_PATH",
    "LECTURE_FORGE_OUTPUT_DIR",
    "LECTURE_FORGE_PROVIDER",
    "LECTURE_FORGE_LLM_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)


def test_defaults_when_env_file_is_empty(tmp_path):
    s = load_settings(tmp_path / "missing.env")
    assert s.model_id == "eleven_v3"
    assert s.provider == "claude-code"
    assert s.style_guide_path == Path("prompts/style-guide.md")
    assert s.output_dir == Path("/mnt/d/Dropbox/Lectures")


def test_values_come_from_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ELEVENLABS_VOICE_ID=abc\nLECTURE_FORGE_PROVIDER=anthropic\n")
    s = load_settings(env)
    assert s.voice_id == "abc" and s.provider == "anthropic"


def test_require_names_the_missing_variable(tmp_path):
    s = load_settings(tmp_path / "missing.env")
    with pytest.raises(ConfigError, match="ELEVENLABS_API_KEY"):
        s.require("elevenlabs_api_key")


def test_unknown_provider_is_rejected(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LECTURE_FORGE_PROVIDER=gemini\n")
    with pytest.raises(ConfigError, match="gemini"):
        load_settings(env)


def test_api_keys_never_appear_in_repr(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "ELEVENLABS_API_KEY=sk_el\nANTHROPIC_API_KEY=sk_ant\nOPENAI_API_KEY=sk_oai\n"
    )
    r = repr(load_settings(env))
    assert not any(k in r for k in ("sk_el", "sk_ant", "sk_oai"))


def test_settings_are_found_under_the_project_root_not_the_working_directory(
    tmp_path, monkeypatch
):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text(
        "ELEVENLABS_VOICE_ID=from-root\nLECTURE_FORGE_OUTPUT_DIR=out\n"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("ELEVENLABS_VOICE_ID=wrong-folder\n")
    monkeypatch.chdir(elsewhere)  # e.g. a GUI server started from another folder
    s = load_settings(root=root)
    assert s.voice_id == "from-root"
    assert s.style_guide_path == root / "prompts" / "style-guide.md"  # the default
    assert s.output_dir == root / "out"  # a relative path in .env is under the root


def test_absolute_paths_and_the_environment_still_win_under_a_root(
    tmp_path, monkeypatch
):
    (tmp_path / ".env").write_text(f"STYLE_GUIDE_PATH={tmp_path / 'g.md'}\n")
    monkeypatch.setenv("LECTURE_FORGE_OUTPUT_DIR", "/abs/out")
    s = load_settings(root=tmp_path)
    assert s.style_guide_path == tmp_path / "g.md" and s.output_dir == Path("/abs/out")
