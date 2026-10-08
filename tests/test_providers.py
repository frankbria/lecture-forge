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
    text, now = llm(
        settings(provider="fake", anthropic_api_key="k"), "s", "u",
        interactive=True, ask=lambda _: "anthropic",
    )  # fmt: skip
    assert (text, now.provider) == ("ok:u", "anthropic")


@pytest.mark.parametrize("interactive,answer", [(False, "anthropic"), (True, "n")])
def test_cli_gives_up_without_a_choice(fake, interactive, answer):
    fake([ProviderError("fake", "400")])
    with pytest.raises(ProviderError):
        llm(
            settings(provider="fake", anthropic_api_key="k"), "s", "u",
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
@pytest.mark.paid  # a few cents of API usage
@pytest.mark.skipif(not REAL.anthropic_api_key, reason="ANTHROPIC_API_KEY not set")
def test_anthropic_reads_pdf(theorem_pdf):
    ask_theorem("anthropic", theorem_pdf)


@pytest.mark.integration
@pytest.mark.paid  # a few cents of API usage
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


# --- claude-code wiring, against a fake `claude` executable on PATH ----------------

FAKE_CLAUDE = """#!{python}
import json, os, pathlib, sys, time
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
pathlib.Path(os.environ["FAKE_CLAUDE_LOG"]).write_text(json.dumps({{
    "argv": sys.argv[1:],
    "has_api_key": "ANTHROPIC_API_KEY" in os.environ,
    "stdin": sys.stdin.read(),
    "files": sorted(os.listdir(".")),
}}))
sys.stdout.write(os.environ["FAKE_CLAUDE_STDOUT"])
sys.stderr.write(os.environ.get("FAKE_CLAUDE_STDERR", ""))
sys.exit(int(os.environ.get("FAKE_CLAUDE_EXIT", "0")))
"""


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """Put a scripted `claude` first on PATH; returns (set_reply, read_log)."""
    import json
    import sys

    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(0o755)
    log = tmp_path / "claude-call.json"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-scrubbed")

    def reply(stdout, exit_code=0, stderr="", sleep=0):
        out = stdout if isinstance(stdout, str) else json.dumps(stdout)
        monkeypatch.setenv("FAKE_CLAUDE_STDOUT", out)
        monkeypatch.setenv("FAKE_CLAUDE_EXIT", str(exit_code))
        monkeypatch.setenv("FAKE_CLAUDE_STDERR", stderr)
        monkeypatch.setenv("FAKE_CLAUDE_SLEEP", str(sleep))

    return reply, lambda: json.loads(log.read_text())


def test_claude_code_invocation_is_isolated_and_billed_to_subscription(
    fake_claude, theorem_pdf
):
    reply, call = fake_claude
    reply({"is_error": False, "result": "the script"})
    out = complete(
        "claude-code",
        "SYS",
        "write it",
        theorem_pdf,
        settings=settings(llm_model="opus"),
    )
    c = call()
    assert out == "the script"
    assert c["has_api_key"] is False  # subscription, not the API key
    args = c["argv"]
    assert (
        args[args.index("--setting-sources") + 1] == ""
    )  # no user hooks/styles/CLAUDE.md
    assert args[args.index("--tools") + 1] == "Read"
    assert args[args.index("--system-prompt") + 1] == "SYS"
    assert args[args.index("--model") + 1] == "opus"
    assert (
        c["stdin"].startswith("write it") and "source.pdf" in c["stdin"]
    )  # prompt via stdin
    assert c["files"] == ["source.pdf"]  # only the sliced PDF is in its sandbox


def test_claude_code_without_pdf_or_model(fake_claude):
    reply, call = fake_claude
    reply({"is_error": False, "result": "ok"})
    assert complete("claude-code", "s", "u", settings=settings()) == "ok"
    c = call()
    assert "--model" not in c["argv"] and c["files"] == [] and c["stdin"] == "u"


@pytest.mark.parametrize(
    "status,retryable",
    [(408, True), (429, True), (529, True), (None, True), (400, False), (401, False)],
)
def test_claude_code_error_status_decides_retry(fake_claude, status, retryable):
    reply, _ = fake_claude
    reply({"is_error": True, "result": "API Error", "api_error_status": status})
    delays = []
    with pytest.raises(ProviderError, match="API Error") as info:
        complete(
            "claude-code",
            "s",
            "u",
            settings=settings(),
            base_delay=1,
            sleep=delays.append,
        )
    assert info.value.retryable is retryable
    assert delays == ([1, 2, 4] if retryable else [])


def test_claude_code_garbage_output_is_retryable_and_shows_stderr(fake_claude):
    reply, _ = fake_claude
    reply("not json", exit_code=1, stderr="boom: usage limit")
    with pytest.raises(ProviderError, match="exit 1: boom: usage limit") as info:
        complete("claude-code", "s", "u", settings=settings(), attempts=1)
    assert info.value.retryable


def test_claude_code_timeout_is_retryable(fake_claude, monkeypatch):
    reply, _ = fake_claude
    reply({"is_error": False, "result": "late"}, sleep=3)
    monkeypatch.setattr(providers, "TIMEOUT_S", 0.5)
    with pytest.raises(ProviderError, match="timed out") as info:
        complete("claude-code", "s", "u", settings=settings(), attempts=1)
    assert info.value.retryable


@pytest.mark.parametrize(
    "status,retryable",
    [
        (408, True),
        (409, True),
        (429, True),
        (503, True),
        (None, True),
        (400, False),
        (404, False),
    ],
)
def test_sdk_error_classification(status, retryable):
    class SDKError(Exception):
        pass

    e = SDKError("x")
    if status is not None:
        e.status_code = status
    assert providers._api_error("anthropic", e).retryable is retryable


# --- review fixes (codex on PR #4) ------------------------------------------------


def openai_response(*parts, status="completed"):
    from openai.types.responses import Response, ResponseOutputMessage

    msg = ResponseOutputMessage.model_construct(
        type="message", role="assistant", content=list(parts)
    )
    return Response.model_construct(
        status=status, output=[msg], incomplete_details=None
    )


def test_openai_refusal_is_a_permanent_error_not_an_empty_script():
    from openai.types.responses import ResponseOutputRefusal

    resp = openai_response(
        ResponseOutputRefusal.model_construct(type="refusal", refusal="no")
    )
    with pytest.raises(ProviderError, match="declined") as info:
        providers._openai_text(resp)
    assert not info.value.retryable


def test_openai_text_is_returned():
    from openai.types.responses import ResponseOutputText

    part = ResponseOutputText.model_construct(
        type="output_text", text="script", annotations=[]
    )
    assert providers._openai_text(openai_response(part)) == "script"


def test_empty_reply_from_any_provider_is_an_error(fake):
    fake([])
    providers.PROVIDERS["fake"] = lambda *a: "   "
    with pytest.raises(ProviderError, match="empty"):
        complete("fake", "s", "u", settings=settings(), sleep=lambda _: None)


def test_switching_provider_drops_the_old_providers_model(fake, monkeypatch):
    seen = []
    fake([ProviderError("fake", "400")])
    monkeypatch.setitem(
        providers.PROVIDERS,
        "openai",
        lambda s, u, p, st: seen.append(st.llm_model) or "ok",
    )
    text, now = llm(
        settings(provider="fake", llm_model="claude-opus-5-5", openai_api_key="k"), "s", "u",
        interactive=True, ask=lambda _: "openai",
    )  # fmt: skip
    assert (text, now.provider, seen) == ("ok", "openai", [""])
    # The next call (e.g. the next episode) carries the switch forward: no prompt, no override.
    text, later = llm(
        now, "s", "u2", interactive=True, ask=lambda _: pytest.fail("asked again")
    )
    assert (later.provider, later.llm_model, seen) == ("openai", "", ["", ""])


@pytest.mark.parametrize("reply_value", [[], {"is_error": False}, {"result": 7}])
def test_unexpected_claude_reply_is_a_provider_error(fake_claude, reply_value):
    reply, _ = fake_claude
    reply(reply_value)
    with pytest.raises(ProviderError, match="unexpected reply"):
        complete(
            "claude-code", "s", "u", settings=settings(),
            sleep=lambda _: pytest.fail("retried"),
        )  # fmt: skip
