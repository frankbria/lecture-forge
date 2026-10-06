"""The guard in conftest.py: no test may use a real API key unless it is a paid test."""

import anthropic
import conftest
import openai
import pytest

from lecture_forge import render

REAL = "sk-pretend-this-is-the-real-key"


@pytest.fixture
def real_key(monkeypatch):
    """Pretend REAL is one of your configured keys, so this works on any machine (CI too)."""
    monkeypatch.setattr(conftest, "REAL_KEYS", conftest.REAL_KEYS | {REAL})


@pytest.mark.parametrize(
    "make",
    [
        lambda: render.ElevenLabs(api_key=REAL),
        lambda: anthropic.Anthropic(api_key=REAL),
        lambda: openai.OpenAI(api_key=REAL),
    ],
    ids=["elevenlabs", "anthropic", "openai"],
)
def test_real_keys_are_blocked_outside_paid_tests(real_key, make):
    with pytest.raises(conftest.BilledCallBlocked, match="paid"):
        make()


@pytest.mark.parametrize(
    "make",
    [
        lambda: render.ElevenLabs(),
        lambda: anthropic.Anthropic(),
        lambda: openai.OpenAI(),
    ],
    ids=["elevenlabs", "anthropic", "openai"],
)
def test_clients_without_an_explicit_key_are_blocked(make):
    """The SDKs fall back to environment variables, which may hold your real key."""
    with pytest.raises(conftest.BilledCallBlocked):
        make()


def test_fake_keys_still_work_for_free_error_path_tests():
    """Invalid keys can't spend anything; tests use them to check 401 handling."""
    assert anthropic.Anthropic(api_key="sk-ant-invalid")
    assert render.ElevenLabs(api_key="k")


def test_the_render_adapter_is_guarded_end_to_end(real_key):
    from lecture_forge.config import Settings

    s = Settings(
        elevenlabs_api_key=REAL, voice_id="v", model_id="eleven_v3",
        style_guide_path=None, output_dir=None, provider="claude-code",
        llm_model="", anthropic_api_key="", openai_api_key="",
    )  # fmt: skip
    with pytest.raises(conftest.BilledCallBlocked):
        render.elevenlabs_tts(s)
