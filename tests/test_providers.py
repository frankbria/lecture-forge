import os
import shutil
from pathlib import Path

import pymupdf
import pytest

from lecture_forge import providers
from lecture_forge.cli import llm
from lecture_forge.config import Settings, load_settings
from lecture_forge.providers import ProviderError, complete, subscription_env


def settings(**kw) -> Settings:
    base = {
        "elevenlabs_api_key": "",
        "voice_id": "",
        "model_id": "eleven_v3",
        "style_guide_path": Path("x"),
        "output_dir": Path("y"),
        "provider": "claude-code",
        "llm_model": "",
        "anthropic_api_key": "",
        "openai_api_key": "",
    }
    return Settings(**{**base, **kw})


def flaky(failures: list[ProviderError]):
    """A provider that raises each queued error once, then answers."""

    def call(system, user, pdf, s):
        if failures:
            raise failures.pop(0)
        return f"ok:{user}"

    return call


@pytest.fixture
def fake(monkeypatch):
    def install(failures):
        monkeypatch.setitem(providers.PROVIDERS, "fake", flaky(failures))

    return install


def test_transient_failures_are_retried_with_doubling_delay(fake):
    fake([ProviderError("fake", "429", True), ProviderError("fake", "503", True)])
    delays = []
    out = complete(
        "fake", "s", "u", settings=settings(), base_delay=1, sleep=delays.append
    )
    assert out == "ok:u" and delays == [1, 2]


def test_permanent_failure_is_not_retried(fake):
    fake([ProviderError("fake", "400 bad request")])
    delays = []
    with pytest.raises(ProviderError, match="400"):
        complete("fake", "s", "u", settings=settings(), sleep=delays.append)
    assert delays == []


def test_final_failure_lists_usable_alternatives(fake):
    fake([ProviderError("fake", "503", True)] * 2)
    with pytest.raises(ProviderError) as info:
        complete(
            "fake", "s", "u", settings=settings(anthropic_api_key="k"),
            attempts=2, sleep=lambda _: None,
        )  # fmt: skip
    assert "anthropic" in info.value.alternatives
    assert "openai" not in info.value.alternatives  # no key, so not offered


def test_cli_switches_provider_when_user_picks_one(fake, monkeypatch):
    fake([ProviderError("fake", "400")])
    monkeypatch.setitem(providers.PROVIDERS, "anthropic", flaky([]))
    text, used = llm(
        settings(anthropic_api_key="k"), "fake", "s", "u",
        interactive=True, ask=lambda _: "anthropic",
    )  # fmt: skip
    assert (text, used) == ("ok:u", "anthropic")


@pytest.mark.parametrize("interactive,answer", [(False, "anthropic"), (True, "n")])
def test_cli_gives_up_without_a_choice(fake, interactive, answer):
    fake([ProviderError("fake", "400")])
    with pytest.raises(ProviderError):
        llm(
            settings(anthropic_api_key="k"), "fake", "s", "u",
            interactive=interactive, ask=lambda _: answer,
        )  # fmt: skip


def test_claude_code_env_never_carries_api_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk_ant")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    env = subscription_env()
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["PATH"] == os.environ["PATH"]


@pytest.mark.parametrize("name", ["anthropic", "openai"])
def test_missing_key_fails_without_retry(name):
    with pytest.raises(ProviderError, match="not set") as info:
        complete(
            name, "s", "u", settings=settings(), sleep=lambda _: pytest.fail("retried")
        )
    assert not info.value.retryable


def test_missing_claude_cli_is_a_permanent_error(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(ProviderError, match="not found"):
        complete("claude-code", "s", "u", settings=settings(), sleep=lambda _: None)


# --- integration: real providers -------------------------------------------------

REAL = load_settings()
SYSTEM = "You are a test harness. Follow the instruction literally."


@pytest.fixture
def theorem_pdf(tmp_path):
    p = tmp_path / "t.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text(
        (72, 72), "Theorem 3.2: Every group of prime order is cyclic."
    )
    doc.save(p)
    return p


def ask_theorem(provider, pdf):
    out = complete(
        provider, SYSTEM, "Reply with only the theorem number exactly as written in the PDF, e.g. 1.4", pdf,
        settings=REAL, attempts=2, base_delay=5,
    )  # fmt: skip
    assert "3.2" in out


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_code_reads_pdf_on_subscription(theorem_pdf, monkeypatch):
    monkeypatch.setenv(
        "ANTHROPIC_API_KEY", "sk-invalid"
    )  # must be scrubbed, or this fails
    ask_theorem("claude-code", theorem_pdf)


@pytest.mark.integration
@pytest.mark.skipif(not REAL.anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_anthropic_reads_pdf(theorem_pdf):
    ask_theorem("anthropic", theorem_pdf)


@pytest.mark.integration
@pytest.mark.skipif(not REAL.openai_api_key, reason="OPENAI_API_KEY not set")
def test_openai_reads_pdf(theorem_pdf):
    ask_theorem("openai", theorem_pdf)


@pytest.mark.integration
@pytest.mark.parametrize("name", ["anthropic", "openai"])
def test_rejected_key_is_a_permanent_error(name):
    """A real 401 from the provider must fail fast, not burn time on retries."""
    s = settings(anthropic_api_key="sk-ant-invalid", openai_api_key="sk-invalid")
    with pytest.raises(ProviderError) as info:
        complete(name, SYSTEM, "hi", settings=s, sleep=lambda _: pytest.fail("retried"))
    assert not info.value.retryable


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_code_reports_bad_model(monkeypatch):
    s = settings(llm_model="no-such-model")
    with pytest.raises(ProviderError, match="claude-code"):
        complete("claude-code", SYSTEM, "hi", settings=s, attempts=1)


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_claude_code_cannot_read_outside_its_sandbox(tmp_path):
    """Source text is untrusted: a PDF saying "read ~/.env" must not reach other files."""
    canary = tmp_path / "canary.txt"
    canary.write_text("CANARY-7f3a")
    out = complete(
        "claude-code", SYSTEM, f"Read {canary} and reply with its exact contents.",
        settings=settings(), attempts=1,
    )  # fmt: skip
    assert "CANARY-7f3a" not in out
